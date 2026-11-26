from pathlib import Path

from app.config import Settings

SECRET = "sk-test-very-secret-value-123"


def test_jev_key_defaults_to_none(monkeypatch):
    monkeypatch.delenv("JEV_KEY", raising=False)
    assert Settings(_env_file=None).jev_key is None


def test_jev_key_read_from_env(monkeypatch):
    monkeypatch.setenv("JEV_KEY", SECRET)
    s = Settings(_env_file=None)
    assert s.jev_key is not None
    assert s.jev_key.get_secret_value() == SECRET


def test_jev_key_by_field_name(monkeypatch):
    monkeypatch.delenv("JEV_KEY", raising=False)
    s = Settings(_env_file=None, jev_key=SECRET)
    assert s.jev_key.get_secret_value() == SECRET


def test_jev_key_never_in_repr_or_str(monkeypatch):
    monkeypatch.setenv("JEV_KEY", SECRET)
    s = Settings(_env_file=None)
    assert SECRET not in repr(s)
    assert SECRET not in str(s)
    assert SECRET not in repr(s.jev_key)


def test_triage_defaults(monkeypatch):
    for k in ("JEV_MODEL", "JEV_BASE_URL", "JEV_TIMEOUT_S", "TRIAGE_ASR_FLAG", "TRIAGE_SKIP_MIN",
              "TRIAGE_MAX_TURNS", "TRIAGE_MAX_TURN_CHARS"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.jev_model == "jev-1.14.0"
    assert s.jev_base_url == "https://api.typesafe.ai"
    assert s.jev_timeout_s == 15
    assert s.triage_asr_flag == 0.5
    assert s.triage_skip_min == 0.8
    assert s.triage_max_turns == 500
    assert s.triage_max_turn_chars == 5000


def test_env_files_are_repo_root_then_backend_local():
    # Repo-root .env first; the backend-local .env (relative to cwd) comes later and overrides it.
    repo_root = Path(__file__).resolve().parents[2]
    assert tuple(Settings.model_config["env_file"]) == (repo_root / ".env", ".env")


def test_key_read_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("JEV_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text(f"JEV_KEY={SECRET}\n")
    assert Settings(_env_file=f).jev_key.get_secret_value() == SECRET
