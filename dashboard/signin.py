"""The sign-in page: the only thing shown before sign-in.

A split layout like the "Sign In Desktop" design: a brand panel on the left (wordmark, headline, a
static illustration, the privacy note) and the form on the right. There is one form for everyone:
the role comes from the account the backend finds.

The markup builders return strings so tests can check the copy without running Streamlit. The
illustration is a fixed, hard-coded sample and is decorative; it is not live data.
"""

from html import escape
from pathlib import Path

import streamlit as st

from dashboard import api, session
from dashboard.api import SignInError
from dashboard.theme import (
    BORDER,
    PRIMARY,
    SECONDARY_TEXT,
    STATE_COLOURS,
)
from shared.config import Config

EMAIL_KEY = "signin_email"
PASSWORD_KEY = "signin_password"
FORM_KEY = "signin_form"

TITLE = "Sign in to ClassPulse"
SUBTITLE = "Use the email and password your department gave you"
BUTTON = "Sign in"
FORGOT = "Forgot your password? Your department admin can reset it"
NOTE_TITLE = "You'll land in your own view"
NOTE_TEXT = (
    "Students see their own attention and marks, teachers their lectures, "
    "and admins the department overview."
)
EYEBROW = "Department of Computer Science"
HEADLINE = "See how each lecture lands, minute by minute"
SUPPORTING = (
    "Attention from the classroom camera, joined to your timetable and test marks. "
    "Students, teachers and administrators each see only what their role allows."
)
PREVIEW_TITLE = "Data Structures · Room R101"
PREVIEW_SUBTITLE = "Seconds in each state, per 30-second window"
PRIVACY = (
    "Video is processed on the classroom machine and never stored. "
    "Only 30-second summaries reach this dashboard."
)

ERRORS = {
    "invalid": "Email or password is incorrect",
    "rate_limited": "Too many attempts. Try again in a few minutes",
    "unreachable": "Cannot reach the ClassPulse backend. Check that it is running, then try again",
    "error": "Sign-in did not work. Try again in a moment",
    "empty": "Enter your email and password",
}

# The illustration: 22 columns of (not visible, distracted, neutral, attentive) seconds-as-pixels out of
# 150, copied from the design. Fixed on purpose: it is a picture of the product, not data.
PREVIEW_COLUMNS: list[tuple[int, int, int, int]] = [
    (0, 0, 20, 130), (0, 0, 25, 125), (0, 10, 30, 110), (0, 0, 15, 135), (0, 40, 20, 90),
    (0, 75, 15, 60), (0, 0, 30, 120), (0, 0, 10, 140), (150, 0, 0, 0), (0, 10, 25, 115),
    (0, 0, 25, 125), (0, 5, 40, 105), (0, 0, 20, 130), (0, 60, 20, 70), (0, 20, 20, 110),
    (0, 0, 15, 135), (0, 0, 10, 140), (0, 0, 25, 125), (0, 0, 35, 115), (0, 5, 15, 130),
    (0, 0, 10, 140), (0, 0, 15, 135),
]  # fmt: skip
PREVIEW_HEIGHT = 150

_CSS = Path(__file__).with_name("signin.css")


# ---- markup ------------------------------------------------------------------------------------------------


def mark_html() -> str:
    """The wordmark: a blue rounded square with four white bars, then the name."""
    bars = "".join(f'<span style="height:{h}px"></span>' for h in (9, 16, 11, 18))
    return f'<div class="cp-brand"><div class="cp-mark" aria-hidden="true">{bars}</div><span>ClassPulse</span></div>'


def preview_html() -> str:
    """The white card with a small static stacked-column chart and its legend."""
    order = ("Not visible", "Distracted", "Neutral", "Attentive")  # top to bottom
    columns = ""
    for values in PREVIEW_COLUMNS:
        segments = "".join(
            f'<div style="height:{px}px;background:{STATE_COLOURS[name]}"></div>'
            for name, px in zip(order, values, strict=True)
            if px
        )
        columns += f'<div class="cp-col">{segments}</div>'
    legend = "".join(
        f'<span class="cp-legend-item"><i style="background:{colour}"></i>{escape(name)}</span>'
        for name, colour in STATE_COLOURS.items()
    )
    return (
        '<div class="cp-card" aria-hidden="true">'
        '<div class="cp-card-head"><div>'
        f'<div class="cp-card-title">{escape(PREVIEW_TITLE)}</div>'
        f'<div class="cp-card-sub">{escape(PREVIEW_SUBTITLE)}</div></div>'
        f'<span class="cp-pill"><i style="background:{PRIMARY}"></i>Live</span></div>'
        f'<div class="cp-cols" style="height:{PREVIEW_HEIGHT}px">{columns}</div>'
        f'<div class="cp-legend">{legend}</div></div>'
    )


def brand_panel_html() -> str:
    return (
        '<div class="cp-left">'
        f"{mark_html()}"
        '<div class="cp-message">'
        f'<div class="cp-eyebrow">{escape(EYEBROW)}</div>'
        f'<h1 class="cp-headline">{escape(HEADLINE)}</h1>'
        f'<p class="cp-supporting">{escape(SUPPORTING)}</p></div>'
        f"{preview_html()}"
        f'<div class="cp-privacy"><span class="cp-lock" aria-hidden="true"></span><span>{escape(PRIVACY)}</span></div>'
        "</div>"
    )


def heading_html() -> str:
    return (
        '<div class="cp-form-head">'
        f"<h2>{escape(TITLE)}</h2><p>{escape(SUBTITLE)}</p></div>"
    )


def note_html() -> str:
    return (
        f'<div class="cp-note" style="border-top:1px solid {BORDER}">'
        f'<div class="cp-note-title">{escape(NOTE_TITLE)}</div>'
        f'<div class="cp-note-text">{escape(NOTE_TEXT)}</div></div>'
    )


def error_text(kind: str) -> str:
    return ERRORS.get(kind, ERRORS["error"])


# ---- page --------------------------------------------------------------------------------------------------


def _attempt(config: Config, email: str, password: str) -> str | None:
    """Try to sign in. Returns the kind of failure, or None after starting the session."""
    if not email.strip() or not password:
        return "empty"
    try:
        body = api.login(config.backend_url, email.strip(), password)
    except SignInError as e:
        return e.kind
    session.start(
        session.Identity(
            token=body["token"],
            role=body["role"],
            email=body["email"],
            display_name=body["display_name"],
            student_register_no=body.get("student_register_no"),
            teacher_id=body.get("teacher_id"),
        )
    )
    return None


def render(config: Config) -> None:
    """Draw the sign-in page. On success the session starts and the app reruns into the person's own view."""
    st.html(_CSS)  # a .css path: wrapped in <style>, takes no space
    left, right = st.columns([640, 800], gap=None)
    with left, st.container(key="signin_left"):
        st.html(brand_panel_html())
    with right, st.container(key="signin_right"), st.container(key="signin_form_box"):
        st.html(heading_html())
        notice = st.session_state.pop(session.NOTICE_KEY, None)
        failure: str | None = None
        with st.form(FORM_KEY, border=False, enter_to_submit=True):
            st.text_input("Email", key=EMAIL_KEY, autocomplete="username")
            st.text_input("Password", type="password", key=PASSWORD_KEY, autocomplete="current-password")
            slot = st.container()
            submitted = st.form_submit_button(BUTTON, type="primary", width="stretch")
        if submitted:
            failure = _attempt(config, str(st.session_state.get(EMAIL_KEY, "")), str(st.session_state.get(PASSWORD_KEY, "")))
            if failure is None:
                st.rerun()
        with slot:
            if failure is not None:
                st.error(error_text(failure), icon=":material/error:")
            elif notice:
                st.info(str(notice), icon=":material/info:")
        st.html(
            f'<div class="cp-forgot" style="color:{SECONDARY_TEXT}">{escape(FORGOT)}</div>'
            f"{note_html()}"
        )
