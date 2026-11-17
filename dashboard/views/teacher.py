"""Teacher view: feedback on one teacher's lectures, never a ranking.

Data comes from GET /api/teachers/{id}/feedback (figures, lectures, attendance) and
GET /api/lectures/{id}/timeline (class attention inside one lecture). The figures are worked out
in backend/teacher_figures.py; this module only words and draws them.

Marks: the third stat card (`marks_card`) shows the marks figure of the feedback answer, the Student
groups card (`student_groups`) shows the four groups of a chosen test from
GET /api/marks/tests/{id}/groups, and the sidebar holds the upload (`sidebar_controls`, posting to
POST /api/marks/tests). The groups list individual students: this view is the teacher's own, and
the Admin view never asks for them.

The helpers above the render functions turn API answers into strings and frames with no
Streamlit calls, so the wording is easy to check.
"""

import math
from datetime import date, datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import api
from dashboard.api import BackendError
from dashboard.simulation import show_banner
from dashboard.theme import (
    BORDER,
    CARD_BACKGROUND,
    PRIMARY,
    SECONDARY_TEXT,
    STATE_COLOURS,
    TEXT,
    page_header,
    stat_card,
)
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
MARKS_CAVEAT = (
    "Marks vs own percentile: percentiles average 50 within every test, so this figure moves only when "
    "different students sit different tests — it reflects who sat each test, not better marks"
)
NO_GROUPS = "No marks yet — upload marks to see student groups"
NO_GROUP_DATA = "No lecture records for the students of this test yet"
CAVEAT = "The slot-adjusted figure cannot separate you from a slot you always teach in."
LABEL_PADDING = {"left": 14, "right": 8, "top": 4, "bottom": 4}

UPLOAD_KEY = "marks_file"
SUBJECT_KEY = "marks_subject"  # the teacher's id is added so each teacher keeps their own
DATE_KEY = "marks_date"
NAME_KEY = "marks_name"
RESULT_KEY = "marks_result"  # {"teacher_id", "subject", "result": {...}} or {"teacher_id", "error": "..."}
TEST_KEY = "marks_test"  # the groups card's test picker; the teacher's id is added
# Reading order of the grid: attention above the line on the top row, marks above the median on the left.
GROUP_GRID = ["On track", "Attentive but struggling", "Knows it already", "At risk"]
AT_RISK = "At risk"
DISTRACTED = STATE_COLOURS["Distracted"]
INSUFFICIENT = "Insufficient data"


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
    upload_notice(feedback)
    figures_row(feedback)
    lead_card(config, feedback)
    lower_row(config, feedback)


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


def pctl(value: float) -> str:
    """"+6 pctl", "-2 pctl", "0 pctl": percentile points, to the whole point."""
    whole = round(value)
    return "0 pctl" if whole == 0 else f"{whole:+d} pctl"


def marks_card(feedback: dict[str, Any]) -> tuple[str, str]:
    """The marks figure and where it comes from: "+6 pctl", "From 2 tests"; a dash with no marks."""
    marks = feedback.get("marks")
    if marks is None or marks["value"] is None or marks["n_tests"] == 0:
        return NO_DATA, NO_MARKS
    n = marks["n_tests"]
    return pctl(marks["value"]), f"From {n} test{'s' if n != 1 else ''}"


def date_text(day: str) -> str:
    """"9 Mar" from "2026-03-09"."""
    moment = datetime.fromisoformat(day)
    return f"{moment.day} {moment:%b}"


def marks_test_label(test: dict[str, Any]) -> str:
    """The test's name, or its subject and date when it has none: "Mid-term 1", "Physics, 9 Mar"."""
    if test["name"]:
        return str(test["name"])
    return f"{test['subject']}, {date_text(test['test_date'])}"


def option_label(test: dict[str, Any]) -> str:
    """The picker line: "Mid-term 1 (SIM-A) · Algorithms · 4 Mar"."""
    parts = [marks_test_label(test)]
    if test["name"]:
        parts.append(test["subject"])
    parts.append(date_text(test["test_date"]))
    return " · ".join(parts)


def groups_title(test: dict[str, Any]) -> str:
    return f"Student groups, {marks_test_label(test)}"


def groups_subtitle(groups: dict[str, Any]) -> str:
    """"Attention above 60% × marks above class median" (the 60 is the configured line)."""
    return f"Attention above {groups['attention_line']:.0%} × marks above class median"


def group_cell_html(name: str, count: int) -> str:
    """One cell: the count large and the group name under it. At risk has the Distracted colour border and count."""
    at_risk = name == AT_RISK
    border = DISTRACTED if at_risk else BORDER
    colour = DISTRACTED if at_risk else TEXT
    background = "#FFFFFF" if at_risk else CARD_BACKGROUND
    return (
        f'<div style="background:{background};border:1px solid {border};border-radius:12px;padding:14px 16px;">'
        f'<div style="font-size:28px;font-weight:600;line-height:1.2;font-variant-numeric:tabular-nums;'
        f'color:{colour};">{count}</div>'
        f'<div style="font-size:13px;color:{SECONDARY_TEXT};">{escape(name)}</div></div>'
    )


def groups_grid_html(counts: dict[str, int]) -> str:
    """The 2 x 2 grid of group counts."""
    cells = "".join(group_cell_html(name, counts.get(name, 0)) for name in GROUP_GRID)
    return f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">{cells}</div>'


def insufficient_text(count: int) -> str:
    return f"{count} student{'s' if count != 1 else ''} not grouped: not enough time in view over the covered lectures"


def students_frame(students: list[dict[str, Any]]) -> pd.DataFrame:
    """The expander's table: group, student, marks and attention, in group order."""
    order: dict[str | None, int] = {name: i for i, name in enumerate([*GROUP_GRID, None])}
    rows = sorted(students, key=lambda s: (order[s["group"]], s["register_no"]))
    return pd.DataFrame(
        {
            "Group": [s["group"] or INSUFFICIENT for s in rows],
            "Register no": [s["register_no"] for s in rows],
            "Name": [s["name"] or NO_DATA for s in rows],
            "Marks": [f"{s['marks']:g} of {s['max_marks']:g}" for s in rows],
            "Percentile": [f"{s['marks_percentile']:.0f}" for s in rows],
            "Attention": [NO_DATA if s["attention"] is None else f"{s['attention']:.0%}" for s in rows],
        }
    )


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
    if (feedback.get("marks") or {}).get("value") is not None:
        st.caption(MARKS_CAVEAT)


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


def lower_row(config: Config, feedback: dict[str, Any]) -> None:
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
        student_groups(config, feedback)
        st.caption(CAVEAT)


def student_groups(config: Config, feedback: dict[str, Any]) -> None:
    """The four groups of a chosen test: attention above the line by marks above the class median."""
    with st.container(border=True):
        try:
            tests = api.list_teacher_marks_tests(config.backend_url, feedback["teacher_id"])
        except BackendError as e:
            st.subheader("Student groups")
            st.error(str(e))
            return
        if not tests:
            st.subheader("Student groups")
            st.caption(NO_GROUPS)
            return
        by_id = {t["id"]: t for t in tests}
        head = st.container()  # the title names the chosen test, so it is filled in after the picker
        test_id = st.selectbox(
            "Test",
            list(by_id),
            format_func=lambda i: option_label(by_id[i]),
            key=f"{TEST_KEY}_{feedback['teacher_id']}",
            label_visibility="collapsed",
        )
        try:
            groups = api.get_marks_groups(config.backend_url, test_id)
        except BackendError as e:
            st.subheader("Student groups")
            st.error(str(e))
            return
        with head:
            st.subheader(groups_title(by_id[test_id]))
            st.caption(groups_subtitle(groups))
        st.html(groups_grid_html(groups["counts"]))
        if groups["n_covered_lectures"] == 0:
            st.caption(NO_GROUP_DATA)
        elif groups["counts"].get(INSUFFICIENT):
            st.caption(insufficient_text(groups["counts"][INSUFFICIENT]))
        if groups["students"]:
            with st.expander("Students in each group"):
                st.dataframe(
                    students_frame(groups["students"]),
                    hide_index=True,
                    alt="Each student of the test with marks, percentile, attention and group",
                )


# ---- marks upload ----------------------------------------------------------------------------------------


def teacher_subjects(timetable: dict[str, Any], teacher_name: str) -> list[str]:
    """The subjects the teacher is timetabled for, alphabetical."""
    return sorted({s["subject"] for s in timetable["slots"] if s["teacher"] == teacher_name})


def sidebar_controls(config: Config, teacher: dict[str, Any]) -> None:
    """The marks upload for the selected teacher: file, subject, test date, optional name and the button.

    Call inside `with st.sidebar:`. The outcome is shown at the top of the page by `upload_notice`.
    """
    st.divider()
    st.markdown("**Upload marks**")
    try:
        subjects = {s["name"]: s["id"] for s in api.list_subjects(config.backend_url)}
        own = teacher_subjects(api.get_timetable(config.backend_url), teacher["name"])
    except BackendError as e:
        st.warning(str(e))
        return
    subject_key = f"{SUBJECT_KEY}_{teacher['id']}"
    st.file_uploader(
        "Marks file",
        type=["csv", "xlsx"],
        key=UPLOAD_KEY,
        help="CSV or Excel (.xlsx) with the columns register_no, marks and max_marks",
    )
    names = list(subjects)
    st.selectbox(
        "Subject",
        names,
        index=names.index(own[0]) if own and own[0] in subjects else None,
        placeholder="Select a subject",
        key=subject_key,
    )
    st.date_input("Test date", value=date.today(), key=DATE_KEY)
    st.text_input("Test name (optional)", placeholder="Mid-term 1", key=NAME_KEY)
    ready = st.session_state.get(UPLOAD_KEY) is not None and bool(st.session_state.get(subject_key))
    st.button(
        "Upload marks",
        type="primary",
        width="stretch",
        disabled=not ready,
        on_click=_upload_marks,
        args=(config.backend_url, teacher["id"], subjects),
        key="marks_upload",
    )


def _upload_marks(backend_url: str, teacher_id: int, subjects: dict[str, int]) -> None:
    """Button callback: runs before the page reruns, so the page shows the outcome."""
    state = st.session_state
    uploaded = state.get(UPLOAD_KEY)
    subject = state.get(f"{SUBJECT_KEY}_{teacher_id}")
    if uploaded is None or subject is None:
        return
    try:
        result = api.upload_marks(
            backend_url,
            teacher_id,
            subjects[subject],
            state.get(DATE_KEY, date.today()),
            str(state.get(NAME_KEY) or ""),
            uploaded.name,
            uploaded.getvalue(),
        )
        state[RESULT_KEY] = {"teacher_id": teacher_id, "subject": subject, "result": result}
    except BackendError as e:
        state[RESULT_KEY] = {"teacher_id": teacher_id, "error": str(e)}


def upload_message(outcome: dict[str, Any]) -> tuple[str, str]:
    """("success" or "error", text) for the last upload."""
    if "error" in outcome:
        return "error", str(outcome["error"])
    result = outcome["result"]
    if result["applied"]:
        n = result["rows"]
        replaced = ". It replaced the earlier upload of this test" if result["replaced"] else ""
        return "success", f"Marks uploaded: {n} student{'s' if n != 1 else ''} in {outcome['subject']}{replaced}"
    n = len(result["errors"])
    return "error", f"Nothing was stored. {n} row{'s' if n != 1 else ''} need{'s' if n == 1 else ''} fixing, then upload the file again"


def upload_notice(feedback: dict[str, Any]) -> None:
    """The result of the last marks upload for this teacher, with the bad rows when it was refused."""
    outcome = st.session_state.get(RESULT_KEY)
    if outcome is None or outcome["teacher_id"] != feedback["teacher_id"]:
        return
    kind, text = upload_message(outcome)
    (st.success if kind == "success" else st.error)(text)
    result = outcome.get("result")
    if result is not None and not result["applied"]:
        problems = pd.DataFrame(result["errors"]).rename(columns={"row": "Row", "message": "Problem"})
        st.dataframe(problems, hide_index=True, alt="Rows of the marks file that need fixing")
