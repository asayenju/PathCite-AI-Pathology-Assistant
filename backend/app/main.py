from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.model import load_model, pick_device


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    device = pick_device()
    app.state.settings = settings
    app.state.device = device
    # Fails fast: a missing or mismatched weights file stops startup with a clear error.
    app.state.model = load_model(settings.model_path, device)
    yield
    app.state.model = None


app = FastAPI(title="PathCite API", lifespan=lifespan)


@app.get("/health")
def health():
    loaded = getattr(app.state, "model", None) is not None
    body = {"status": "ok" if loaded else "unavailable", "model_loaded": loaded}
    return JSONResponse(body, status_code=200 if loaded else 503)
