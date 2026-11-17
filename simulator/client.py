"""Seeds and wipes the simulation through the backend's public HTTP API.

The simulator is an ordinary client: it uploads its timetable through the timetable upload,
registers cameras and students, stores the true effects and sends attention records through
the batch ingest endpoint. It never writes to the database itself.

`http` is anything with a `request(method, url, **kwargs)` method that returns a response with
`status_code`, `json()` and `text`: a `requests.Session` in real use, a FastAPI `TestClient`
in tests (with an empty `base_url`).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from simulator.config import SimulatorConfig
from simulator.generate import (
    build_students,
    lecture_records,
    lecture_schedule,
    true_effects,
)
from simulator.timetable import camera_for, room_for, timetable_csv

REQUEST_TIMEOUT_SECONDS = 300


class SimulatorError(Exception):
    """The backend refused or could not be reached; the message is safe to print."""


class TimeoutSession(requests.Session):
    """A requests session that never waits forever."""

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
        return super().request(method, url, *args, **kwargs)


class Backend:
    """Thin wrapper over the HTTP client that raises `SimulatorError` on any failure."""

    def __init__(self, http: Any, base_url: str = "") -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._http.request(method, f"{self._base}{path}", **kwargs)
        except requests.RequestException as e:
            raise SimulatorError(f"Cannot reach the backend at {self._base}: {e}") from e
        if resp.status_code >= 400:
            raise SimulatorError(f"{method} {path} failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()


@dataclass(frozen=True)
class SeedReport:
    students: int
    lectures: int
    records: int
    seconds: float


def wipe(backend: Backend) -> dict[str, int]:
    """Remove all simulated data. Returns how many rows of each kind were removed."""
    removed: dict[str, int] = backend.call("DELETE", "/api/simulation")
    return removed


def seed(
    backend: Backend,
    config: SimulatorConfig,
    now: datetime | None = None,
    progress: Callable[[str], None] = lambda message: None,
) -> SeedReport:
    """Wipe the old simulation, then generate and send a new one ending at `now` (default: now)."""
    started = time.monotonic()
    moment = (now or datetime.now(UTC)).astimezone(UTC)

    removed = wipe(backend)
    progress(f"Wiped the previous simulation ({removed['records']} records)")

    upload = backend.call(
        "POST",
        "/api/timetable",
        files={"file": ("simulated-timetable.csv", timetable_csv(config).encode(), "text/csv")},
        data={"simulated": "true"},
    )
    if not upload["applied"]:
        raise SimulatorError(f"The simulated timetable was rejected: {upload['errors']}")
    progress(f"Uploaded the timetable ({upload['slots_total']} weekly slots)")
    tz = ZoneInfo(backend.call("GET", "/api/timetable")["timezone"])

    for i in range(len(config.sections)):
        backend.call(
            "PUT",
            f"/api/cameras/{camera_for(i)}",
            json={"room_id": room_for(i), "simulated": True},
        )
    students = build_students(config)
    for s in students:
        backend.call(
            "POST",
            "/api/students",
            json={
                "register_no": s.register_no,
                "name": s.name,
                "section": s.section,
                "simulated": True,
                "archetype": s.archetype,
            },
        )
    backend.call(
        "PUT",
        "/api/simulation",
        json={"effects": true_effects(config), "weeks": config.weeks, "seed": config.seed},
    )
    progress(f"Registered {len(students)} students and the true effects")

    lectures = lecture_schedule(config, tz, moment)
    sent = 0
    for number, lecture in enumerate(lectures, start=1):
        records = lecture_records(config, lecture, students)
        for first in range(0, len(records), config.records_per_request):
            backend.call(
                "POST", "/api/records", json=records[first : first + config.records_per_request]
            )
        sent += len(records)
        if number % 20 == 0 or number == len(lectures):
            elapsed = time.monotonic() - started
            progress(f"Sent {number} of {len(lectures)} lectures, {sent} records ({elapsed:.0f} s)")
    return SeedReport(
        students=len(students),
        lectures=len(lectures),
        records=sent,
        seconds=time.monotonic() - started,
    )
