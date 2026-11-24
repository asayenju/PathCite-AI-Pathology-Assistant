import pytest
import timm
import torch
from fastapi.testclient import TestClient

from app.classes import CLASS_NAMES
from app.config import DEFAULT_MODEL_PATH, Settings
from app.model import ModelLoadError, ModelMissingError, load_model, pick_device


def test_defaults(monkeypatch):
    for k in ("MODEL_PATH", "UNCERTAIN_THRESHOLD", "MAX_UPLOAD_MB"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.model_path == DEFAULT_MODEL_PATH
    assert s.uncertain_threshold == 0.7
    assert s.max_upload_mb == 10


def test_env_override(monkeypatch):
    monkeypatch.setenv("UNCERTAIN_THRESHOLD", "0.5")
    monkeypatch.setenv("MAX_UPLOAD_MB", "3")
    s = Settings(_env_file=None)
    assert s.uncertain_threshold == 0.5 and s.max_upload_mb == 3


def test_pick_device_is_valid():
    assert pick_device().type in {"cuda", "mps", "cpu"}


def test_missing_weights(tmp_path):
    with pytest.raises(ModelLoadError, match="not found"):
        load_model(tmp_path / "nope.pt", torch.device("cpu"))


def test_mismatched_weights(tmp_path):
    p = tmp_path / "bad.pt"
    torch.save(timm.create_model("resnet18", pretrained=False, num_classes=5).state_dict(), p)
    with pytest.raises(ModelLoadError, match="Could not load"):
        load_model(p, torch.device("cpu"))


def test_garbage_file(tmp_path):
    p = tmp_path / "junk.pt"
    p.write_bytes(b"not a checkpoint")
    with pytest.raises(ModelLoadError):
        load_model(p, torch.device("cpu"))


def test_real_weights_load():
    model = load_model(DEFAULT_MODEL_PATH, torch.device("cpu"))
    assert not model.training
    with torch.inference_mode():
        out = model(torch.zeros(1, 3, 224, 224))
    assert out.shape == (1, len(CLASS_NAMES))


def test_health_with_model():
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model_loaded": True}


def test_missing_weights_raises_missing_error(tmp_path):
    with pytest.raises(ModelMissingError):
        load_model(tmp_path / "missing.pt")


def test_startup_without_weights_runs_degraded(monkeypatch, tmp_path, caplog):
    from app.main import app

    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing.pt"))
    with TestClient(app) as c:
        health = c.get("/health")
        predict = c.post("/predict", files={"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert health.status_code == 200
    assert health.json() == {"status": "degraded", "model_loaded": False}
    assert predict.status_code == 503
    assert predict.json()["code"] == "model_unavailable"
    assert "Model weights not found" in caplog.text


def test_startup_fails_fast_when_weights_mismatched(monkeypatch, tmp_path):
    from app.main import app

    bad = tmp_path / "bad.pt"
    torch.save(timm.create_model("resnet18", pretrained=False, num_classes=3).state_dict(), bad)
    monkeypatch.setenv("MODEL_PATH", str(bad))
    with pytest.raises(ModelLoadError) as exc:
        with TestClient(app):
            pass
    assert not isinstance(exc.value, ModelMissingError)


def test_health_degraded_when_model_not_loaded():
    from app.main import app

    app.state.model = None
    r = TestClient(app).get("/health")  # no lifespan: model never loaded
    assert r.status_code == 200
    assert r.json() == {"status": "degraded", "model_loaded": False}
