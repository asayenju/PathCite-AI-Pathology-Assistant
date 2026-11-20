"""End-to-end API tests with synthetic images (no network): edge-case inputs,
concurrency, privacy, and error handling."""
import io
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main
from app.main import app


def encode(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def noise(size, mode="RGB", seed=0):
    rng = np.random.default_rng(seed)
    channels = {"RGB": 3, "L": None, "RGBA": 4}[mode]
    shape = (size[1], size[0]) if channels is None else (size[1], size[0], channels)
    return Image.fromarray(rng.integers(0, 256, shape, dtype=np.uint8), mode)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def post(client, data, name="a.png", ctype="image/png"):
    return client.post("/predict", files={"file": (name, data, ctype)})


# ---- every accepted format / colour mode returns a valid prediction ----


@pytest.mark.parametrize(
    "img,fmt",
    [
        (Image.new("RGB", (224, 224), (150, 60, 140)), "PNG"),
        (Image.new("RGB", (224, 224), (150, 60, 140)), "JPEG"),
        (Image.new("RGB", (224, 224), (150, 60, 140)), "TIFF"),
        (Image.new("CMYK", (224, 224), (0, 200, 0, 0)), "JPEG"),
        (Image.new("CMYK", (224, 224), (0, 200, 0, 0)), "TIFF"),
        (Image.new("RGBA", (224, 224), (150, 60, 140, 128)), "PNG"),
        (Image.new("L", (224, 224), 128), "PNG"),
        (Image.new("P", (224, 224), 3), "PNG"),
        (Image.new("I;16", (224, 224), 30000), "PNG"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.mode,
)
def test_all_formats_and_modes_predict(client, img, fmt):
    r = post(client, encode(img, fmt))
    assert r.status_code == 200
    assert sum(r.json()["probabilities"].values()) == pytest.approx(1.0, abs=1e-4)


def test_large_image_predicts_with_warning(client):
    r = post(client, encode(noise((3000, 2000)), "JPEG"))
    assert r.status_code == 200
    assert len(r.json()["warnings"]) == 2  # not square, and not ~224


def test_tiny_image_predicts_with_warning(client):
    r = post(client, encode(Image.new("RGB", (1, 1), (200, 100, 150)), "PNG"))
    assert r.status_code == 200 and r.json()["warnings"]


# ---- bad inputs through the real app ----


def test_png_header_with_garbage_is_422(client):
    r = post(client, b"\x89PNG\r\n\x1a\n" + b"garbage" * 50)
    assert r.status_code == 422 and r.json()["code"] == "corrupt_image"


def test_oversize_upload_is_413(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    with TestClient(app) as c:
        r = post(c, b"\x00" * (1024 * 1024 + 10))
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"


def test_pixel_bomb_is_422(client, monkeypatch):
    monkeypatch.setenv("MAX_IMAGE_PIXELS", "1000000")
    data = encode(Image.new("L", (3000, 3000)), "PNG")  # tiny file, 9M pixels
    assert len(data) < 100_000
    with TestClient(app) as c:
        r = post(c, data)
    assert r.status_code == 422 and r.json()["code"] == "image_too_large"


def test_wrong_field_name_is_422(client):
    r = client.post("/predict", files={"image": ("a.png", encode(noise((8, 8)), "PNG"), "image/png")})
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"


# ---- concurrency: inference must not block the event loop ----


def test_health_stays_responsive_during_slow_predict(client, monkeypatch):
    real = main.predict_probs

    def slow(*a, **kw):
        time.sleep(1.5)
        return real(*a, **kw)

    monkeypatch.setattr(main, "predict_probs", slow)
    result = {}
    t = threading.Thread(target=lambda: result.update(r=post(client, encode(noise((224, 224)), "PNG"))))
    t.start()
    time.sleep(0.3)  # let /predict enter its slow inference
    start = time.perf_counter()
    health = client.get("/health")
    elapsed = time.perf_counter() - start
    t.join()
    assert health.status_code == 200
    assert elapsed < 0.75, f"/health blocked for {elapsed:.2f}s while /predict ran"
    assert result["r"].status_code == 200


def test_concurrent_predicts_all_succeed(client):
    data = encode(noise((224, 224)), "PNG")
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(post(client, data).status_code)) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes == [200] * 8


# ---- privacy: uploads stay in memory ----


def test_large_upload_never_spooled_to_disk(client, monkeypatch):
    seen = {}
    real = main.read_upload

    async def spy(file, max_bytes):
        seen["rolled"] = file.file._rolled  # SpooledTemporaryFile: True once written to disk
        return await real(file, max_bytes)

    monkeypatch.setattr(main, "read_upload", spy)
    data = encode(noise((800, 800)), "PNG")
    assert len(data) > 1024 * 1024  # larger than Starlette's default 1 MB spool limit
    assert post(client, data).status_code == 200
    assert seen["rolled"] is False


# ---- unexpected errors: JSON 500, no leakage ----


def test_unexpected_error_is_json_500_without_details(monkeypatch, caplog):
    def boom(*a, **kw):
        raise RuntimeError("SECRET-INTERNAL-DETAIL")

    monkeypatch.setattr(main, "predict_probs", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = post(c, encode(noise((224, 224)), "PNG"))
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error", "code": "internal_error"}
    assert "SECRET-INTERNAL-DETAIL" not in r.text
    assert "Traceback" not in r.text
    assert "SECRET-INTERNAL-DETAIL" not in caplog.text  # only the exception type is logged
