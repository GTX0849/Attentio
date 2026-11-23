"""The dashboard's only data source: HTTP calls to the backend (never the database)."""

from datetime import date
from typing import Any

import requests

from dashboard import session
from dashboard.session import SessionExpired
from shared.config import http_timeout

__all__ = ["BackendError", "SessionExpired", "SignInError", "WAKING_UP_MESSAGE"]

DEFAULT_TIMEOUT_SECONDS = 10.0
# Free hosting sleeps when idle and takes up to a minute to wake: a timeout or a refused connection
# then almost always means "try again shortly", so that is what the person is told.
WAKING_UP_MESSAGE = "The backend is waking up (free hosting sleeps when idle). Refresh in a moment"


class BackendError(Exception):
    """The backend could not give an answer; the message is safe to show the user."""


class SignInError(Exception):
    """Signing in failed. `kind` is "invalid", "rate_limited", "unreachable" or "error"."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _call(
    backend_url: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any = None,
    files: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Any:
    url = f"{backend_url.rstrip('/')}{path}"
    token = session.token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.request(
            method,
            url,
            params=params,
            json=json,
            files=files,
            data=data,
            headers=headers,
            timeout=http_timeout(DEFAULT_TIMEOUT_SECONDS),
        )
    except (requests.Timeout, requests.ConnectionError) as e:
        raise BackendError(WAKING_UP_MESSAGE) from e
    except requests.RequestException as e:
        raise BackendError(f"Cannot reach the backend at {backend_url}: {e}") from e
    if resp.status_code == 401 and token:
        raise SessionExpired()  # the token expired, was revoked, or the account was removed
    if not resp.ok:
        detail = ""
        try:
            detail = str(resp.json().get("detail", ""))
        except (ValueError, AttributeError):
            pass
        raise BackendError(detail or f"Backend error {resp.status_code}")
    return None if resp.status_code == 204 else resp.json()


def login(backend_url: str, email: str, password: str) -> dict[str, Any]:
    """Sign in. Returns `{token, role, email, display_name, student_register_no, teacher_id, ...}`.

    Raises SignInError. Sent without a token, so a 401 here is "wrong email or password".
    """
    url = f"{backend_url.rstrip('/')}/api/auth/login"
    try:
        resp = requests.post(url, json={"email": email, "password": password}, timeout=http_timeout(DEFAULT_TIMEOUT_SECONDS))
    except requests.RequestException as e:
        raise SignInError("unreachable") from e
    if resp.status_code == 401:
        raise SignInError("invalid")
    if resp.status_code == 429:
        raise SignInError("rate_limited")
    if not resp.ok:
        raise SignInError("error")
    body: dict[str, Any] = resp.json()
    return body


def logout(backend_url: str) -> None:
    """End this sign-in on the backend. Failing to reach it does not matter: the token expires anyway."""
    try:
        _call(backend_url, "POST", "/api/auth/logout")
    except (BackendError, SessionExpired):
        pass


def list_accounts(backend_url: str) -> list[dict[str, Any]]:
    """Every account (admin only): `id`, `email`, `role`, `linked_name`, `created_at`, `simulated`. No passwords."""
    rows: list[dict[str, Any]] = _call(backend_url, "GET", "/api/accounts")
    return rows


def create_account(
    backend_url: str,
    email: str,
    password: str,
    role: str,
    student_register_no: str | None,
    teacher_id: int | None,
) -> dict[str, Any]:
    """Make an account (admin only). See POST /api/accounts."""
    body: dict[str, Any] = _call(
        backend_url,
        "POST",
        "/api/accounts",
        json={
            "email": email,
            "password": password,
            "role": role,
            "student_register_no": student_register_no,
            "teacher_id": teacher_id,
        },
    )
    return body


def reset_account_password(backend_url: str, account_id: int, password: str) -> None:
    """Set a new password for an account (admin only); its sessions end."""
    _call(backend_url, "POST", f"/api/accounts/{account_id}/password", json={"password": password})


def list_teacher_subjects(backend_url: str, teacher_id: int) -> list[dict[str, Any]]:
    """The subjects the teacher is timetabled for. See GET /api/teachers/{id}/subjects."""
    subjects: list[dict[str, Any]] = _call(backend_url, "GET", f"/api/teachers/{teacher_id}/subjects")
    return subjects


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


def get_student_marks(backend_url: str, register_no: str) -> dict[str, Any]:
    """The student's own marks and percentile in each test, newest first. See GET /api/students/{id}/marks."""
    body: dict[str, Any] = _call(backend_url, "GET", f"/api/students/{register_no}/marks")
    return body


def get_simulation(backend_url: str) -> dict[str, Any] | None:
    """What the loaded simulation holds (counts, weeks, true effects), or None when there is none."""
    body: dict[str, Any] | None = _call(backend_url, "GET", "/api/simulation")
    return body


def list_students(backend_url: str) -> list[dict[str, Any]]:
    """Every student: `register_no`, `name`, `section`, `simulated`, `archetype`. See GET /api/students."""
    students: list[dict[str, Any]] = _call(backend_url, "GET", "/api/students")
    return students


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


def get_teacher_feedback(backend_url: str, teacher_id: int) -> dict[str, Any]:
    """One teacher's figures, lectures and attendance, nothing about other teachers.

    See GET /api/teachers/{id}/feedback.
    """
    body: dict[str, Any] = _call(backend_url, "GET", f"/api/teachers/{teacher_id}/feedback")
    return body


def list_teacher_feedback(backend_url: str) -> list[dict[str, Any]]:
    """Every teacher's figures and attendance, for the admin view. See GET /api/teachers/feedback."""
    rows: list[dict[str, Any]] = _call(backend_url, "GET", "/api/teachers/feedback")
    return rows


def get_admin_overview(backend_url: str) -> dict[str, Any]:
    """Teacher figures with class averages, attendance by section and unassigned windows.

    Totals only, nothing about an individual student. See GET /api/admin/overview.
    """
    body: dict[str, Any] = _call(backend_url, "GET", "/api/admin/overview")
    return body


def get_lecture_timeline(backend_url: str, lecture_id: int) -> dict[str, Any]:
    """Class attention per 30-second window of one lecture. See GET /api/lectures/{id}/timeline."""
    body: dict[str, Any] = _call(backend_url, "GET", f"/api/lectures/{lecture_id}/timeline")
    return body


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


def upload_marks(
    backend_url: str,
    teacher_id: int,
    subject_id: int,
    test_date: date,
    name: str,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    """Upload the marks of one test (CSV or .xlsx). See POST /api/marks/tests.

    `applied` is false, with the bad rows in `errors`, when nothing was stored.
    """
    body: dict[str, Any] = _call(
        backend_url,
        "POST",
        "/api/marks/tests",
        files={"file": (filename, content, "application/octet-stream")},
        data={
            "teacher_id": teacher_id,
            "subject_id": subject_id,
            "test_date": test_date.isoformat(),
            "name": name,
        },
    )
    return body


def list_teacher_marks_tests(backend_url: str, teacher_id: int) -> list[dict[str, Any]]:
    """The teacher's own tests, newest first. See GET /api/teachers/{id}/marks/tests."""
    tests: list[dict[str, Any]] = _call(backend_url, "GET", f"/api/teachers/{teacher_id}/marks/tests")
    return tests


def get_marks_groups(backend_url: str, test_id: int) -> dict[str, Any]:
    """The four student groups of a test, with the students (the teacher's own view).

    See GET /api/marks/tests/{id}/groups.
    """
    body: dict[str, Any] = _call(
        backend_url, "GET", f"/api/marks/tests/{test_id}/groups", params={"students": "true"}
    )
    return body
