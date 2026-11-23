"""The sign-in page, signing in and out, and an expired session, with the backend calls faked."""

from html import unescape
from typing import Any

import pytest
import requests
from streamlit.testing.v1 import AppTest

from dashboard import api, session, signin, theme
from dashboard.api import SignInError
from dashboard.session import SessionExpired
from tests.test_dashboard_render import (  # noqa: F401  (fixtures and data shared with the view tests)
    APP,
    IDENTITIES,
    fake_backend,
    html_of,
    run_app,
)

LOGINS: dict[str, dict[str, Any]] = {
    "student": {**IDENTITIES["student"], "token": "tok-student"},
    "teacher": {**IDENTITIES["teacher"], "token": "tok-teacher"},
    "admin": {**IDENTITIES["admin"], "token": "tok-admin"},
}


def first_page() -> AppTest:
    """The app with nobody signed in."""
    return AppTest.from_file(APP, default_timeout=20).run()


def sign_in_as(at: AppTest, email: str, password: str) -> AppTest:
    at.text_input(key=signin.EMAIL_KEY).input(email)
    at.text_input(key=signin.PASSWORD_KEY).input(password)
    at.button[0].click()
    return at.run()


def fake_login(monkeypatch: pytest.MonkeyPatch, accounts: dict[str, str]) -> list[tuple[str, str]]:
    """`accounts` maps an email to a role. Returns the (email, password) pairs that were tried."""
    tried: list[tuple[str, str]] = []

    def login(url: str, email: str, password: str) -> dict[str, Any]:
        tried.append((email, password))
        if email not in accounts or password != "right-password":
            raise SignInError("invalid")
        body = {**LOGINS[accounts[email]], "email": email}
        return body

    monkeypatch.setattr(api, "login", login)
    return tried


# ---- the page ------------------------------------------------------------------------------------------------


def test_before_sign_in_only_the_sign_in_page_is_shown(fake_backend: dict[str, list[Any]]) -> None:  # noqa: F811
    at = first_page()
    assert not at.exception
    page = html_of(at)
    assert "Sign in to Attentio" in page
    assert "Use the email and password your department gave you" in page
    assert [t.label for t in at.text_input] == ["Email", "Password"]
    assert [b.label for b in at.button] == ["Sign in"]
    assert "Forgot your password? Your department admin can reset it" in page
    assert "You'll land in your own view" in unescape(page)
    assert "Students see their own attention and marks, teachers their lectures, and admins the department overview." in page
    # none of the signed-in page: no sidebar, no views
    assert not list(at.sidebar) or not [el for el in at.sidebar.get("button")]
    assert "Teacher feedback" not in page and "Your attention, live" not in page


def test_there_is_one_form_for_everyone_no_role_cards_and_no_role_wording(fake_backend: dict[str, list[Any]]) -> None:  # noqa: F811
    at = first_page()
    page = html_of(at)
    for word in ("Register number", "Staff username", "Admin username", "Sign in as", "I am signing in as"):
        assert word not in page
    assert not at.get("button_group") and not list(at.selectbox) and not list(at.radio)
    assert not [b for b in at.button if "Sign in as" in b.label]


def test_the_left_panel_follows_the_design_copy(fake_backend: dict[str, list[Any]]) -> None:  # noqa: F811
    page = html_of(first_page())
    for text in (
        "Attentio",
        "Department of Computer Science",
        "See how each lecture lands, minute by minute",
        "Attention from the classroom camera, joined to your timetable and test marks. Students, teachers and "
        "administrators each see only what their role allows.",
        "Data Structures · Room R101",
        "Seconds in each state, per 30-second window",
        "Live",
        "Video is processed on the classroom machine and never stored. Only 30-second summaries reach this dashboard.",
    ):
        assert text in page
    for state in theme.STATE_COLOURS:
        assert state in page


def test_the_preview_is_a_static_stacked_chart_in_the_four_state_colours() -> None:
    markup = signin.preview_html()
    assert markup.count('class="cp-col"') == len(signin.PREVIEW_COLUMNS) == 22
    for colour in theme.STATE_COLOURS.values():
        assert f"background:{colour}" in markup
    assert all(sum(col) <= signin.PREVIEW_HEIGHT for col in signin.PREVIEW_COLUMNS)
    assert 'aria-hidden="true"' in markup  # decorative, not data


def test_the_wordmark_is_a_blue_square_with_four_bars() -> None:
    markup = signin.mark_html()
    assert markup.count("<span style=") == 4 and "Attentio" in markup


def test_the_sign_in_copy_has_no_exclamation_marks_emoji_or_final_periods_on_labels() -> None:
    labels = [
        signin.TITLE,
        signin.SUBTITLE,
        signin.BUTTON,
        signin.FORGOT,
        signin.NOTE_TITLE,
        signin.EYEBROW,
        signin.HEADLINE,
        signin.PREVIEW_TITLE,
        signin.PREVIEW_SUBTITLE,
        *signin.ERRORS.values(),
    ]
    text = " ".join([*labels, signin.NOTE_TEXT, signin.SUPPORTING, signin.PRIVACY])
    assert "!" not in text and not any(ch in text for ch in "😀👍✅")
    assert not [label for label in labels if label.endswith(".")]


def test_the_page_is_escaped_markup_not_the_values_of_the_form(fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    fake_login(monkeypatch, {})
    at = sign_in_as(first_page(), "<b>x</b>@example.edu", "right-password")
    assert "<b>x</b>" not in html_of(at)


# ---- failing ------------------------------------------------------------------------------------------------


def test_a_wrong_password_shows_the_same_inline_error_for_any_account(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    fake_login(monkeypatch, {"asha@example.edu": "student"})
    for email in ("asha@example.edu", "nobody@example.edu"):
        at = sign_in_as(first_page(), email, "wrong-password")
        assert not at.exception
        assert [e.value for e in at.error] == ["Email or password is incorrect"]
        assert [t.label for t in at.text_input] == ["Email", "Password"]  # still the sign-in page
        assert session.AUTH_KEY not in at.session_state


def test_too_many_attempts_and_an_unreachable_backend_have_their_own_plain_messages(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    for kind, message in (
        ("rate_limited", "Too many attempts. Try again in a few minutes"),
        ("unreachable", "The backend is waking up (free hosting sleeps when idle). Refresh in a moment"),
    ):
        def login(url: str, email: str, password: str, kind: str = kind) -> dict[str, Any]:
            raise SignInError(kind)

        monkeypatch.setattr(api, "login", login)
        at = sign_in_as(first_page(), "asha@example.edu", "whatever-1")
        assert [e.value for e in at.error] == [message]


def test_an_empty_form_asks_for_both_without_calling_the_backend(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    tried = fake_login(monkeypatch, {})
    at = sign_in_as(first_page(), "", "")
    assert [e.value for e in at.error] == ["Enter your email and password"]
    assert tried == []


# ---- succeeding ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "email", "marker"),
    [
        ("student", "asha@example.edu", "Student · STU001"),
        ("teacher", "rao@example.edu", "Teacher · Dr Rao"),
        ("admin", "admin@example.edu", "Teacher feedback, all teachers"),
    ],
)
def test_signing_in_lands_in_the_view_of_the_accounts_own_role(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch, role: str, email: str, marker: str  # noqa: F811
) -> None:
    fake_login(monkeypatch, {email: role})
    at = sign_in_as(first_page(), email, "right-password")
    assert not at.exception
    page = html_of(at)
    assert marker in page
    assert "Sign in to Attentio" not in page
    # no role switcher, no picker; who is signed in, and Sign out, are in the sidebar
    assert not at.sidebar.get("button_group")
    assert "role" not in at.session_state
    assert "teacher_name" not in at.session_state and "student_register_no" not in at.session_state
    assert [b.key for b in at.sidebar.button if b.key == "sign_out"] == ["sign_out"]
    stored = at.session_state[session.AUTH_KEY]
    assert stored["role"] == role and stored["token"] == LOGINS[role]["token"]


def test_the_email_is_trimmed_and_the_password_is_not_kept_after_signing_in(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    tried = fake_login(monkeypatch, {"asha@example.edu": "student"})
    at = sign_in_as(first_page(), "  asha@example.edu ", "right-password")
    assert tried == [("asha@example.edu", "right-password")]
    assert signin.PASSWORD_KEY not in at.session_state
    held = {key: at.session_state[key] for key in at.session_state}
    assert "right-password" not in str(held)


def test_the_page_never_puts_the_token_in_the_url(fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    fake_login(monkeypatch, {"asha@example.edu": "student"})
    at = sign_in_as(first_page(), "asha@example.edu", "right-password")
    assert dict(at.query_params) == {}


# ---- signing out and expiry ---------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["student", "teacher", "admin"])
def test_signing_out_returns_to_the_sign_in_page_and_forgets_everything(
    fake_backend: dict[str, list[Any]], role: str  # noqa: F811
) -> None:
    at = run_app(role)
    at.session_state["leftover"] = "from the last person"
    at.sidebar.button(key="sign_out").click().run()
    assert not at.exception
    assert fake_backend["logout"] == ["http://127.0.0.1:8000"] or len(fake_backend["logout"]) == 1
    assert [t.label for t in at.text_input] == ["Email", "Password"]
    assert session.AUTH_KEY not in at.session_state and "leftover" not in at.session_state
    assert "Sign in to Attentio" in html_of(at)


def test_an_expired_session_returns_to_sign_in_with_a_message_in_every_view(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    def expired(*args: Any) -> Any:
        raise SessionExpired()

    from dashboard.views import student

    monkeypatch.setattr(student, "get_current", expired)  # a fragment of the student view
    monkeypatch.setattr(api, "get_teacher_feedback", expired)
    monkeypatch.setattr(api, "get_admin_overview", expired)
    for role in ("student", "teacher", "admin"):
        at = run_app(role)
        assert not at.exception, role
        assert [t.label for t in at.text_input] == ["Email", "Password"]
        assert session.AUTH_KEY not in at.session_state
        assert [i.value for i in at.info] == ["Your session ended. Sign in again"]
        # and the message is shown once: a rerun does not repeat it
        assert not at.run().info


def test_after_an_expired_session_the_person_can_sign_in_again(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from dashboard.views import student

    calls = {"n": 0}

    def flaky(url: str, reg: str) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise SessionExpired()
        return student_current

    from tests.test_dashboard_render import NO_CURRENT as student_current

    monkeypatch.setattr(student, "get_current", flaky)
    fake_login(monkeypatch, {"asha@example.edu": "student"})
    at = run_app("student")
    assert [t.label for t in at.text_input] == ["Email", "Password"]
    at = sign_in_as(at, "asha@example.edu", "right-password")
    assert "Student · STU001" in html_of(at)


# ---- the api module ----------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


def test_every_call_sends_the_bearer_token_and_a_401_means_the_session_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    status = {"code": 200}

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        seen.append(kwargs)
        return FakeResponse(status["code"], [])

    monkeypatch.setattr(requests, "request", request)
    monkeypatch.setattr(session, "token", lambda: "the-token")
    assert api.list_teachers("http://b") == []
    assert seen[0]["headers"] == {"Authorization": "Bearer the-token"}
    status["code"] = 401
    with pytest.raises(SessionExpired):
        api.list_teachers("http://b")
    status["code"] = 403
    with pytest.raises(api.BackendError):
        api.list_teachers("http://b")  # a 403 is "not allowed", not "signed out"


def test_without_a_token_no_header_is_sent_and_a_401_is_an_ordinary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        seen.append(kwargs)
        return FakeResponse(401, {"detail": "Sign in required"})

    monkeypatch.setattr(requests, "request", request)
    monkeypatch.setattr(session, "token", lambda: None)
    with pytest.raises(api.BackendError, match="Sign in required"):
        api.list_teachers("http://b")
    assert seen[0]["headers"] == {}


def test_login_maps_the_backends_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    answers = iter([FakeResponse(200, {"token": "t"}), FakeResponse(401), FakeResponse(429), FakeResponse(500)])

    def post(url: str, **kwargs: Any) -> FakeResponse:
        sent.append({"url": url, **kwargs})
        return next(answers)

    monkeypatch.setattr(requests, "post", post)
    assert api.login("http://b/", "a@x.test", "pw")["token"] == "t"
    assert sent[0]["url"] == "http://b/api/auth/login" and sent[0]["json"] == {"email": "a@x.test", "password": "pw"}
    assert "headers" not in sent[0]  # signing in sends no token
    for kind in ("invalid", "rate_limited", "error"):
        with pytest.raises(SignInError) as err:
            api.login("http://b", "a@x.test", "pw")
        assert err.value.kind == kind

    def down(url: str, **kwargs: Any) -> FakeResponse:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", down)
    with pytest.raises(SignInError) as err:
        api.login("http://b", "a@x.test", "pw")
    assert err.value.kind == "unreachable" and "refused" not in str(err.value)


def test_logout_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "request", request)
    api.logout("http://b")  # the token expires anyway
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(204))
    monkeypatch.setattr(session, "token", lambda: "t")
    api.logout("http://b")
