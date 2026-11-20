import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.formparsers import MultiPartParser

from app.config import get_settings
from app.errors import ApiError, register_error_handlers
from app.images import load_image, read_upload
from app.model import load_model, pick_device
from app.predict import build_response, predict_probs
from app.schemas import PredictResponse


def _model_version(path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return f"{path.name}@{digest}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Starlette spools uploads over 1 MB to a temp file on disk by default. Keep the
    # whole (size-capped) upload in memory so patient images are never written to disk.
    MultiPartParser.spool_max_size = settings.max_upload_mb * 1024 * 1024 + 1
    device = pick_device()
    app.state.settings = settings
    app.state.device = device
    # Fails fast: a missing or mismatched weights file stops startup with a clear error.
    app.state.model = load_model(settings.model_path, device)
    app.state.model_version = _model_version(settings.model_path)
    yield
    app.state.model = None


app = FastAPI(title="PathCite API", lifespan=lifespan)
register_error_handlers(app)


@app.get("/health")
def health():
    loaded = getattr(app.state, "model", None) is not None
    body = {"status": "ok" if loaded else "unavailable", "model_loaded": loaded}
    return JSONResponse(body, status_code=200 if loaded else 503)


@app.post(
    "/predict",
    response_model=PredictResponse,
    summary="Classify the tissue type of one H&E patch",
    description=(
        "Upload one PNG, JPEG or TIFF image of a colorectal H&E patch. Returns the top tissue class, "
        "its confidence and all class probabilities. Research use only: not a medical device and not "
        "a diagnosis. Confidence is an uncalibrated model score."
    ),
)
async def predict(request: Request, file: UploadFile) -> PredictResponse:
    state = request.app.state
    model = getattr(state, "model", None)
    if model is None:
        raise ApiError(503, "model_unavailable", "Model is not loaded")

    max_bytes = state.settings.max_upload_mb * 1024 * 1024
    data = await read_upload(file, max_bytes)
    img = load_image(data, max_bytes=max_bytes, max_pixels=state.settings.max_image_pixels)

    # Inference is blocking CPU/GPU work: keep it off the event loop.
    probs = await run_in_threadpool(predict_probs, model, img, state.device)
    return build_response(
        probs,
        threshold=state.settings.uncertain_threshold,
        width=img.width,
        height=img.height,
        model_version=state.model_version,
    )
