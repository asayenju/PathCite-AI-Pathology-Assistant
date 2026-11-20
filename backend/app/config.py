from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[2] / "model" / "resnet18_finetuned.pt"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_path: Path = DEFAULT_MODEL_PATH
    uncertain_threshold: float = 0.7
    max_upload_mb: int = 10


def get_settings() -> Settings:
    return Settings()
