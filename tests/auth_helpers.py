"""Helpers for tests that run the backend with sign-in enforced."""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.accounts import create_account
from backend.models import Student, Teacher

PASSWORD = "a-long-test-password"


def db(client: TestClient) -> Session:
    return Session(client.app.state.engine)  # type: ignore[attr-defined]


def add_student(client: TestClient, register_no: str, name: str | None = None) -> None:
    with db(client) as session:
        session.add(Student(register_no=register_no, name=name))
        session.commit()


def add_teacher(client: TestClient, name: str) -> int:
    with db(client) as session:
        teacher = Teacher(name=name)
        session.add(teacher)
        session.commit()
        return teacher.id


def add_account(
    client: TestClient,
    email: str,
    role: str,
    *,
    student: str | None = None,
    teacher_id: int | None = None,
    password: str = PASSWORD,
    simulated: bool = False,
) -> None:
    with db(client) as session:
        create_account(
            session,
            email=email,
            password=password,
            role=role,
            now=datetime.now(UTC),
            student_register_no=student,
            teacher_id=teacher_id,
            simulated=simulated,
        )


def sign_in(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
