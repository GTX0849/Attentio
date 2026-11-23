"""Loads the shared YAML configuration and the database URLs from .env."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "classpulse.yaml"


@dataclass(frozen=True)
class Config:
    target_fps: int
    window_seconds: int
    backend_url: str
    dashboard_refresh_seconds: int
    # The whole parsed YAML, so later tickets can read their own keys.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


BACKEND_URL_ENV = "CLASSPULSE_BACKEND_URL"


def load_config(path: Path = CONFIG_PATH) -> Config:
    """The shared config. CLASSPULSE_BACKEND_URL, when set, replaces `backend.url`."""
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    backend_url = _env(BACKEND_URL_ENV) or str(raw["backend"]["url"])
    return Config(
        target_fps=int(raw["worker"]["target_fps"]),
        window_seconds=int(raw["worker"]["window_seconds"]),
        backend_url=backend_url.rstrip("/"),
        dashboard_refresh_seconds=int(raw["dashboard"]["refresh_seconds"]),
        raw=raw,
    )


def _env(name: str) -> str | None:
    load_dotenv(PROJECT_ROOT / ".env")  # does not override real environment variables
    return os.environ.get(name) or None


def env_setting(name: str) -> str | None:
    """An environment variable (or a line of .env), None when unset or empty."""
    return _env(name)


_PSYCOPG_SCHEME = "postgresql+psycopg://"


def normalise_database_url(url: str) -> str:
    """Point a `postgres://` or `postgresql://` URL at the installed driver (psycopg 3).

    Managed hosts (Neon, Render, Heroku) hand out plain `postgres://` or `postgresql://` URLs, which
    SQLAlchemy would send to psycopg2, and that is not installed. Everything after the scheme,
    including query parameters such as `sslmode` and `channel_binding`, is kept exactly as it was.
    Any other URL is returned unchanged. Never log the result: it carries the password.
    """
    url = url.strip()
    for scheme in ("postgres://", "postgresql://"):
        if url.startswith(scheme):
            return _PSYCOPG_SCHEME + url[len(scheme) :]
    return url


def database_url() -> str:
    url = _env("DATABASE_URL")
    if url is None:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    return normalise_database_url(url)


def test_database_url() -> str | None:
    """None when TEST_DATABASE_URL is not configured (tests then skip)."""
    url = _env("TEST_DATABASE_URL")
    return normalise_database_url(url) if url else None


HTTP_TIMEOUT_ENV = "CLASSPULSE_HTTP_TIMEOUT"


def http_timeout(default: float) -> float:
    """Seconds to wait for the backend: CLASSPULSE_HTTP_TIMEOUT when it is a positive number, else `default`.

    A free host sleeps when idle and takes up to a minute to wake, so a hosted dashboard sets about 75.
    """
    raw = _env(HTTP_TIMEOUT_ENV)
    if raw is None:
        return default
    try:
        seconds = float(raw)
    except ValueError:
        return default
    return seconds if math.isfinite(seconds) and seconds > 0 else default


SERVICE_KEY_ENV = "CLASSPULSE_SERVICE_KEY"
DEMO_PASSWORD_ENV = "CLASSPULSE_DEMO_PASSWORD"


def service_key() -> str | None:
    """The key the worker and the simulator send in `X-ClassPulse-Key`. None when not configured:
    there is no default, so the machine endpoints then accept only an admin token."""
    return _env(SERVICE_KEY_ENV)


def demo_password() -> str | None:
    """The password for simulated accounts, when the person running the simulator chose one."""
    return _env(DEMO_PASSWORD_ENV)
