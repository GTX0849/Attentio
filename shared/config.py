"""Loads the shared YAML configuration and the database URLs from .env."""

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


def database_url() -> str:
    url = _env("DATABASE_URL")
    if url is None:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    return url


def test_database_url() -> str | None:
    """None when TEST_DATABASE_URL is not configured (tests then skip)."""
    return _env("TEST_DATABASE_URL")
