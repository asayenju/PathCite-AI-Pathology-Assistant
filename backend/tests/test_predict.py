import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.classes import CLASS_NAMES
from app.main import app
from app.predict import build_response, size_warnings


def png(size=(224, 224), color=(180, 90, 160)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client():  # function-scoped: each test gets a freshly started app and loaded model
    with TestClient(app) as c:
        yield c


def post(client, data, name="a.png", ctype="image/png"):
    return client.post("/predict", files={"file": (name, data, ctype)})


def test_predict_shape_and_consistency(client):
    r = post(client, png())
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "label", "tissue", "confidence", "uncertain", "probabilities", "warnings", "model_version", "notice",
    }
    probs = body["probabilities"]
    assert list(probs) == list(CLASS_NAMES)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-4)
    assert body["confidence"] == pytest.approx(max(probs.values()))
    assert body["tissue"] == CLASS_NAMES[body["label"]]
    assert probs[body["tissue"]] == body["confidence"]
    assert body["model_version"].startswith("resnet18_finetuned_v2.pt@")
    assert "not a diagnosis" in body["notice"]


def test_224_square_has_no_warnings(client):
    assert post(client, png((224, 224))).json()["warnings"] == []


def test_150px_warns(client):
    warnings = post(client, png((150, 150))).json()["warnings"]
    assert len(warnings) == 1 and "150x150" in warnings[0]


def test_non_square_warns_twice_when_far_from_224(client):
    assert len(post(client, png((600, 300))).json()["warnings"]) == 2


def test_uncertain_follows_threshold(client, monkeypatch):
    monkeypatch.setenv("UNCERTAIN_THRESHOLD", "1.01")
    with TestClient(app) as c:
        assert post(c, png()).json()["uncertain"] is True
    monkeypatch.setenv("UNCERTAIN_THRESHOLD", "0")
    with TestClient(app) as c:
        body = post(c, png()).json()
    assert body["uncertain"] is False and body["label"] in range(9)  # never rejected


def test_uncertain_boundary_is_strictly_below():
    probs = [0.7, 0.3] + [0.0] * 7
    kw = dict(width=224, height=224, model_version="v")
    assert build_response(probs, threshold=0.7, **kw).uncertain is False
    assert build_response(probs, threshold=0.7000001, **kw).uncertain is True


def test_size_warning_tolerance():
    assert size_warnings(224, 224) == []
    assert size_warnings(205, 205) == []
    assert size_warnings(246, 246) == []
    assert len(size_warnings(180, 180)) == 1


def test_bad_inputs_return_json_errors(client):
    r = post(client, b"hello", "a.txt", "text/plain")
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type"
    r = post(client, b"")
    assert r.status_code == 422 and r.json()["code"] == "empty_file"
    r = client.post("/predict")
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"


def test_503_when_model_missing():
    app.state.model = None
    r = TestClient(app).post("/predict", files={"file": ("a.png", png(), "image/png")})
    assert r.status_code == 503 and r.json()["code"] == "model_unavailable"


def test_openapi_documents_disclaimer(client):
    desc = client.get("/openapi.json").json()["paths"]["/predict"]["post"]["description"]
    assert "not a diagnosis" in desc
