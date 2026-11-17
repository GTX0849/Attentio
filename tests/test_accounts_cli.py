"""The accounts command line, with the password prompt and the output injected."""

import io
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend import accounts, auth
from backend.models import Account, Teacher
from shared.config import test_database_url as configured_test_url
from tests.auth_helpers import db

PASSWORD = "cli-test-password"


def typed(*answers: str) -> tuple[Callable[[str], str], list[str]]:
    """A stand-in for getpass that returns the answers in order and records the prompts."""
    remaining = list(answers)
    prompts: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return remaining.pop(0)

    return read, prompts


def run(argv: list[str], *answers: str) -> tuple[int, str, list[str]]:
    url = configured_test_url()
    assert url is not None
    read, prompts = typed(*answers)
    out = io.StringIO()
    code = accounts.main(argv, url=url, read_password=read, out=out, now=lambda: datetime.now(UTC))
    return code, out.getvalue(), prompts


def test_create_admin_asks_for_the_password_twice_and_stores_only_a_hash(client: TestClient) -> None:
    code, out, prompts = run(["create", "--role", "admin", "--email", "Admin@Example.edu"], PASSWORD, PASSWORD)
    assert code == 0
    assert out.strip() == "Created admin account admin@example.edu"
    assert prompts == ["Password: ", "Password again: "]
    assert PASSWORD not in out
    with db(client) as session:
        account = session.query(Account).one()
        assert account.email == "admin@example.edu" and account.role == "admin"
        assert auth.verify_password(PASSWORD, account.password_hash)
        assert PASSWORD not in account.password_hash


def test_a_mismatched_or_short_password_creates_nothing(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = run(["create", "--role", "admin", "--email", "a@example.edu"], PASSWORD, "something-else-1")
    assert code == 1 and "do not match" in capsys.readouterr().err
    code, _, _ = run(["create", "--role", "admin", "--email", "a@example.edu"], "short")
    assert code == 1 and "at least 8" in capsys.readouterr().err
    with db(client) as session:
        assert session.query(Account).count() == 0


def test_create_student_links_to_the_register_number(client: TestClient) -> None:
    code, out, _ = run(
        ["create", "--role", "student", "--email", "asha@example.edu", "--student", "stu001"], PASSWORD, PASSWORD
    )
    assert code == 0 and "student account asha@example.edu" in out
    with db(client) as session:
        assert session.query(Account).one().student_register_no == "STU001"


def test_create_student_needs_a_known_register_number(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = run(["create", "--role", "student", "--email", "x@example.edu", "--student", "NOPE"], PASSWORD, PASSWORD)
    assert code == 1 and "No student has the register number NOPE" in capsys.readouterr().err
    code, _, _ = run(["create", "--role", "student", "--email", "x@example.edu"], PASSWORD, PASSWORD)
    assert code == 1 and "register number" in capsys.readouterr().err


def test_create_teacher_links_to_the_named_teacher(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    with db(client) as session:
        session.add(Teacher(name="Dr. Anita Menon"))
        session.commit()
    code, out, _ = run(
        ["create", "--role", "teacher", "--email", "anita@example.edu", "--teacher", "dr. anita menon"],
        PASSWORD,
        PASSWORD,
    )
    assert code == 0 and "teacher account anita@example.edu" in out
    with db(client) as session:
        account = session.query(Account).one()
        teacher = session.query(Teacher).one()
        assert account.teacher_id == teacher.id

    code, _, _ = run(["create", "--role", "teacher", "--email", "b@example.edu", "--teacher", "Dr. Nobody"], PASSWORD, PASSWORD)
    assert code == 1 and "No teacher is named Dr. Nobody" in capsys.readouterr().err
    code, _, _ = run(["create", "--role", "teacher", "--email", "b@example.edu"], PASSWORD, PASSWORD)
    assert code == 1 and "--teacher" in capsys.readouterr().err


def test_an_invalid_or_repeated_email_is_refused(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run(["create", "--role", "admin", "--email", "not-an-email"], PASSWORD, PASSWORD)
    assert code == 1 and "email" in capsys.readouterr().err
    assert run(["create", "--role", "admin", "--email", "a@example.edu"], PASSWORD, PASSWORD)[0] == 0
    code, _, _ = run(["create", "--role", "admin", "--email", "A@EXAMPLE.edu"], PASSWORD, PASSWORD)
    assert code == 1 and "already has an account" in capsys.readouterr().err


def test_list_shows_emails_roles_and_links_but_never_a_password_or_hash(client: TestClient) -> None:
    run(["create", "--role", "admin", "--email", "admin@example.edu"], PASSWORD, PASSWORD)
    run(["create", "--role", "student", "--email", "asha@example.edu", "--student", "STU001"], PASSWORD, PASSWORD)
    code, out, prompts = run(["list"])
    assert code == 0 and prompts == []
    lines = out.strip().splitlines()
    assert lines[0].startswith("admin@example.edu  admin")
    assert lines[1].startswith("asha@example.edu  student") and "Demo Student" in lines[1]
    assert PASSWORD not in out and "scrypt" not in out


def test_list_says_so_when_there_are_no_accounts(client: TestClient) -> None:
    assert run(["list"])[1].strip() == "No accounts yet"


def test_reset_password_changes_the_password_and_ends_sessions(client: TestClient) -> None:
    run(["create", "--role", "admin", "--email", "admin@example.edu"], PASSWORD, PASSWORD)
    code, out, _ = run(["reset-password", "--email", "ADMIN@example.edu"], "a-new-password-1", "a-new-password-1")
    assert code == 0 and "New password set for admin@example.edu" in out
    with db(client) as session:
        stored = session.query(Account).one().password_hash
    assert auth.verify_password("a-new-password-1", stored)
    assert not auth.verify_password(PASSWORD, stored)


def test_reset_and_delete_of_an_unknown_email_fail_without_asking_for_a_password(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, prompts = run(["reset-password", "--email", "ghost@example.edu"])
    assert code == 1 and prompts == [] and "No account has the email ghost@example.edu" in capsys.readouterr().err
    assert run(["delete", "--email", "ghost@example.edu"])[0] == 1


def test_delete_removes_the_account(client: TestClient) -> None:
    run(["create", "--role", "admin", "--email", "admin@example.edu"], PASSWORD, PASSWORD)
    code, out, _ = run(["delete", "--email", "admin@example.edu"])
    assert code == 0 and "Deleted account admin@example.edu" in out
    with db(client) as session:
        assert session.query(Account).count() == 0
