"""The dashboard's only data source: HTTP calls to the backend (never the database)."""

from typing import Any

import requests


class BackendError(Exception):
    """The backend could not give an answer; the message is safe to show the user."""


def get_live(backend_url: str, register_no: str, minutes: int) -> list[dict[str, Any]]:
    """Recent windows for a student, oldest first. See GET /api/students/{id}/live."""
    url = f"{backend_url.rstrip('/')}/api/students/{register_no}/live"
    try:
        resp = requests.get(url, params={"minutes": minutes}, timeout=5)
    except requests.RequestException as e:
        raise BackendError(f"Cannot reach the backend at {backend_url}: {e}") from e
    if resp.status_code == 404:
        raise BackendError(resp.json().get("detail", "Not found"))
    if not resp.ok:
        raise BackendError(f"Backend error {resp.status_code}")
    windows: list[dict[str, Any]] = resp.json()["windows"]
    return windows
