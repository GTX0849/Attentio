"""Teacher view (placeholder, built in a later ticket)."""

import streamlit as st

from dashboard.theme import page_header
from shared.config import Config

TEACHER_KEY = "teacher_name"  # set by the sidebar's teacher select


def render(config: Config) -> None:
    teacher = st.session_state.get(TEACHER_KEY)
    page_header(
        "Teacher",
        "Your lectures",
        f"Showing {teacher}" if teacher else "Select a teacher in the sidebar",
    )
    st.info("Lecture attention figures are coming soon.")
