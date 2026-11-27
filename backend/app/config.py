from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = REPO_ROOT / "model" / "resnet18_finetuned_v2.pt"


class Settings(BaseSettings):
    # Repo-root .env (gitignored) first, then a backend-local .env (cwd) that overrides it.
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), extra="ignore")

    model_path: Path = DEFAULT_MODEL_PATH
    uncertain_threshold: float = 0.7
    max_upload_mb: int = 10
    max_image_pixels: int = 25_000_000  # ~5000x5000; guards against decompression bombs
    max_transcript_mb: int = 20
    asr_low_confidence: float = 0.6  # words below this are low-confidence

    # Jev triage (TypeSafe AI). Without a key /triage is unavailable; the rest of the app runs.
    jev_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("JEV_KEY", "jev_key"))
    jev_model: str = "jev-1.14.0"  # pinned, not jev-latest, so results don't change under us
    jev_base_url: str = "https://api.typesafe.ai"
    jev_timeout_s: float = 15
    # Kept after the 2026-11-25 `pytest -m jev` eval (backend/evals/triage/README.md).
    triage_asr_flag: float = 0.5  # p_asr >= this -> possible transcription error
    triage_skip_min: float = 0.8  # P(small_talk) >= this -> skip; otherwise claim
    triage_max_turns: int = 500
    triage_max_turn_chars: int = 5000

    # OpenAI embeddings for the evidence corpus (docs/PRD-corpus-ingest.md). Backend scripts only.
    openai_vector_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPEN_AI_VECTOR_KEY", "openai_vector_key")
    )
    embed_model: str = "text-embedding-3-small"
    embed_dims: int = 1536


def get_settings() -> Settings:
    return Settings()
