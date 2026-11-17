"""Student view: live attention header, last 15 minutes by state, semester breakdowns.

Two fragments refresh on their own timers and leave the sidebar alone:

- `live_view` (every `dashboard.refresh_seconds`): header, the four stat cards
  and the 15-minute chart. Data from GET /api/students/{id}/current and /live.
- `semester_view` (every minute): by subject, weekly trend, by teacher, by time
  of day and attendance. Data from GET /api/students/{id}/profile.

The helpers above the render functions turn API answers into strings and
frames, with no Streamlit calls, so the wording is easy to check.
"""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.api import BackendError, get_live
from dashboard.api import get_student_current as get_current
from dashboard.api import get_student_profile as get_profile
from dashboard.simulation import show_banner
from dashboard.theme import (
    PRIMARY,
    SECONDARY_TEXT,
    STATE_COLOURS,
    STATE_FIELDS,
    live_indicator,
    page_header,
    stat_card,
    state_color,
)
from shared.config import Config, load_config

DEFAULT_REGISTER_NO = "STU001"
REGISTER_NO_KEY = "student_register_no"  # set by the sidebar's register number input
LIVE_MINUTES = 15  # span of the lead chart
REFRESH_SECONDS = load_config().dashboard_refresh_seconds
PROFILE_REFRESH_SECONDS = 60  # semester figures change slowly
TITLE = "Your attention, live"
NO_DATA = "—"
EMPTY_LECTURES = "No lectures yet — upload a timetable or start a session in the Admin view"
REASONS = {
    "low_presence": "Not enough time in view",
    "no_records": "No records yet",
    "no_scored_time": "No attentive or distracted time yet",
}


def render(config: Config) -> None:
    register_no = str(st.session_state.get(REGISTER_NO_KEY, DEFAULT_REGISTER_NO)).strip()
    live_view(config, register_no)
    if register_no:
        semester_view(config, register_no)


# ---- wording and frames (no Streamlit calls) -------------------------------------


def reason_text(reason: str | None) -> str:
    return REASONS.get(reason or "", "No data")


def local_time(timestamp: str, tz: ZoneInfo, fmt: str = "%H:%M") -> str:
    """An API timestamp (UTC, ISO 8601) in the backend's local zone."""
    return datetime.fromisoformat(timestamp).astimezone(tz).strftime(fmt)


def context_line(current: dict[str, Any]) -> str:
    """"Data Structures · Dr. Meera Iyer · Room R101 · started 10:02", or why there is no lecture."""
    lecture = current.get("lecture")
    if lecture is None:
        return "No lecture in progress"
    tz = ZoneInfo(current["timezone"])
    parts = [lecture["subject"], lecture["teacher"]]
    if lecture.get("section"):
        parts.append(lecture["section"])
    parts += [f"Room {lecture['room_id']}", f"started {local_time(lecture['start'], tz)}"]
    return " · ".join(parts)


def format_duration(seconds: float) -> str:
    """"4 min 10 s" or "45 s"."""
    whole = round(seconds)
    minutes, rest = divmod(whole, 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def percent(value: float | None) -> str:
    return NO_DATA if value is None else f"{value:.0%}"


def minutes_pair(visible: float, elapsed: float) -> str:
    """"In view 33 of 34 min", or in seconds for the first minute."""
    if elapsed < 60:
        return f"In view {round(visible)} of {round(elapsed)} s"
    return f"In view {round(visible / 60)} of {round(elapsed / 60)} min"


def attention_card(current: dict[str, Any]) -> tuple[str, str]:
    """Figure and comparison for "Attention, last 5 min"."""
    last = current["attention_last_5_min"]
    if last["value"] is None:
        return NO_DATA, reason_text(last["reason"])
    average = current["attention_average"]["value"]
    if average is None:
        return percent(last["value"]), "No average to compare with yet"
    return percent(last["value"]), f"{(last['value'] - average) * 100:+.1f} pts vs your average"


def presence_card(current: dict[str, Any]) -> tuple[str, str]:
    """Figure and comparison for "Presence, this lecture"."""
    presence = current["presence_this_lecture"]
    if presence is None:
        return NO_DATA, "No lecture in progress"
    if presence["ratio"] is None:
        return NO_DATA, "The lecture has just started"
    return percent(presence["ratio"]), minutes_pair(
        presence["visible_seconds"], presence["elapsed_seconds"]
    )


def distracted_card(current: dict[str, Any]) -> tuple[str, str]:
    """Figure and comparison for "Distracted, this lecture"."""
    distracted = current["distracted_this_lecture"]
    if distracted is None:
        return NO_DATA, "No lecture in progress"
    figure = format_duration(distracted["seconds"])
    if distracted["share_of_visible"] is None:
        return figure, "Not enough time in view"
    return figure, f"{distracted['share_of_visible']:.0%} of visible time"


def attendance_card(current: dict[str, Any]) -> tuple[str, str]:
    """Figure and comparison for "Attendance, this month"."""
    month = current["month"]
    if month["total"] == 0:
        return NO_DATA, "No lectures this month"
    return (
        f"{month['present']} of {month['total']}",
        f"Present at {month['min_presence_ratio']:.0%} in view or more",
    )


def live_status(current: dict[str, Any], windows: list[dict[str, Any]], window_seconds: int) -> tuple[str, bool]:
    """"Live · updated 12 s ago" from the newest window's end, and whether records are arriving."""
    if not windows:
        return "Live · waiting for records", False
    now = datetime.fromisoformat(current["now"])
    newest = datetime.fromisoformat(windows[-1]["window_start"])
    age = max(0, round((now - newest).total_seconds() - window_seconds))
    return f"Live · updated {age} s ago", True


# Axis labels are measured before the web font loads and can be clipped on the left; give them room.
LABEL_PADDING = {"left": 14, "right": 8, "top": 4, "bottom": 4}
SLOT_FORMAT = "%Y-%m-%dT%H:%M:%S"


def floor_to_window(moment: datetime, window_seconds: int) -> datetime:
    """Round a (naive local) time down to a window boundary."""
    epoch = datetime(1970, 1, 1)
    whole = int((moment - epoch).total_seconds()) // window_seconds * window_seconds
    return epoch + timedelta(seconds=whole)


def slot_starts(now_local: datetime, window_seconds: int) -> list[str]:
    """Local start times of the windows in the last LIVE_MINUTES, oldest first, as sortable text."""
    count = LIVE_MINUTES * 60 // window_seconds
    newest = floor_to_window(now_local, window_seconds)
    return [
        (newest - timedelta(seconds=window_seconds * (count - k))).strftime(SLOT_FORMAT)
        for k in range(count)
    ]


def state_frame(windows: list[dict[str, Any]], tz: ZoneInfo, window_seconds: int) -> pd.DataFrame:
    """One row per window and state. `slot` is the window start in local time, as slot_starts gives it."""
    rows: list[dict[str, Any]] = []
    for w in windows:
        start = datetime.fromisoformat(w["window_start"]).astimezone(tz).replace(tzinfo=None)
        start = floor_to_window(start, window_seconds)
        for rank, (field, label) in enumerate(STATE_FIELDS.items()):
            rows.append(
                {
                    "slot": start.strftime(SLOT_FORMAT),
                    "state": label,
                    "seconds": w[field],
                    "rank": rank,
                }
            )
    return pd.DataFrame(rows, columns=["slot", "state", "seconds", "rank"])


def states_chart(frame: pd.DataFrame, slots: list[str], window_seconds: int) -> alt.Chart:
    """Stacked columns: Attentive at the bottom, Not visible on top, y 0 to one window.

    The x axis lists every window of the last LIVE_MINUTES so missing records leave a gap.
    """
    chart: alt.Chart = (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X(
                "slot:O",
                title=None,
                scale=alt.Scale(domain=slots, paddingInner=0.15, paddingOuter=0.05),
                axis=alt.Axis(
                    values=slots[::4],  # a label every two minutes
                    labelExpr="substring(datum.value, 11, 16)",
                    labelAngle=0,
                ),
            ),
            y=alt.Y(
                "seconds:Q",
                title="Seconds",
                scale=alt.Scale(domain=[0, window_seconds]),
                stack="zero",
            ),
            color=state_color("state"),
            order=alt.Order("rank:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("slot:O", title="Window start"),
                alt.Tooltip("state:N", title="State"),
                alt.Tooltip("seconds:Q", title="Seconds"),
            ],
        )
        .properties(height=260)
    )
    return chart


def bars_chart(frame: pd.DataFrame, height: int) -> alt.LayerChart:
    """Horizontal Attentive-coloured bars (name, value 0 to 1) with the percentage at the end."""
    bars = alt.Chart(frame).encode(
        y=alt.Y("name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180)),
        x=alt.X(
            "value:Q",
            title=None,
            scale=alt.Scale(domain=[0, 1]),
            axis=alt.Axis(format="%", values=[0, 0.25, 0.5, 0.75, 1]),
        ),
    )
    layered: alt.LayerChart = (
        bars.mark_bar(color=STATE_COLOURS["Attentive"], size=18)
        + bars.mark_text(align="left", dx=4, color=SECONDARY_TEXT).encode(
            text=alt.Text("value:Q", format=".0%")
        )
    ).properties(height=height, padding=LABEL_PADDING)
    return layered


def hours_chart(frame: pd.DataFrame) -> alt.LayerChart:
    """Vertical Attentive-coloured bars by local hour."""
    bars = alt.Chart(frame).encode(
        x=alt.X("label:N", title=None, sort=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y(
            "value:Q",
            title=None,
            scale=alt.Scale(domain=[0, 1]),
            axis=alt.Axis(format="%", values=[0, 0.25, 0.5, 0.75, 1]),
        ),
    )
    layered: alt.LayerChart = (
        bars.mark_bar(color=STATE_COLOURS["Attentive"])
        + bars.mark_text(baseline="bottom", dy=-3, color=SECONDARY_TEXT).encode(
            text=alt.Text("value:Q", format=".0%")
        )
    ).properties(height=220, padding=LABEL_PADDING)
    return layered


def weekly_chart(frame: pd.DataFrame) -> alt.Chart:
    """A line in the primary colour; weeks with a null value leave a gap."""
    chart: alt.Chart = (
        alt.Chart(frame)
        .mark_line(point=True, color=PRIMARY)
        .encode(
            x=alt.X("week_start:T", title=None, axis=alt.Axis(format="%d %b")),
            y=alt.Y(
                "value:Q",
                title=None,
                scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(format="%", values=[0, 0.25, 0.5, 0.75, 1]),
            ),
            tooltip=[
                alt.Tooltip("week_start:T", title="Week of", format="%d %b %Y"),
                alt.Tooltip("value:Q", title="Attention", format=".0%"),
            ],
        )
        .properties(height=260, padding=LABEL_PADDING)
    )
    return chart


def breakdown_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Rows of {"name", "figure"} with a value, as a frame for `bars_chart`."""
    return pd.DataFrame(
        [{"name": r["name"], "value": r["figure"]["value"]} for r in rows if r["figure"]["value"] is not None],
        columns=["name", "value"],
    )


def left_out(rows: list[dict[str, Any]]) -> str:
    """A line naming the entries that have no value, or an empty string."""
    names = [r["name"] for r in rows if r["figure"]["value"] is None]
    return f"Not enough time in view: {', '.join(names)}" if names else ""


def attendance_frame(rows: list[dict[str, Any]], tz: ZoneInfo) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": [local_time(r["lecture"]["start"], tz, "%a %d %b") for r in rows],
            "Start": [local_time(r["lecture"]["start"], tz) for r in rows],
            "Subject": [r["lecture"]["subject"] for r in rows],
            "Teacher": [r["lecture"]["teacher"] for r in rows],
            "In view": [percent(r["presence"]) for r in rows],
            "Present": [
                ("Yes" if r["present"] else "No") + (" so far" if r["in_progress"] else "")
                for r in rows
            ],
        }
    )


# ---- live fragment -----------------------------------------------------------------


@st.fragment(run_every=REFRESH_SECONDS)
def live_view(config: Config, register_no: str) -> None:
    """Header, stat cards and the last 15 minutes. Reruns on its own timer."""
    eyebrow = f"Student · {register_no}" if register_no else "Student"
    if not register_no:
        page_header(eyebrow, TITLE, "Enter a register number in the sidebar")
        return
    try:
        current = get_current(config.backend_url, register_no)
        windows = get_live(config.backend_url, register_no, LIVE_MINUTES)
    except BackendError as e:
        page_header(eyebrow, TITLE)
        st.error(str(e))
        return
    tz = ZoneInfo(current["timezone"])
    if current.get("simulated"):
        show_banner(config)

    left, right = st.columns([4, 1])
    with left:
        page_header(eyebrow, TITLE, context_line(current))
    with right:
        status, live = live_status(current, windows, config.window_seconds)
        live_indicator(status, live)

    cards = [
        ("Attention, last 5 min", *attention_card(current), True),
        ("Presence, this lecture", *presence_card(current), False),
        ("Distracted, this lecture", *distracted_card(current), False),
        ("Attendance, this month", *attendance_card(current), False),
    ]
    for column, (label, figure, comparison, accent) in zip(st.columns(4), cards, strict=True):
        with column:
            stat_card(label, figure, comparison, accent)

    with st.container(border=True):
        st.subheader("Seconds in each state, per 30-second window")
        st.caption(f"Last {LIVE_MINUTES} minutes · each column is one record sent by the worker")
        if not windows:
            st.info(f"No records in the last {LIVE_MINUTES} minutes. Is the worker running?")
        else:
            now_local = datetime.fromisoformat(current["now"]).astimezone(tz).replace(tzinfo=None)
            st.altair_chart(
                states_chart(
                    state_frame(windows, tz, config.window_seconds),
                    slot_starts(now_local, config.window_seconds),
                    config.window_seconds,
                ),
                alt=(
                    f"Seconds in each attention state for every {config.window_seconds}-second "
                    f"window in the last {LIVE_MINUTES} minutes"
                ),
            )


# ---- semester fragment ---------------------------------------------------------------


@st.fragment(run_every=PROFILE_REFRESH_SECONDS)
def semester_view(config: Config, register_no: str) -> None:
    """Breakdowns over the whole semester and the attendance list."""
    try:
        profile = get_profile(config.backend_url, register_no)
    except BackendError as e:
        st.error(str(e))
        return
    tz = ZoneInfo(profile["timezone"])

    subject_col, week_col = st.columns([1, 1.6])
    with subject_col, st.container(border=True):
        st.subheader("Attention by subject")
        st.caption("This semester, from summed seconds")
        subject_figure(profile["by_subject"])
    with week_col, st.container(border=True):
        st.subheader("Weekly attention this semester")
        st.caption(
            f"All subjects · weeks with under {profile['min_presence_ratio']:.0%} presence are left blank"
        )
        weekly_figure(profile["weekly"])

    teacher_tab, hour_tab = st.tabs(["By teacher", "By time of day"])
    with teacher_tab:
        teachers = breakdown_frame(profile["by_teacher"])
        if profile["by_teacher"]:
            if not teachers.empty:
                st.altair_chart(
                    bars_chart(teachers, 40 * len(teachers) + 20), alt="Attention by teacher this semester"
                )
            if note := left_out(profile["by_teacher"]):
                st.caption(note)
        else:
            st.info(EMPTY_LECTURES)
    with hour_tab:
        hours = pd.DataFrame(
            [
                {"label": h["label"], "value": h["figure"]["value"]}
                for h in profile["by_time_of_day"]
                if h["figure"]["value"] is not None
            ],
            columns=["label", "value"],
        )
        st.caption("Local hour of the day, from summed seconds")
        if hours.empty:
            st.info("No hour has enough time in view yet")
        else:
            st.altair_chart(hours_chart(hours), alt="Attention by local hour of the day")

    with st.expander("Attendance"):
        rows = profile["attendance"]
        if not rows:
            st.info(EMPTY_LECTURES)
        else:
            month = profile["month"]
            st.caption(
                f"Present in {month['present']} of {month['total']} lectures in {month['label']}. "
                f"A lecture counts as attended at {profile['min_presence_ratio']:.0%} in view or more of its full length"
            )
            st.dataframe(attendance_frame(rows, tz), hide_index=True, alt="Attendance for each lecture")


def subject_figure(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.info(EMPTY_LECTURES)
        return
    frame = breakdown_frame(rows)
    if not frame.empty:
        st.altair_chart(bars_chart(frame, 40 * len(frame) + 20), alt="Attention by subject this semester")
    if note := left_out(rows):
        st.caption(note)


def weekly_figure(weeks: list[dict[str, Any]]) -> None:
    if not weeks:
        st.info("No weeks to show yet")
        return
    frame = pd.DataFrame(
        {
            "week_start": pd.to_datetime([w["week_start"] for w in weeks]),
            "value": [w["figure"]["value"] for w in weeks],
        }
    )
    if frame["value"].isna().all():
        st.info("No week has enough time in view yet")
        return
    st.altair_chart(weekly_chart(frame), alt="Attention by week this semester")
