"""The dashboard's only data source: HTTP calls to the backend (never the database)."""

from typing import Any

import requests


class BackendError(Exception):
    """The backend could not give an answer; the message is safe to show the user."""


def _call(
    backend_url: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any = None,
    files: dict[str, Any] | None = None,
) -> Any:
    url = f"{backend_url.rstrip('/')}{path}"
    try:
        resp = requests.request(method, url, params=params, json=json, files=files, timeout=10)
    except requests.RequestException as e:
        raise BackendError(f"Cannot reach the backend at {backend_url}: {e}") from e
    if not resp.ok:
        detail = ""
        try:
            detail = str(resp.json().get("detail", ""))
        except (ValueError, AttributeError):
            pass
        raise BackendError(detail or f"Backend error {resp.status_code}")
    return resp.json()


def get_live(backend_url: str, register_no: str, minutes: int) -> list[dict[str, Any]]:
    """Recent windows for a student, oldest first. See GET /api/students/{id}/live.

    Each window carries `lecture`: the lecture it belongs to, or None.
    """
    body = _call(backend_url, "GET", f"/api/students/{register_no}/live", params={"minutes": minutes})
    windows: list[dict[str, Any]] = body["windows"]
    return windows


def get_student_current(backend_url: str, register_no: str) -> dict[str, Any]:
    """The live header figures: running lecture, last 5 minutes, average, presence, month count.

    See GET /api/students/{id}/current.
    """
    body: dict[str, Any] = _call(backend_url, "GET", f"/api/students/{register_no}/current")
    return body


def get_student_profile(backend_url: str, register_no: str) -> dict[str, Any]:
    """Semester breakdowns, weekly trend and attendance. See GET /api/students/{id}/profile."""
    body: dict[str, Any] = _call(backend_url, "GET", f"/api/students/{register_no}/profile")
    return body


def get_timetable(backend_url: str) -> dict[str, Any]:
    """`{"timezone": "Asia/Kolkata", "slots": [...]}`. See GET /api/timetable."""
    body: dict[str, Any] = _call(backend_url, "GET", "/api/timetable")
    return body


def upload_timetable(backend_url: str, filename: str, content: bytes) -> dict[str, Any]:
    """Replace the weekly timetable with a CSV. See POST /api/timetable."""
    body: dict[str, Any] = _call(
        backend_url, "POST", "/api/timetable", files={"file": (filename, content, "text/csv")}
    )
    return body


def list_rooms(backend_url: str) -> list[dict[str, Any]]:
    rooms: list[dict[str, Any]] = _call(backend_url, "GET", "/api/rooms")
    return rooms


def list_teachers(backend_url: str) -> list[dict[str, Any]]:
    teachers: list[dict[str, Any]] = _call(backend_url, "GET", "/api/teachers")
    return teachers


def list_subjects(backend_url: str) -> list[dict[str, Any]]:
    subjects: list[dict[str, Any]] = _call(backend_url, "GET", "/api/subjects")
    return subjects


def list_sections(backend_url: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = _call(backend_url, "GET", "/api/sections")
    return sections


def list_lectures(backend_url: str, limit: int = 20) -> list[dict[str, Any]]:
    """Lectures, newest first. See GET /api/lectures."""
    lectures: list[dict[str, Any]] = _call(
        backend_url, "GET", "/api/lectures", params={"limit": limit}
    )
    return lectures


def list_sessions(backend_url: str, open_only: bool = False) -> list[dict[str, Any]]:
    """Manual sessions, newest first. See GET /api/sessions."""
    params = {"open": "true"} if open_only else None
    sessions: list[dict[str, Any]] = _call(backend_url, "GET", "/api/sessions", params=params)
    return sessions


def start_session(
    backend_url: str, room_id: str, teacher_id: int, subject_id: int, section_id: int | None
) -> dict[str, Any]:
    """Start a manual session. A second open session in the room is refused."""
    body: dict[str, Any] = _call(
        backend_url,
        "POST",
        "/api/sessions",
        json={
            "room_id": room_id,
            "teacher_id": teacher_id,
            "subject_id": subject_id,
            "section_id": section_id,
        },
    )
    return body


def stop_session(backend_url: str, session_id: int) -> dict[str, Any]:
    body: dict[str, Any] = _call(backend_url, "POST", f"/api/sessions/{session_id}/stop")
    return body
