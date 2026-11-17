"""Admin view: weekly timetable, manual sessions and recent lectures.

Later tickets add the teacher-figures table to this view. The three sections
below are separate functions and share only the data passed to them.

The sidebar holds the controls (`sidebar_controls`): the timetable file picker
and the manual-session pickers. The main area holds what they act on: the
preview, the result messages, the tables and the Stop buttons.
"""

import io
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from dashboard import api
from dashboard.api import BackendError
from dashboard.simulation import show_banner
from dashboard.theme import page_header
from shared.config import Config

UPLOAD_KEY = "timetable_file"
TIMETABLE_RESULT_KEY = "timetable_result"  # {"result": {...}} or {"error": "..."}
SESSION_NOTICE_KEY = "session_notice"  # (kind, text)
PREVIEW_ROWS = 20
LECTURE_ROWS = 15
SOURCE_LABELS = {"timetable": "Timetable", "manual": "Manual session"}


# ---- sidebar ---------------------------------------------------------------------


def sidebar_controls(config: Config) -> None:
    """The admin's sidebar controls. Call inside `with st.sidebar:`."""
    st.file_uploader(
        "Timetable CSV",
        type="csv",
        key=UPLOAD_KEY,
        help="Columns: day, start, end, room, section, subject, teacher. Uploading replaces the weekly timetable.",
    )
    st.divider()
    _session_controls(config)


def _session_controls(config: Config) -> None:
    st.markdown("**Manual session**")
    try:
        rooms = [r["id"] for r in api.list_rooms(config.backend_url)]
        teachers = {t["name"]: t["id"] for t in api.list_teachers(config.backend_url)}
        subjects = {s["name"]: s["id"] for s in api.list_subjects(config.backend_url)}
        sections = {s["name"]: s["id"] for s in api.list_sections(config.backend_url)}
    except BackendError as e:
        st.warning(str(e))
        return
    st.selectbox("Room", rooms, index=None, placeholder="Select a room", key="session_room")
    st.selectbox(
        "Teacher", list(teachers), index=None, placeholder="Select a teacher", key="session_teacher"
    )
    st.selectbox(
        "Subject", list(subjects), index=None, placeholder="Select a subject", key="session_subject"
    )
    st.selectbox(
        "Section (optional)",
        list(sections),
        index=None,
        placeholder="Select a section",
        key="session_section",
    )
    ready = all(st.session_state.get(k) for k in ("session_room", "session_teacher", "session_subject"))
    st.button(
        "Start session",
        type="primary",
        width="stretch",
        disabled=not ready,
        on_click=_start_session,
        args=(config.backend_url, teachers, subjects, sections),
        key="session_start",
    )


def _start_session(
    backend_url: str,
    teachers: dict[str, int],
    subjects: dict[str, int],
    sections: dict[str, int],
) -> None:
    """Button callback: runs before the page reruns, so the main area shows the outcome."""
    state = st.session_state
    section = state.get("session_section")
    try:
        started = api.start_session(
            backend_url,
            state["session_room"],
            teachers[state["session_teacher"]],
            subjects[state["session_subject"]],
            sections[section] if section else None,
        )
        state[SESSION_NOTICE_KEY] = (
            "success",
            f"Session started in {started['room_id']} for {started['subject']} with {started['teacher']}",
        )
    except BackendError as e:
        state[SESSION_NOTICE_KEY] = ("error", str(e))


def _stop_session(backend_url: str, session_id: int) -> None:
    try:
        stopped = api.stop_session(backend_url, session_id)
        st.session_state[SESSION_NOTICE_KEY] = (
            "success",
            f"Session stopped in {stopped['room_id']}",
        )
    except BackendError as e:
        st.session_state[SESSION_NOTICE_KEY] = ("error", str(e))


def _apply_timetable(backend_url: str) -> None:
    uploaded = st.session_state.get(UPLOAD_KEY)
    if uploaded is None:
        return
    try:
        result = api.upload_timetable(backend_url, uploaded.name, uploaded.getvalue())
        st.session_state[TIMETABLE_RESULT_KEY] = {"result": result}
    except BackendError as e:
        st.session_state[TIMETABLE_RESULT_KEY] = {"error": str(e)}


# ---- main area -------------------------------------------------------------------


def render(config: Config) -> None:
    try:
        timetable = api.get_timetable(config.backend_url)
    except BackendError as e:
        page_header("Admin", "Timetable and sessions")
        st.error(str(e))
        return
    tz = ZoneInfo(timetable["timezone"])
    if _simulation_loaded(config):
        show_banner(config)
    page_header(
        "Admin",
        "Timetable and sessions",
        f"Timetable times are local time in {timetable['timezone']}. Records are stored in UTC.",
    )
    timetable_section(config, timetable)
    sessions_section(config, tz)
    lectures_section(config, tz)


def _simulation_loaded(config: Config) -> bool:
    """True when simulated data may be among the timetable and lectures shown."""
    try:
        return api.get_simulation(config.backend_url) is not None
    except BackendError:
        return False


def timetable_section(config: Config, timetable: dict[str, Any]) -> None:
    """Upload preview, the result of the last upload, and the current weekly template."""
    with st.container(border=True):
        st.subheader("Weekly timetable")
        uploaded = st.session_state.get(UPLOAD_KEY)
        if uploaded is not None:
            _upload_preview(config, uploaded.name, uploaded.getvalue())
        _upload_result()

        slots = timetable["slots"]
        if not slots:
            st.info("No timetable yet. Choose a CSV file in the sidebar.")
            return
        st.caption(f"{len(slots)} weekly slots")
        frame = pd.DataFrame(slots).rename(
            columns={
                "day": "Day",
                "start": "Start",
                "end": "End",
                "room": "Room",
                "section": "Section",
                "subject": "Subject",
                "teacher": "Teacher",
            }
        )[["Day", "Start", "End", "Room", "Section", "Subject", "Teacher"]]
        st.dataframe(frame, hide_index=True, alt="Current weekly timetable")


def _upload_preview(config: Config, name: str, content: bytes) -> None:
    st.markdown(f"**Preview of {name}**")
    try:
        preview = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as e:
        st.error(f"This file cannot be read as CSV: {e}")
        return
    st.dataframe(
        preview.head(PREVIEW_ROWS),
        hide_index=True,
        alt=f"First rows of the uploaded file {name}",
    )
    shown = min(len(preview), PREVIEW_ROWS)
    st.caption(f"{len(preview)} rows, showing the first {shown}. Replacing keeps lectures already recorded.")
    st.button(
        "Replace timetable",
        type="primary",
        on_click=_apply_timetable,
        args=(config.backend_url,),
        key="timetable_apply",
    )


def _upload_result() -> None:
    outcome = st.session_state.get(TIMETABLE_RESULT_KEY)
    if outcome is None:
        return
    if "error" in outcome:
        st.error(outcome["error"])
        return
    result = outcome["result"]
    if result["applied"]:
        created = result["created"]
        st.success(
            f"Timetable replaced: {result['slots_total']} slots "
            f"({result['slots_added']} added, {result['slots_kept']} kept, "
            f"{result['slots_removed']} removed). Created {created['teachers']} teachers, "
            f"{created['subjects']} subjects, {created['rooms']} rooms and {created['sections']} sections"
        )
    else:
        errors = pd.DataFrame(result["errors"]).rename(columns={"row": "Row", "message": "Problem"})
        st.error(f"Nothing was changed. {len(errors)} rows need fixing, then upload the file again")
        st.dataframe(errors, hide_index=True, alt="Rows of the uploaded file that need fixing")


def sessions_section(config: Config, tz: ZoneInfo) -> None:
    """Open manual sessions with a Stop button each. Starting happens in the sidebar."""
    with st.container(border=True):
        st.subheader("Manual sessions")
        st.caption("An open session overrides the timetable for its room until it is stopped.")
        notice = st.session_state.pop(SESSION_NOTICE_KEY, None)
        if notice is not None:
            kind, text = notice
            (st.success if kind == "success" else st.error)(text)
        try:
            open_sessions = api.list_sessions(config.backend_url, open_only=True)
        except BackendError as e:
            st.error(str(e))
            return
        if not open_sessions:
            st.info("No session is open. Use Manual session in the sidebar to start one.")
            return
        for item in open_sessions:
            info, action = st.columns([5, 1], vertical_alignment="center")
            info.markdown(
                f"**{item['room_id']}** · {item['subject']} · {item['teacher']}"
                f"{' · ' + item['section'] if item['section'] else ''}"
                f" · since {_local(item['start'], tz, '%H:%M')}"
            )
            action.button(
                "Stop",
                key=f"stop_session_{item['id']}",
                width="stretch",
                on_click=_stop_session,
                args=(config.backend_url, item["id"]),
            )


def lectures_section(config: Config, tz: ZoneInfo) -> None:
    """The most recent lectures, from both the timetable and manual sessions."""
    with st.container(border=True):
        st.subheader("Recent lectures")
        try:
            lectures = api.list_lectures(config.backend_url, limit=LECTURE_ROWS)
        except BackendError as e:
            st.error(str(e))
            return
        if not lectures:
            st.info("No lectures yet. A lecture appears when the first record for it arrives.")
            return
        frame = pd.DataFrame(
            {
                "Start": [_local(x["start"], tz, "%a %d %b %H:%M") for x in lectures],
                "End": [_local(x["end"], tz, "%H:%M") if x["end"] else "Open" for x in lectures],
                "Room": [x["room_id"] for x in lectures],
                "Subject": [x["subject"] for x in lectures],
                "Teacher": [x["teacher"] for x in lectures],
                "Section": [x["section"] or "" for x in lectures],
                "Source": [SOURCE_LABELS.get(x["source"], x["source"]) for x in lectures],
            }
        )
        st.dataframe(frame, hide_index=True, alt="Recent lectures")


def _local(timestamp: str, tz: ZoneInfo, fmt: str) -> str:
    """An API timestamp (UTC, ISO 8601) shown in the timetable's time zone."""
    return datetime.fromisoformat(timestamp).astimezone(tz).strftime(fmt)
