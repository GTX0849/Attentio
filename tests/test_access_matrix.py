"""Who may call which endpoint, with sign-in enforced: student, teacher, admin, no token and the service key.

An endpoint is "allowed" for an actor when the answer is neither 401 nor 403 (it may be 404 or 422 for
the made-up input); it is "denied" when it is 403, and 401 for a caller with no valid token.
"""

from dataclasses import dataclass
from pathlib import Path
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.models import Lecture, MarksTest, Subject
from tests.auth_helpers import add_account, add_student, add_teacher, bearer, db, sign_in
from tests.conftest import SERVICE_KEY, FakeClock, ClientFactory

ACTORS = ("student1", "student2", "teacher1", "teacher2", "admin", "service", "nobody")


@dataclass
class World:
    client: TestClient
    headers: dict[str, dict[str, str]]
    teacher1: int
    teacher2: int
    lecture: int
    test: int


@pytest.fixture
def world(secure_client: TestClient) -> World:
    add_student(secure_client, "STU002", "Second Student")
    teacher1 = add_teacher(secure_client, "Dr. One")
    teacher2 = add_teacher(secure_client, "Dr. Two")
    add_account(secure_client, "admin@example.test", "admin")
    add_account(secure_client, "s1@example.test", "student", student="STU001")
    add_account(secure_client, "s2@example.test", "student", student="STU002")
    add_account(secure_client, "t1@example.test", "teacher", teacher_id=teacher1)
    add_account(secure_client, "t2@example.test", "teacher", teacher_id=teacher2)
    with db(secure_client) as session:
        subject = Subject(name="Algorithms")
        session.add(subject)
        session.flush()
        lecture = Lecture(
            source="manual",
            room_id="R101",
            teacher_id=teacher1,
            subject_id=subject.id,
            lecture_date=date(2026, 3, 2),
            starts_at=datetime(2026, 3, 2, 4, 30, tzinfo=UTC),
        )
        test = MarksTest(teacher_id=teacher1, subject_id=subject.id, test_date=date(2026, 3, 2), name="T")
        session.add_all([lecture, test])
        session.commit()
        lecture_id, test_id = lecture.id, test.id
    headers = {
        "student1": bearer(sign_in(secure_client, "s1@example.test")["token"]),
        "student2": bearer(sign_in(secure_client, "s2@example.test")["token"]),
        "teacher1": bearer(sign_in(secure_client, "t1@example.test")["token"]),
        "teacher2": bearer(sign_in(secure_client, "t2@example.test")["token"]),
        "admin": bearer(sign_in(secure_client, "admin@example.test")["token"]),
        "service": {"X-Attentio-Key": SERVICE_KEY},
        "nobody": {},
    }
    return World(secure_client, headers, teacher1, teacher2, lecture_id, test_id)


def call(w: World, actor: str, method: str, path: str, json: Any = None) -> int:
    status: int = w.client.request(method, path, headers=w.headers[actor], json=json).status_code
    return status


# (method, path with {t1} {t2} {lecture} {test}, json, actors who may call it)
STUDENT_OWN = {"student1"}
RULES: list[tuple[str, str, Any, set[str]]] = [
    # a student's own data: that student only, not even an admin
    ("GET", "/api/students/STU001/live", None, STUDENT_OWN),
    ("GET", "/api/students/STU001/profile", None, STUDENT_OWN),
    ("GET", "/api/students/STU001/current", None, STUDENT_OWN),
    ("GET", "/api/students/STU001/marks", None, STUDENT_OWN),
    ("GET", "/api/students/STU002/profile", None, {"student2"}),
    # a teacher's own data: that teacher only
    ("GET", "/api/teachers/{t1}/feedback", None, {"teacher1"}),
    ("GET", "/api/teachers/{t2}/feedback", None, {"teacher2"}),
    ("GET", "/api/teachers/{t1}/marks/tests", None, {"teacher1"}),
    ("GET", "/api/teachers/{t1}/subjects", None, {"teacher1"}),
    ("GET", "/api/marks/tests/{test}/groups", None, {"teacher1"}),
    ("GET", "/api/marks/tests/{test}/groups?students=true", None, {"teacher1"}),
    ("GET", "/api/lectures/{lecture}/timeline", None, {"teacher1"}),
    # the admin's overview
    ("GET", "/api/admin/overview", None, {"admin"}),
    ("GET", "/api/teachers/feedback", None, {"admin"}),
    ("GET", "/api/accounts", None, {"admin"}),
    ("POST", "/api/sessions", {"room_id": "NOPE", "teacher_id": 1, "subject_id": 1}, {"admin"}),
    ("POST", "/api/sessions/99999/stop", None, {"admin"}),
    # admin or the service key (machine endpoints and the lists the admin view and the simulator read)
    ("POST", "/api/records", [], {"admin", "service"}),
    ("GET", "/api/timetable", None, {"admin", "service"}),
    ("GET", "/api/rooms", None, {"admin", "service"}),
    ("GET", "/api/students", None, {"admin", "service"}),
    ("GET", "/api/lectures", None, {"admin", "service"}),
    ("GET", "/api/sessions", None, {"admin", "service"}),
    ("PUT", "/api/cameras/CAM1", {"room_id": "R101"}, {"admin", "service"}),
    ("POST", "/api/students", {"register_no": "STU001"}, {"admin", "service"}),
    ("PUT", "/api/simulation", {"effects": []}, {"admin", "service"}),
    ("DELETE", "/api/simulation", None, {"admin", "service"}),
    # lists teachers need: any staff, never a student
    ("GET", "/api/teachers", None, {"teacher1", "teacher2", "admin", "service"}),
    ("GET", "/api/subjects", None, {"teacher1", "teacher2", "admin", "service"}),
    ("GET", "/api/sections", None, {"teacher1", "teacher2", "admin", "service"}),
    # the "simulated data" banner is for every signed-in role
    ("GET", "/api/simulation", None, set(ACTORS) - {"nobody"}),
    ("GET", "/api/auth/me", None, {"student1", "student2", "teacher1", "teacher2", "admin"}),
]


@pytest.mark.parametrize(("method", "path", "body", "allowed"), RULES, ids=[f"{m} {p}" for m, p, _, _ in RULES])
def test_each_endpoint_allows_only_its_actors(
    world: World, method: str, path: str, body: Any, allowed: set[str]
) -> None:
    url = path.format(t1=world.teacher1, t2=world.teacher2, lecture=world.lecture, test=world.test)
    for actor in ACTORS:
        status = call(world, actor, method, url, body)
        if actor == "nobody":
            assert status == 401, f"{method} {url} without a token gave {status}"
        elif actor in allowed:
            assert status not in (401, 403), f"{method} {url} refused {actor} with {status}"
        else:
            assert status == 403, f"{method} {url} gave {actor} {status}, expected 403"


def test_a_student_cannot_read_another_student(world: World) -> None:
    assert call(world, "student1", "GET", "/api/students/STU002/profile") == 403
    assert call(world, "student1", "GET", "/api/students/STU002/live") == 403
    assert call(world, "student1", "GET", "/api/students/STU002/marks") == 403
    assert call(world, "student2", "GET", "/api/students/STU001/current") == 403
    assert call(world, "student1", "GET", "/api/students") == 403  # and cannot list students


def test_a_teacher_cannot_read_another_teachers_feedback_groups_or_timeline(world: World) -> None:
    assert call(world, "teacher2", "GET", f"/api/teachers/{world.teacher1}/feedback") == 403
    assert call(world, "teacher2", "GET", f"/api/teachers/{world.teacher1}/marks/tests") == 403
    assert call(world, "teacher2", "GET", f"/api/marks/tests/{world.test}/groups?students=true") == 403
    assert call(world, "teacher2", "GET", f"/api/lectures/{world.lecture}/timeline") == 403
    assert call(world, "teacher1", "GET", f"/api/lectures/{world.lecture}/timeline") == 200


def test_a_student_cannot_read_the_admin_overview_or_the_teacher_figures(world: World) -> None:
    assert call(world, "student1", "GET", "/api/admin/overview") == 403
    assert call(world, "student1", "GET", "/api/teachers/feedback") == 403
    assert call(world, "teacher1", "GET", "/api/admin/overview") == 403
    assert call(world, "teacher1", "GET", "/api/teachers/feedback") == 403


def test_admin_cannot_read_per_student_endpoints_or_one_teachers_own_view(world: World) -> None:
    for path in ("live", "profile", "current", "marks"):
        assert call(world, "admin", "GET", f"/api/students/STU001/{path}") == 403
    assert call(world, "admin", "GET", f"/api/teachers/{world.teacher1}/feedback") == 403
    assert call(world, "admin", "GET", f"/api/marks/tests/{world.test}/groups?students=true") == 403


def test_a_missing_or_unknown_token_is_401_everywhere(world: World) -> None:
    for method, path in (
        ("GET", "/api/students/STU001/profile"),
        ("GET", "/api/admin/overview"),
        ("GET", "/api/teachers"),
        ("POST", "/api/records"),
    ):
        assert world.client.request(method, path).status_code == 401
        assert world.client.request(method, path, headers=bearer("not-a-real-token")).status_code == 401
    assert world.client.get("/api/admin/overview").headers["www-authenticate"] == "Bearer"


def test_non_admins_get_the_simulation_counts_without_the_true_effects(world: World) -> None:
    body = {"effects": [{"kind": "teacher", "key": "Dr. One", "value": 6}], "weeks": 4, "seed": 1}
    world.client.put("/api/simulation", json=body, headers=world.headers["admin"])
    admin = world.client.get("/api/simulation", headers=world.headers["admin"]).json()
    teacher = world.client.get("/api/simulation", headers=world.headers["teacher1"]).json()
    assert admin["effects"] and admin["weeks"] == 4
    assert teacher["effects"] == [] and teacher["archetypes"] == {} and teacher["weeks"] == 4


def test_a_teacher_uploads_marks_only_for_themselves(world: World) -> None:
    files = {"file": ("m.csv", b"register_no,marks,max_marks\nSTU001,5,10\n", "text/csv")}
    with db(world.client) as session:
        subject_id = session.query(Subject.id).scalar()
    form = {"subject_id": str(subject_id), "test_date": "2026-03-02", "name": "Quiz"}
    own = world.client.post(
        "/api/marks/tests", files=files, data={**form, "teacher_id": str(world.teacher1)}, headers=world.headers["teacher1"]
    )
    assert own.status_code == 200 and own.json()["applied"] is True
    other = world.client.post(
        "/api/marks/tests", files=files, data={**form, "teacher_id": str(world.teacher1)}, headers=world.headers["teacher2"]
    )
    assert other.status_code == 403
    student = world.client.post(
        "/api/marks/tests", files=files, data={**form, "teacher_id": str(world.teacher1)}, headers=world.headers["student1"]
    )
    assert student.status_code == 403


def test_the_timetable_upload_is_for_admins_and_the_service_key(world: World) -> None:
    csv = b"day,start,end,room,section,subject,teacher\nMon,10:00,10:50,R101,CSE-A,Physics,Dr. One\n"
    for actor, expected in (("admin", 200), ("service", 200), ("teacher1", 403), ("student1", 403), ("nobody", 401)):
        resp = world.client.post(
            "/api/timetable", files={"file": ("t.csv", csv, "text/csv")}, headers=world.headers[actor]
        )
        assert resp.status_code == expected, actor


# ---- the service key ----------------------------------------------------------------------------------------

RECORD = {
    "camera_id": "CAM1",
    "student_id": "STU001",
    "window_start": "2026-03-02T10:00:00+00:00",
    "attentive": 20,
    "neutral": 0,
    "distracted": 0,
    "not_visible": 10,
}


def test_the_service_key_sends_records_and_a_wrong_key_does_not(secure_client: TestClient) -> None:
    ok = secure_client.post("/api/records", json=[RECORD], headers={"X-Attentio-Key": SERVICE_KEY})
    assert ok.status_code == 200 and ok.json()["inserted"] == 1
    for wrong in ("wrong-key", SERVICE_KEY + "x", SERVICE_KEY[:-1], ""):
        resp = secure_client.post("/api/records", json=[RECORD], headers={"X-Attentio-Key": wrong})
        assert resp.status_code == 401
    assert secure_client.post("/api/records", json=[RECORD]).status_code == 401


def test_the_service_key_does_not_open_the_dashboard_endpoints(secure_client: TestClient) -> None:
    headers = {"X-Attentio-Key": SERVICE_KEY}
    assert secure_client.get("/api/students/STU001/profile", headers=headers).status_code == 403
    assert secure_client.get("/api/admin/overview", headers=headers).status_code == 403
    assert secure_client.get("/api/auth/me", headers=headers).status_code == 403


def test_the_service_key_creates_only_simulated_student_and_teacher_accounts(world: World) -> None:
    key = world.headers["service"]
    add_student(world.client, "SIM-A01")
    body = {
        "email": "sim-a01@attentio.test",
        "password": "demo-password-123",
        "role": "student",
        "student_register_no": "SIM-A01",
    }
    assert world.client.post("/api/accounts", json=body, headers=key).status_code == 403  # not simulated
    assert world.client.post("/api/accounts", json={**body, "simulated": True}, headers=key).status_code == 201
    admin = {"email": "sim-admin@attentio.test", "password": "demo-password-123", "role": "admin", "simulated": True}
    assert world.client.post("/api/accounts", json=admin, headers=key).status_code == 403
    assert world.client.get("/api/accounts", headers=key).status_code == 403


def test_with_no_service_key_configured_only_an_admin_token_passes(
    make_secure_client: ClientFactory, clock: FakeClock
) -> None:
    with make_secure_client(service_key="") as client:
        for header in ({"X-Attentio-Key": SERVICE_KEY}, {"X-Attentio-Key": ""}):
            assert client.post("/api/records", json=[RECORD], headers=header).status_code == 401
        add_account(client, "admin@example.test", "admin")
        token = sign_in(client, "admin@example.test")["token"]
        assert client.post("/api/records", json=[RECORD], headers=bearer(token)).status_code == 200


def test_the_open_app_used_by_the_logic_tests_checks_nothing(client: TestClient) -> None:
    assert client.get("/api/admin/overview").status_code == 200
    assert client.get("/api/students/STU001/profile").status_code == 200
    assert client.post("/api/records", json=[]).status_code == 200


def test_the_production_entry_point_enforces_sign_in_by_default() -> None:
    import inspect

    from backend.main import create_app

    assert inspect.signature(create_app).parameters["auth_required"].default is True
    entry = (Path(__file__).resolve().parent.parent / "backend" / "__main__.py").read_text(encoding="utf-8")
    assert "auth_required=False" not in entry  # python -m backend never turns the checks off
