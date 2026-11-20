import torch
from PIL import Image
from torchvision import transforms as T

from app.classes import CLASS_NAMES
from app.schemas import PredictResponse

INPUT_SIZE = 224
SIZE_TOLERANCE = 0.10  # inputs within 10% of 224px count as "about 224"

# Identical to the eval transform in model/CancerCheckResnetFinetune.ipynb.
MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
eval_transform = T.Compose([T.Resize((INPUT_SIZE, INPUT_SIZE)), T.ToTensor(), T.Normalize(MEAN, STD)])


def predict_probs(model: torch.nn.Module, img: Image.Image, device: torch.device) -> list[float]:
    """Softmax probabilities for one RGB image, in CLASS_NAMES order."""
    x = eval_transform(img).unsqueeze(0).to(device)
    with torch.inference_mode():
        logits = model(x)
    return torch.softmax(logits.float(), dim=1)[0].cpu().tolist()


def size_warnings(width: int, height: int) -> list[str]:
    warnings = []
    if width != height:
        warnings.append(
            f"Image is not square ({width}x{height}); resizing to {INPUT_SIZE}x{INPUT_SIZE} distorts it."
        )
    low, high = INPUT_SIZE * (1 - SIZE_TOLERANCE), INPUT_SIZE * (1 + SIZE_TOLERANCE)
    if not (low <= width <= high and low <= height <= high):
        warnings.append(
            f"Model was trained on {INPUT_SIZE}x{INPUT_SIZE} patches; input is {width}x{height}. "
            "A resized larger image (e.g. a whole slide) is not a valid patch."
        )
    return warnings


def build_response(
    probs: list[float], *, threshold: float, width: int, height: int, model_version: str
) -> PredictResponse:
    top = max(range(len(probs)), key=probs.__getitem__)
    confidence = probs[top]
    return PredictResponse(
        label=top,
        tissue=CLASS_NAMES[top],
        confidence=confidence,
        uncertain=confidence < threshold,
        probabilities=dict(zip(CLASS_NAMES, probs)),
        warnings=size_warnings(width, height),
        model_version=model_version,
    )
