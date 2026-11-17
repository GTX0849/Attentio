"""Who is signed in, kept in `st.session_state` (never in the URL).

The token lives only in the browser session's server-side state. A 401 on any call after sign-in
means the session ended: `ends_on_expiry` turns that into a return to the sign-in page.
"""

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import streamlit as st

AUTH_KEY = "auth"
NOTICE_KEY = "signin_notice"
SESSION_ENDED = "Your session ended. Sign in again"

F = TypeVar("F", bound=Callable[..., Any])


class SessionExpired(Exception):
    """The backend answered 401 to a signed-in call. Not a BackendError, so views do not swallow it."""


@dataclass(frozen=True)
class Identity:
    token: str = ""
    role: str = ""  # "student", "teacher" or "admin"
    email: str = ""
    display_name: str = ""
    student_register_no: str | None = None
    teacher_id: int | None = None


def current() -> Identity | None:
    """The signed-in identity, or None on the sign-in page."""
    stored = st.session_state.get(AUTH_KEY)
    return Identity(**stored) if stored else None


def token() -> str | None:
    stored = st.session_state.get(AUTH_KEY)
    return str(stored["token"]) if stored else None


def start(identity: Identity) -> None:
    st.session_state[AUTH_KEY] = {
        "token": identity.token,
        "role": identity.role,
        "email": identity.email,
        "display_name": identity.display_name,
        "student_register_no": identity.student_register_no,
        "teacher_id": identity.teacher_id,
    }


def end(notice: str | None = None) -> None:
    """Forget everything this browser session held (the next person starts clean) and keep `notice`."""
    st.session_state.clear()
    if notice:
        st.session_state[NOTICE_KEY] = notice


def ends_on_expiry(fn: F) -> F:
    """Wrap a render function: when the backend says the session ended, go back to sign-in.

    Works for fragments too (they rerun alone, so the whole app is rerun).
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except SessionExpired:
            end(SESSION_ENDED)
            st.rerun(scope="app")

    return wrapper  # type: ignore[return-value]
