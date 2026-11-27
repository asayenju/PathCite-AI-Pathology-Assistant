import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.formparsers import MultiPartParser

from app.check.router import default_llm_factory, default_search_factory
from app.check.router import router as check_router
from app.config import get_settings
from app.errors import ApiError, register_error_handlers
from app.images import load_image, read_upload
from app.model import ModelMissingError, load_model, pick_device
from app.predict import build_response, predict_probs
from app.schemas import PredictResponse
from app.transcripts.router import router as transcripts_router
from app.triage.router import default_jev_client_factory
from app.triage.router import router as triage_router

logger = logging.getLogger("pathcite")


def _model_version(path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return f"{path.name}@{digest}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Starlette spools uploads over 1 MB to a temp file on disk by default. Keep the whole
    # (size-capped) upload in memory so patient images and transcripts never touch disk.
    largest_mb = max(settings.max_upload_mb, settings.max_transcript_mb)
    MultiPartParser.spool_max_size = largest_mb * 1024 * 1024 + 1
    device = pick_device()
    app.state.settings = settings
    app.state.device = device
    # Builds the Jev client for /triage; tests swap in a fake. Without JEV_KEY, /triage returns 503.
    app.state.jev_client_factory = default_jev_client_factory
    # Clients for /check, built per request; tests swap in fakes. Without JEV_KEY, /check answers from Luna alone.
    app.state.check_search_factory = default_search_factory
    app.state.check_llm_factory = default_llm_factory
    app.state.check_jev_factory = default_jev_client_factory
    # A missing weights file only disables /predict (transcript import doesn't need it).
    # A present but mismatched or corrupt file is a real bug: startup still fails fast.
    try:
        app.state.model = load_model(settings.model_path, device)
        app.state.model_version = _model_version(settings.model_path)
    except ModelMissingError:
        logger.warning("Model weights not found at %s: /predict is disabled", settings.model_path)
        app.state.model = None
        app.state.model_version = None
    yield
    app.state.model = None


app = FastAPI(title="PathCite API", lifespan=lifespan)
register_error_handlers(app)
app.include_router(transcripts_router)
app.include_router(triage_router)
app.include_router(check_router)


@app.get("/health")
def health():
    # The app is up either way; without the model only /predict is unavailable.
    loaded = getattr(app.state, "model", None) is not None
    return {"status": "ok" if loaded else "degraded", "model_loaded": loaded}


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
