"""Teacher view (placeholder, built in a later ticket)."""

import streamlit as st

from dashboard import api
from dashboard.api import BackendError
from dashboard.simulation import show_banner
from dashboard.theme import page_header
from shared.config import Config

TEACHER_KEY = "teacher_name"  # set by the sidebar's teacher select


def render(config: Config) -> None:
    teacher = st.session_state.get(TEACHER_KEY)
    if teacher and _is_simulated(config, teacher):
        show_banner(config)
    page_header(
        "Teacher",
        "Your lectures",
        f"Showing {teacher}" if teacher else "Select a teacher in the sidebar",
    )
    st.info("Lecture attention figures are coming soon.")


def _is_simulated(config: Config, name: str) -> bool:
    try:
        return any(t["name"] == name and t.get("simulated") for t in api.list_teachers(config.backend_url))
    except BackendError:
        return False
