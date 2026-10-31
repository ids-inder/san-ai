"""Runtime configuration for San AI, loaded from env / .env."""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Autonomy(str, Enum):
    FULL = "full"           # execute immediately, nobody in the loop
    GUARDED = "guarded"     # allowlist auto-runs, the rest queues
    APPROVAL = "approval"   # everything waits for a human


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SANAI_", env_file=".env", extra="ignore"
    )

    # brain
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    deep_model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tokens: int = 32000

    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:14b-instruct"

    # autonomy
    autonomy: Autonomy = Autonomy.FULL
    integrity_guard: bool = True

    # learning gate
    mastery_threshold: float = 0.85
    max_learn_rounds: int = 4
    quiz_size: int = 10

    # execution
    workdir: Path = Path("/var/lib/sanai/work")
    db_path: Path = Path("/var/lib/sanai/sanai.db")
    cmd_timeout: int = 300
    max_act_rounds: int = 12
    shell_user: str = ""

    # web
    web_host: str = "127.0.0.1"
    web_port: int = 8899
    web_token: str = ""

    def ensure_dirs(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Test hook."""
    global _settings
    _settings = None
