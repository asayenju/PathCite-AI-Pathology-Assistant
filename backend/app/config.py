from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[2] / "model" / "resnet18_finetuned_v2.pt"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_path: Path = DEFAULT_MODEL_PATH
    uncertain_threshold: float = 0.7
    max_upload_mb: int = 10
    max_image_pixels: int = 25_000_000  # ~5000x5000; guards against decompression bombs
    max_transcript_mb: int = 20
    asr_low_confidence: float = 0.6  # words below this are low-confidence


def get_settings() -> Settings:
    return Settings()
