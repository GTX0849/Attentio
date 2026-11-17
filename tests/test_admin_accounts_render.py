"""The Accounts section of the Admin view: list by email, create by email, role and link, reset a password."""

from typing import Any

import pytest

from dashboard import api
from dashboard.api import BackendError
from tests.test_dashboard_render import (  # noqa: F401
    STUDENT,
    fake_backend,
    html_of,
    run_app,
)


def press(at: Any, label: str) -> None:
    """Click the form button with this label and rerun."""
    next(b for b in at.button if b.label == label).click()
    at.run()


def accounts_page() -> Any:
    at = run_app("admin")
    assert not at.exception
    return at


def test_accounts_are_listed_by_email_with_role_and_link_and_never_a_password(
    fake_backend: dict[str, list[Any]],  # noqa: F811
) -> None:
    at = accounts_page()
    assert "Accounts" in [s.value for s in at.subheader]
    frame = at.dataframe[-1].value
    assert list(frame.columns) == ["Email", "Role", "Linked to", "Created", "Demo"]
    assert list(frame["Email"]) == ["admin@example.edu", "rao@example.edu"]
    assert list(frame["Role"]) == ["Admin", "Teacher"]
    assert list(frame["Linked to"]) == ["", "Dr Rao"]
    assert "password" not in " ".join(frame.columns).lower()
    assert "People sign in with their email. Passwords are never shown, only set." in [c.value for c in at.caption]


def test_an_admin_creates_a_student_account_by_email_role_and_link(
    fake_backend: dict[str, list[Any]],  # noqa: F811
) -> None:
    at = accounts_page()
    assert at.segmented_control(key="account_role").value == "student"
    at.text_input(key="account_email").input("Asha@Example.edu")
    at.selectbox(key="account_link").select("STU001 · Demo Student")
    at.text_input(key="account_password").input("initial-pass-1")
    press(at, "Create account")
    assert not at.exception
    assert fake_backend["accounts"] == [("Asha@Example.edu", "student", "STU001", None)]
    assert any("Account created for asha@example.edu" in s.value for s in at.success)


def test_creating_a_teacher_account_links_the_teacher_id_and_an_admin_account_links_nothing(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.setattr(api, "list_teachers", lambda url: [{"id": 1, "name": "Dr Rao"}, {"id": 2, "name": "Dr Sim"}])
    at = accounts_page()
    at.segmented_control(key="account_role").set_value("teacher").run()
    assert at.selectbox(key="account_link").options == ["Dr Sim"]  # Dr Rao already has an account
    at.text_input(key="account_email").input("sim@example.edu")
    at.selectbox(key="account_link").select("Dr Sim")
    at.text_input(key="account_password").input("initial-pass-1")
    press(at, "Create account")
    assert fake_backend["accounts"] == [("sim@example.edu", "teacher", None, 2)]

    at.segmented_control(key="account_role").set_value("admin").run()
    assert not [s for s in at.selectbox if s.key == "account_link"]  # nothing to link
    at.text_input(key="account_email").input("boss@example.edu")
    at.text_input(key="account_password").input("initial-pass-1")
    press(at, "Create account")
    assert fake_backend["accounts"][-1] == ("boss@example.edu", "admin", None, None)


def test_only_students_without_an_account_can_be_linked(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    linked = {
        "id": 3, "email": "asha@example.edu", "role": "student", "student_register_no": "STU001",
        "teacher_id": None, "linked_name": "Demo Student", "created_at": "2026-03-01T09:00:00Z", "simulated": False,
    }  # fmt: skip
    monkeypatch.setattr(api, "list_accounts", lambda url: [linked])
    monkeypatch.setattr(api, "list_students", lambda url: [STUDENT, {**STUDENT, "register_no": "STU002", "name": None}])
    at = accounts_page()
    assert at.selectbox(key="account_link").options == ["STU002"]


def test_a_refused_account_shows_the_backends_message(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    def refuse(*args: Any) -> dict[str, Any]:
        raise BackendError("The email asha@example.edu already has an account")

    monkeypatch.setattr(api, "create_account", refuse)
    at = accounts_page()
    at.text_input(key="account_email").input("asha@example.edu")
    at.selectbox(key="account_link").select("STU001 · Demo Student")
    at.text_input(key="account_password").input("initial-pass-1")
    press(at, "Create account")
    assert any("already has an account" in e.value for e in at.error)


def test_an_admin_resets_a_password_for_a_chosen_account(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(api, "reset_account_password", lambda url, account_id, password: sent.append((account_id, password)))
    at = accounts_page()
    assert at.selectbox(key="reset_account").options == ["admin@example.edu", "rao@example.edu"]
    at.selectbox(key="reset_account").select("rao@example.edu")
    at.text_input(key="reset_password").input("a-new-password-9")
    press(at, "Set new password")
    assert not at.exception
    assert sent == [(2, "a-new-password-9")]
    assert any("New password set for rao@example.edu" in s.value for s in at.success)
    assert "a-new-password-9" not in html_of(at)


def test_a_student_or_teacher_view_never_shows_accounts(fake_backend: dict[str, list[Any]]) -> None:  # noqa: F811
    for role in ("student", "teacher"):
        at = run_app(role)
        assert "Accounts" not in [s.value for s in at.subheader]
