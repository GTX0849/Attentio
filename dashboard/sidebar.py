"""The sidebar: wordmark, role switcher, the role's own control and the states legend."""

from typing import Any

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
            _student_select(config)
        elif role == "Teacher":
            _teacher_select(config)
        else:
            admin.sidebar_controls(config)
        theme.states_legend()
    return role


def student_options(students: list[dict[str, Any]]) -> dict[str, str]:
    """Register number -> label for the student picker. STU001 is always offered, first."""
    labels = {student.DEFAULT_REGISTER_NO: student.DEFAULT_REGISTER_NO}
    for s in students:
        parts = [s["register_no"], s.get("name"), s.get("section")]
        labels[s["register_no"]] = " · ".join(str(p) for p in parts if p)
    return labels


def _student_select(config: Config) -> None:
    """Pick a known student (STU001 and the simulated ones) or type any register number."""
    try:
        labels = student_options(api.list_students(config.backend_url))
    except BackendError as e:
        st.warning(str(e))
        labels = student_options([])
    st.selectbox(
        "Register number",
        list(labels),
        index=0,
        format_func=lambda register_no: labels.get(register_no, register_no),
        accept_new_options=True,
        required=True,
        help="Pick a student, or type a register number. Simulated students are SIM-A01 to SIM-B30.",
        key=student.REGISTER_NO_KEY,
        persist_state="session",
    )


def _teacher_select(config: Config) -> None:
    """Pick one of the real teachers; the Teacher view shows that teacher's feedback."""
    try:
        teachers = api.list_teachers(config.backend_url)
    except BackendError as e:
        st.warning(str(e))
        return
    names = [t["name"] for t in teachers]
    if not names:
        st.caption(teacher.NO_TEACHERS)
        return
    chosen = st.selectbox(
        "Teacher",
        names,
        index=0,
        key=teacher.TEACHER_KEY,
        persist_state="session",
    )
    selected = next((t for t in teachers if t["name"] == chosen), teachers[0])
    teacher.sidebar_controls(config, selected)
