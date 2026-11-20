import io

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from PIL import Image

from app.errors import ApiError, register_error_handlers
from app.images import load_image, read_upload

MB = 1024 * 1024
KW = dict(max_bytes=10 * MB, max_pixels=1_000_000)


def encode(img, fmt, **kw):
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def code_of(data, **kw):
    with pytest.raises(ApiError) as e:
        load_image(data, **{**KW, **kw})
    return e.value.status_code, e.value.code


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "TIFF"])
def test_accepts_formats(fmt):
    img = load_image(encode(Image.new("RGB", (64, 64), (200, 50, 50)), fmt), **KW)
    assert img.mode == "RGB" and img.size == (64, 64)


@pytest.mark.parametrize(
    "mode,fmt",
    [("L", "PNG"), ("P", "PNG"), ("RGBA", "PNG"), ("LA", "PNG"), ("CMYK", "JPEG"), ("CMYK", "TIFF"),
     ("1", "PNG"), ("I;16", "PNG")],
)
def test_converts_modes_to_rgb(mode, fmt):
    img = load_image(encode(Image.new(mode, (16, 16)), fmt), **KW)
    assert img.mode == "RGB"


def test_alpha_flattened_onto_white():
    clear = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    assert load_image(encode(clear, "PNG"), **KW).getpixel((0, 0)) == (255, 255, 255)
    opaque = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
    assert load_image(encode(opaque, "PNG"), **KW).getpixel((0, 0)) == (0, 0, 0)


def test_palette_transparency_flattened():
    p = Image.new("P", (4, 4), 0)
    p.putpalette([0, 0, 0] * 256)
    data = encode(p, "PNG", transparency=0)
    assert load_image(data, **KW).getpixel((0, 0)) == (255, 255, 255)


def test_sixteen_bit_scaled_not_clipped():
    white = Image.new("I;16", (4, 4), 65535)
    assert load_image(encode(white, "PNG"), **KW).getpixel((0, 0)) == (255, 255, 255)
    mid = Image.new("I;16", (4, 4), 32768)
    assert load_image(encode(mid, "PNG"), **KW).getpixel((0, 0)) == (128, 128, 128)


def test_multiframe_tiff_uses_first_frame():
    a, b = Image.new("RGB", (10, 10), (255, 0, 0)), Image.new("RGB", (30, 30), (0, 0, 255))
    data = encode(a, "TIFF", save_all=True, append_images=[b])
    img = load_image(data, **KW)
    assert img.size == (10, 10) and img.getpixel((0, 0)) == (255, 0, 0)


def test_exif_orientation_applied():
    src = Image.new("RGB", (40, 20), (255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW to display
    img = load_image(encode(src, "JPEG", exif=exif), **KW)
    assert img.size == (20, 40)


def test_empty_file():
    assert code_of(b"") == (422, "empty_file")


def test_unsupported_type():
    assert code_of(b"%PDF-1.4 hello") == (415, "unsupported_type")
    assert code_of(encode(Image.new("RGB", (8, 8)), "GIF")) == (415, "unsupported_type")
    assert code_of(encode(Image.new("RGB", (8, 8)), "BMP")) == (415, "unsupported_type")


def test_truncated_image_is_corrupt():
    data = encode(Image.effect_noise((200, 200), 80).convert("RGB"), "PNG")
    assert code_of(data[: len(data) // 2]) == (422, "corrupt_image")


def test_oversize_bytes():
    assert code_of(b"x" * 101, max_bytes=100) == (413, "file_too_large")


def test_pixel_cap_rejects_bomb_without_decoding():
    big = Image.new("L", (4000, 4000))  # 16M px, compresses to a tiny PNG
    data = encode(big, "PNG")
    assert len(data) < MB
    assert code_of(data) == (422, "image_too_large")


def test_pixel_cap_boundary():
    assert load_image(encode(Image.new("RGB", (1000, 1000)), "PNG"), **KW).size == (1000, 1000)
    assert code_of(encode(Image.new("RGB", (1001, 1000)), "PNG")) == (422, "image_too_large")


# ---- HTTP level: JSON {detail, code} bodies and the upload size limit ----


def make_client(max_bytes=10 * MB):
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/t")
    async def t(file: UploadFile):
        data = await read_upload(file, max_bytes)
        img = load_image(data, max_bytes=max_bytes, max_pixels=1_000_000)
        return {"size": img.size}

    return TestClient(app)


def test_http_ok():
    png = encode(Image.new("RGB", (5, 7)), "PNG")
    r = make_client().post("/t", files={"file": ("a.png", png, "image/png")})
    assert r.status_code == 200 and r.json() == {"size": [5, 7]}


def test_http_errors_are_json_with_code():
    r = make_client(max_bytes=100).post("/t", files={"file": ("a.png", b"x" * 500, "image/png")})
    assert r.status_code == 413 and r.json()["code"] == "file_too_large" and "detail" in r.json()
    r = make_client().post("/t", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type"
    r = make_client().post("/t", files={"file": ("a.png", b"", "image/png")})
    assert r.status_code == 422 and r.json()["code"] == "empty_file"


def test_http_missing_file_field():
    r = make_client().post("/t")
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_request" and "file" in r.json()["detail"]
