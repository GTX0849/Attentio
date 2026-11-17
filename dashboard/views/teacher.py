"""Teacher view: feedback on one teacher's lectures, never a ranking.

Data comes from GET /api/teachers/{id}/feedback (figures, lectures, attendance) and
GET /api/lectures/{id}/timeline (class attention inside one lecture). The figures are worked out
in backend/teacher_figures.py; this module only words and draws them.

Hooks for the marks ticket (CLA-15): `marks_card` (the third stat card) and `student_groups`
(the Student groups card) currently say that no marks are uploaded. Replace their bodies to read
the marks figures from the feedback answer.

The helpers above the render functions turn API answers into strings and frames with no
Streamlit calls, so the wording is easy to check.
"""

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import api
from dashboard.api import BackendError
from dashboard.simulation import show_banner
from dashboard.theme import PRIMARY, SECONDARY_TEXT, page_header, stat_card
from shared.config import Config

TEACHER_KEY = "teacher_name"  # set by the sidebar's teacher select
LECTURE_KEY = "teacher_lecture"  # the lecture picker; the teacher's id is added so each teacher keeps their own
TITLE = "Feedback on your lectures"
NO_DATA = "—"
RECENT_LECTURES = 8  # rows in the Recent lectures table
NO_TEACHERS = "No teachers yet — upload a timetable in the Admin view"
NO_LECTURES = "No lectures with records yet"
NO_TIMELINE = "No records for this lecture yet"
NO_MARKS = "No marks uploaded yet"
NO_GROUPS = "Upload marks to see student groups"
CAVEAT = "The slot-adjusted figure cannot separate you from a slot you always teach in."
LABEL_PADDING = {"left": 14, "right": 8, "top": 4, "bottom": 4}


def render(config: Config) -> None:
    try:
        teachers = api.list_teachers(config.backend_url)
    except BackendError as e:
        page_header("Teacher", TITLE)
        st.error(str(e))
        return
    chosen = st.session_state.get(TEACHER_KEY)
    teacher = next((t for t in teachers if t["name"] == chosen), None)
    if teacher is None:
        page_header("Teacher", TITLE, "Select a teacher in the sidebar")
        st.info(NO_TEACHERS if not teachers else "Select a teacher in the sidebar")
        return
    try:
        feedback = api.get_teacher_feedback(config.backend_url, teacher["id"])
    except BackendError as e:
        page_header(f"Teacher · {teacher['name']}", TITLE)
        st.error(str(e))
        return

    if feedback.get("simulated"):
        show_banner(config)
    page_header(f"Teacher · {feedback['teacher']}", TITLE, context_line(feedback))
    figures_row(feedback)
    lead_card(config, feedback)
    lower_row(feedback)


# ---- wording and frames (no Streamlit calls) -------------------------------------------------------------


def points(value: float) -> str:
    """"+4.8 pts"."""
    return f"{value:+.1f} pts"


def join_names(names: list[str]) -> str:
    """"A", "A and B", "A, B and C"."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def local_start(lecture: dict[str, Any], tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(lecture["start"]).astimezone(tz)


def day_text(moment: datetime) -> str:
    """"Tue 11 Nov" (no leading zero on the day)."""
    return f"{moment:%a} {moment.day} {moment:%b}"


def context_line(feedback: dict[str, Any]) -> str:
    """"Data Structures · sections A and B · 22 lectures since 20 Oct"."""
    rows = feedback["lectures"]
    if not rows:
        return NO_LECTURES
    tz = ZoneInfo(feedback["timezone"])
    lectures = [r["lecture"] for r in rows]
    subjects = sorted({x["subject"] for x in lectures})
    sections = sorted({x["section"] for x in lectures if x.get("section")})
    first = min(local_start(x, tz) for x in lectures)
    parts = [join_names(subjects)]
    if sections:
        parts.append(f"section{'s' if len(sections) > 1 else ''} {join_names(sections)}")
    parts.append(f"{feedback['n_lectures']} lecture{'s' if feedback['n_lectures'] != 1 else ''} since {first.day} {first:%b}")
    return " · ".join(parts)


def range_text(figure: dict[str, Any], level: float) -> str:
    """"95% range +1.9 to +7.6"; one student gives no range."""
    if figure["low"] is None or figure["high"] is None:
        return "Range needs two or more students"
    return f"{level:.0%} range {figure['low']:+.1f} to {figure['high']:+.1f}"


def hidden_text(feedback: dict[str, Any]) -> str:
    return f"Figures appear after {feedback['min_lectures']} lectures"


def figure_card(feedback: dict[str, Any], key: str, with_count: bool) -> tuple[str, str]:
    """Figure and comparison for a points card: hidden, no data, or value with its range."""
    if feedback["hidden"]:
        return NO_DATA, hidden_text(feedback)
    figure = feedback[key]
    if figure["value"] is None:
        return NO_DATA, "No students with enough time in view"
    comparison = range_text(figure, feedback["range_level"])
    if with_count and figure["low"] is not None:
        comparison += f" · {feedback['n_lectures']} lectures"
    return points(figure["value"]), comparison


def marks_card(feedback: dict[str, Any]) -> tuple[str, str]:
    """Hook for CLA-15: the marks figure ("+6 pctl", "From 2 tests") once marks exist."""
    return NO_DATA, NO_MARKS


def attendance_card(feedback: dict[str, Any]) -> tuple[str, str]:
    attendance = feedback["attendance"]
    if attendance["rate"] is None:
        return NO_DATA, "No lectures yet"
    return f"{attendance['rate']:.1%}", f"Across {len(feedback['lectures'])} lectures"


def lecture_label(lecture: dict[str, Any], tz: ZoneInfo) -> str:
    """"Tue 11 Nov · 10:00–10:50 · R101"."""
    start = local_start(lecture, tz)
    end = datetime.fromisoformat(lecture["end"]).astimezone(tz).strftime("%H:%M") if lecture["end"] else "now"
    return f"{day_text(start)} · {start:%H:%M}–{end} · {lecture['room_id']}"


def lectures_frame(rows: list[dict[str, Any]], tz: ZoneInfo) -> pd.DataFrame:
    """The Recent lectures table: Lecture, Section, Present ("29 of 30"), Attention."""
    return pd.DataFrame(
        {
            "Lecture": [f"{day_text(local_start(r['lecture'], tz))}, {local_start(r['lecture'], tz):%H:%M}" for r in rows],
            "Section": [r["lecture"].get("section") or NO_DATA for r in rows],
            "Present": [f"{r['present']} of {r['expected']}" for r in rows],
            "Attention": [
                NO_DATA if r["attention"]["value"] is None else f"{r['attention']['value']:.1%}" for r in rows
            ],
        }
    )


def timeline_frame(points_: list[dict[str, Any]]) -> pd.DataFrame:
    """Minutes from the lecture start and attention (0 to 1); a window with no figure leaves a gap."""
    return pd.DataFrame(
        {
            "minute": [p["offset_seconds"] / 60 for p in points_],
            "attention": [p["attention"] for p in points_],
        }
    )


def timeline_chart(frame: pd.DataFrame) -> alt.Chart:
    """A line in the primary colour; the y axis starts a little below the lowest value."""
    lowest = frame["attention"].min()
    floor = 0.0 if pd.isna(lowest) else max(0.0, math.floor(lowest * 5) / 5 - 0.1)
    chart: alt.Chart = (
        alt.Chart(frame)
        .mark_line(color=PRIMARY, point=alt.OverlayMarkDef(size=14, color=PRIMARY))
        .encode(
            x=alt.X("minute:Q", title="Minutes from the lecture start", axis=alt.Axis(tickMinStep=5)),
            y=alt.Y(
                "attention:Q",
                title=None,
                scale=alt.Scale(domain=[floor, 1]),
                axis=alt.Axis(format="%", tickCount=4),
            ),
            tooltip=[
                alt.Tooltip("minute:Q", title="Minute", format=".1f"),
                alt.Tooltip("attention:Q", title="Class attention", format=".1%"),
            ],
        )
        .properties(height=220, padding=LABEL_PADDING)
    )
    return chart


# ---- rendering -----------------------------------------------------------------------------------------------


def figures_row(feedback: dict[str, Any]) -> None:
    cards = [
        ("Students vs their own average", *figure_card(feedback, "self_comparison", True), True),
        ("After removing time-slot effect", *figure_card(feedback, "slot_adjusted", False), False),
        ("Marks vs own percentile", *marks_card(feedback), False),
        ("Attendance", *attendance_card(feedback), False),
    ]
    for column, (label, figure, comparison, accent) in zip(st.columns([1.25, 1.25, 1, 1]), cards, strict=True):
        with column:
            stat_card(label, figure, comparison, accent)


def lead_card(config: Config, feedback: dict[str, Any]) -> None:
    rows = feedback["lectures"]
    with st.container(border=True):
        title, picker = st.columns([2, 1], vertical_alignment="top")
        with title:
            st.subheader("Class attention within the lecture")
            st.caption("Attentive ÷ (attentive + distracted), all present students, per 30-second window")
        if not rows:
            st.info(NO_LECTURES)
            return
        tz = ZoneInfo(feedback["timezone"])
        by_id = {r["lecture"]["id"]: r["lecture"] for r in rows}
        with picker:
            lecture_id = st.selectbox(
                "Lecture",
                list(by_id),
                format_func=lambda i: lecture_label(by_id[i], tz),
                key=f"{LECTURE_KEY}_{feedback['teacher_id']}",
                label_visibility="collapsed",
            )
        timeline = api.get_lecture_timeline(config.backend_url, lecture_id)["points"]
        if not timeline:
            st.info(NO_TIMELINE)
            return
        st.altair_chart(
            timeline_chart(timeline_frame(timeline)),
            alt="Class attention in each 30-second window of the chosen lecture",
        )


def lower_row(feedback: dict[str, Any]) -> None:
    left, right = st.columns([1.3, 1])
    with left, st.container(border=True):
        st.subheader("Recent lectures")
        rows = feedback["lectures"][:RECENT_LECTURES]
        if not rows:
            st.info(NO_LECTURES)
        else:
            tz = ZoneInfo(feedback["timezone"])
            right_aligned = st.column_config.TextColumn(alignment="right")
            st.dataframe(
                lectures_frame(rows, tz),
                hide_index=True,
                column_config={"Present": right_aligned, "Attention": right_aligned},
                alt="Recent lectures with section, students present and attention",
            )
    with right:
        student_groups(feedback)
        st.caption(CAVEAT)


def student_groups(feedback: dict[str, Any]) -> None:
    """Hook for CLA-15: the four groups (attention above 60% by marks above the class median)."""
    with st.container(border=True):
        st.subheader("Student groups")
        st.caption(NO_GROUPS)
