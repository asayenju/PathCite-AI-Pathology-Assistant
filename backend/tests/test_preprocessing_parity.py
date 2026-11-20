"""The API must preprocess exactly like model/CancerCheckResnetFinetune.ipynb.

The notebook's own transform source is extracted and executed here, so a change to
either side that breaks parity fails this test.
"""
import json
import re

import numpy as np
import pytest
import torch
from PIL import Image
from torchvision import transforms as T

from app.config import DEFAULT_MODEL_PATH
from app.model import load_model
from app.predict import eval_transform, predict_probs

NOTEBOOK = DEFAULT_MODEL_PATH.parent / "CancerCheckResnetFinetune.ipynb"
CPU = torch.device("cpu")


def notebook_eval_transform():
    cells = json.loads(NOTEBOOK.read_text())["cells"]
    source = "\n".join("".join(c["source"]) for c in cells if c["cell_type"] == "code")
    mean_std = re.search(r"^MEAN, STD = .+$", source, re.M).group(0)
    eval_tf = re.search(r"^eval_tf = .+$", source, re.M).group(0)
    scope = {"T": T}
    exec(mean_std, scope)  # noqa: S102 - our own repository's notebook
    exec(eval_tf, scope)  # noqa: S102
    return scope["eval_tf"]


def sample_images():
    rng = np.random.default_rng(0)
    for size in [(224, 224), (150, 150), (300, 200), (512, 512)]:
        arr = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
        yield Image.fromarray(arr, "RGB")


@pytest.fixture(scope="module")
def model():
    return load_model(DEFAULT_MODEL_PATH, CPU)


def test_transform_matches_notebook_exactly():
    nb = notebook_eval_transform()
    for img in sample_images():
        assert torch.equal(nb(img), eval_transform(img))


def test_logits_match_notebook_pipeline(model):
    nb = notebook_eval_transform()
    for img in sample_images():
        with torch.no_grad():
            expected = torch.softmax(model(nb(img).unsqueeze(0)), dim=1)[0]
        got = torch.tensor(predict_probs(model, img, CPU))
        assert torch.allclose(expected, got, atol=1e-5)


def test_model_in_eval_mode_and_inference_does_not_track_grad(model):
    assert not model.training
    assert not any(m.training for m in model.modules())  # BatchNorm must use running stats
    x = eval_transform(next(sample_images())).unsqueeze(0)
    with torch.inference_mode():
        out = model(x)
    assert not out.requires_grad


def test_api_device_matches_cpu_reference_within_tolerance(model):
    """The server may run on mps/cuda; results must stay close to the cpu reference."""
    from app.model import pick_device

    device = pick_device()
    dev_model = load_model(DEFAULT_MODEL_PATH, device)
    for img in sample_images():
        ref = np.array(predict_probs(model, img, CPU))
        got = np.array(predict_probs(dev_model, img, device))
        assert np.abs(ref - got).max() < 1e-3
        assert ref.argmax() == got.argmax() or np.sort(ref)[-1] - np.sort(ref)[-2] < 1e-3
