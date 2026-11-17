"""The sidebar after sign-in: wordmark, who is signed in, Sign out, the role's own controls and the legend.

There is no role switcher: the role comes from the account.
"""

from html import escape

import streamlit as st

from dashboard import api, session, theme
from dashboard.session import Identity
from dashboard.views import admin, teacher
from shared.config import Config

ROLE_LABELS = {"student": "Student", "teacher": "Teacher", "admin": "Admin"}
SIGN_OUT = "Sign out"


def identity_html(identity: Identity) -> str:
    """The signed-in name with the role under it (and the email when the name is something else)."""
    role = ROLE_LABELS.get(identity.role, identity.role)
    email = (
        f'<div style="font-size:13px;color:{theme.SECONDARY_TEXT};overflow-wrap:anywhere;">'
        f"{escape(identity.email)}</div>"
        if identity.email and identity.email != identity.display_name
        else ""
    )
    return (
        f'<div style="padding:2px 0 4px 0;">'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:0.12em;'
        f'color:{theme.SECONDARY_TEXT};">{escape(role)}</div>'
        f'<div style="font-size:15px;font-weight:600;color:{theme.TEXT};overflow-wrap:anywhere;">'
        f"{escape(identity.display_name or identity.email)}</div>{email}</div>"
    )


def sign_out(config: Config) -> None:
    """Button callback: end the sign-in on the backend, then forget it here."""
    api.logout(config.backend_url)
    session.end()


def render_sidebar(config: Config, identity: Identity) -> None:
    """Draw the sidebar for the signed-in person."""
    theme.pin_legend_css()
    with st.sidebar:
        theme.wordmark()
        st.html(identity_html(identity))
        st.button(
            SIGN_OUT,
            icon=":material/logout:",
            width="stretch",
            on_click=sign_out,
            args=(config,),
            key="sign_out",
        )
        st.divider()
        if identity.role == "teacher" and identity.teacher_id is not None:
            teacher.sidebar_controls(config, {"id": identity.teacher_id, "name": identity.display_name})
        elif identity.role == "admin":
            admin.sidebar_controls(config)
        theme.states_legend()
