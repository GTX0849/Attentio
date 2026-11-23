"""The simulator makes simulated accounts for its students and teachers, authenticates with the service
key, and the wipe removes the accounts."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.models import Account
from simulator import client as simulator_client
from simulator.client import Backend, SimulatorError, seed, wipe
from simulator.config import load_simulator_config
from simulator.credentials import credentials_text, write_credentials
from tests.auth_helpers import db, sign_in
from tests.conftest import SERVICE_KEY, ClientFactory, FakeClock

NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)


def test_a_seed_creates_a_simulated_account_for_every_simulated_student_and_teacher(
    client: TestClient, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ATTENTIO_DEMO_PASSWORD", raising=False)
    clock.set(NOW)
    config = replace(load_simulator_config(small=True), records_per_request=1000)
    report = seed(Backend(client), config, now=NOW)
    assert len(report.accounts) == 12 + 5  # students and teachers
    emails = {a.email for a in report.accounts}
    assert "sim-a01@attentio.test" in emails and "anita.menon@attentio.test" in emails
    assert all(e.endswith("@attentio.test") for e in emails)
    with db(client) as session:
        rows = session.query(Account).all()
        assert len(rows) == 17 and all(a.simulated for a in rows)
        assert {a.role for a in rows} == {"student", "teacher"}
        teacher = next(a for a in rows if a.email == "anita.menon@attentio.test")
        assert teacher.teacher_id is not None
        student = next(a for a in rows if a.email == "sim-a01@attentio.test")
        assert student.student_register_no == "SIM-A01"
        assert all(report.demo_password not in a.password_hash for a in rows)


def test_the_wipe_removes_the_simulated_accounts_but_not_live_ones(
    client: TestClient, clock: FakeClock
) -> None:
    from backend.accounts import create_account

    clock.set(NOW)
    with db(client) as session:
        create_account(session, email="admin@example.test", password="a-live-password", role="admin", now=NOW)
    config = replace(load_simulator_config(small=True), records_per_request=1000)
    backend = Backend(client)
    seed(backend, config, now=NOW)
    removed = wipe(backend)
    assert removed["accounts"] == 17
    with db(client) as session:
        assert [a.email for a in session.query(Account).all()] == ["admin@example.test"]


def test_a_new_password_is_made_for_each_run_unless_one_is_set(
    client: TestClient, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the lookup itself: the real .env (re-read on every call) may set ATTENTIO_DEMO_PASSWORD.
    monkeypatch.setattr(simulator_client, "configured_demo_password", lambda: None)
    clock.set(NOW)
    config = replace(load_simulator_config(small=True), weeks=1, records_per_request=1000)
    first = seed(Backend(client), config, now=NOW)
    second = seed(Backend(client), config, now=NOW)
    assert first.password_generated and second.password_generated
    assert first.demo_password != second.demo_password and len(first.demo_password) >= 12

    monkeypatch.setattr(simulator_client, "configured_demo_password", lambda: "chosen-by-the-person")
    third = seed(Backend(client), config, now=NOW)
    assert third.demo_password == "chosen-by-the-person" and not third.password_generated


def test_simulated_accounts_sign_in_with_the_demo_password_and_the_simulator_uses_the_service_key(
    make_secure_client: ClientFactory, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ATTENTIO_DEMO_PASSWORD", raising=False)
    clock.set(NOW)
    config = replace(load_simulator_config(small=True), records_per_request=1000)
    with make_secure_client() as client:
        with pytest.raises(SimulatorError, match="ATTENTIO_SERVICE_KEY"):
            seed(Backend(client), config, now=NOW)  # no key sent: refused
        with pytest.raises(SimulatorError, match="service key is wrong"):
            seed(Backend(client, service_key="wrong"), config, now=NOW)

        report = seed(Backend(client, service_key=SERVICE_KEY), config, now=NOW)
        body = sign_in(client, "sim-a01@attentio.test", report.demo_password)
        assert body["role"] == "student" and body["student_register_no"] == "SIM-A01"
        teacher = sign_in(client, "anita.menon@attentio.test", report.demo_password)
        assert teacher["role"] == "teacher" and teacher["display_name"] == "Dr. Anita Menon"


def test_the_credentials_file_lists_emails_and_the_password_only_when_it_was_generated(tmp_path: Path) -> None:
    from simulator.client import DemoAccount

    accounts = (
        DemoAccount("anita.menon@attentio.test", "teacher", "Dr. Anita Menon"),
        DemoAccount("sim-a01@attentio.test", "student", "Aarav Sharma (SIM-A01)"),
    )
    generated = credentials_text(accounts, "Zx9-generated", True)
    assert "Zx9-generated" in generated and "anita.menon@attentio.test  teacher" in generated
    chosen = credentials_text(accounts, "my-own-password", False)
    assert "my-own-password" not in chosen and "ATTENTIO_DEMO_PASSWORD" in chosen
    path = write_credentials(accounts, "Zx9-generated", True, tmp_path / "nested" / "demo-credentials.txt")
    assert path.read_text(encoding="utf-8") == generated


def test_the_credentials_file_lives_in_the_git_ignored_spool_folder() -> None:
    from shared.config import PROJECT_ROOT
    from simulator.credentials import CREDENTIALS_PATH

    assert CREDENTIALS_PATH == PROJECT_ROOT / ".spool" / "demo-credentials.txt"
    assert ".spool/" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
