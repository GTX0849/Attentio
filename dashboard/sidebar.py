"""The sidebar: wordmark, role switcher, the role's own control and the states legend."""

import streamlit as st

from dashboard import api, theme
from dashboard.api import BackendError
from dashboard.views import admin, student, teacher
from shared.config import Config

ROLES = ["Student", "Teacher", "Admin"]


def render_sidebar(config: Config) -> str:
    """Draw the sidebar and return the chosen role (key `role` in session state)."""
    theme.pin_legend_css()
    with st.sidebar:
        theme.wordmark()
        role = (
            st.segmented_control(
                "View as", ROLES, default=ROLES[0], key="role", width="stretch"
            )
            or ROLES[0]
        )
        st.divider()
        if role == "Student":
            st.text_input(
                "Register number",
                value=student.DEFAULT_REGISTER_NO,
                key=student.REGISTER_NO_KEY,
                persist_state="session",
            )
        elif role == "Teacher":
            _teacher_select(config)
        else:
            admin.sidebar_controls(config)
        theme.states_legend()
    return role


def _teacher_select(config: Config) -> None:
    """Placeholder: the choice is stored, a later ticket shows that teacher's figures."""
    try:
        names = [t["name"] for t in api.list_teachers(config.backend_url)]
    except BackendError as e:
        st.warning(str(e))
        return
    st.selectbox(
        "Teacher",
        names,
        index=None,
        placeholder="Select a teacher",
        key=teacher.TEACHER_KEY,
        persist_state="session",
    )
