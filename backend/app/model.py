from pathlib import Path

import timm
import torch

from app.classes import CLASS_NAMES


class ModelLoadError(RuntimeError):
    pass


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_model(path: Path, device: torch.device | None = None) -> torch.nn.Module:
    """Load the fine-tuned timm resnet18 (9 classes). Raises ModelLoadError on any problem."""
    path = Path(path)
    if not path.is_file():
        raise ModelLoadError(f"Model weights not found at {path}")
    device = device or pick_device()
    model = timm.create_model("resnet18", pretrained=False, num_classes=len(CLASS_NAMES))
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
    except Exception as e:  # corrupt file, wrong keys, wrong shapes
        raise ModelLoadError(f"Could not load weights from {path}: {e}") from e
    return model.to(device).eval()
