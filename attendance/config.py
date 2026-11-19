"""Runtime configuration, loaded from the environment.

Deliberately dependency-free (no pydantic-settings) so the attendance
service can run on a bare interpreter.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PREFIX = "ATT_"


def _raw(name: str) -> str:
    return os.environ.get(PREFIX + name, "").strip()


def _int(name: str, default: int) -> int:
    raw = _raw(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # storage
    db_path: Path = Path("/var/lib/attendance/attendance.db")

    # credential encryption. Generate with: attendance keygen
    secret_key: str = ""

    # dashboard
    web_host: str = "127.0.0.1"
    web_port: int = 8898
    web_password: str = ""
    session_hours: int = 12

    # upstream
    api_base: str = "https://api.etimeoffice.com/api"
    http_timeout: int = 60

    # sync behaviour
    lookback_days: int = 7
    # Punches are stored as clinic-local wall-clock time. This is recorded for
    # display only; no conversion is performed.
    tz_name: str = "Asia/Kolkata"

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        if _raw("DB_PATH"):
            d.db_path = Path(_raw("DB_PATH"))
        d.secret_key = _raw("SECRET_KEY")
        d.web_host = _raw("WEB_HOST") or d.web_host
        d.web_port = _int("WEB_PORT", d.web_port)
        d.web_password = _raw("WEB_PASSWORD")
        d.session_hours = _int("SESSION_HOURS", d.session_hours)
        d.api_base = (_raw("API_BASE") or d.api_base).rstrip("/")
        d.http_timeout = _int("HTTP_TIMEOUT", d.http_timeout)
        d.lookback_days = _int("LOOKBACK_DAYS", d.lookback_days)
        d.tz_name = _raw("TZ_NAME") or d.tz_name
        return d

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


_cached: Settings | None = None


def settings(refresh: bool = False) -> Settings:
    global _cached
    if _cached is None or refresh:
        _cached = Settings.from_env()
    return _cached
