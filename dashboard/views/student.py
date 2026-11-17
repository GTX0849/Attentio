"""Student view: live attention and presence for one student."""

from datetime import datetime
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.api import BackendError, get_live
from dashboard.theme import page_header
from shared.config import Config, load_config

DEFAULT_REGISTER_NO = "STU001"
REGISTER_NO_KEY = "student_register_no"  # set by the sidebar's register number input
RANGES = [10, 30, 60]  # minutes of history
REFRESH_SECONDS = load_config().dashboard_refresh_seconds
TITLE = "Your attention, live"


def render(config: Config) -> None:
    register_no = str(st.session_state.get(REGISTER_NO_KEY, DEFAULT_REGISTER_NO)).strip()
    live_view(config, register_no)


@st.fragment(run_every=REFRESH_SECONDS)
def live_view(config: Config, register_no: str) -> None:
    """Header and charts. Reruns on its own timer, leaving the sidebar alone."""
    header = st.container()  # filled once the data is known, so it can name the lecture
    eyebrow = f"Student · {register_no}" if register_no else "Student"
    if not register_no:
        with header:
            page_header(eyebrow, TITLE, "Enter a register number in the sidebar")
        return
    minutes = st.segmented_control(
        "History (minutes)", RANGES, default=RANGES[0], key="student_minutes"
    ) or RANGES[0]
    try:
        windows = get_live(config.backend_url, register_no, minutes)
    except BackendError as e:
        with header:
            page_header(eyebrow, TITLE, "")
        st.error(str(e))
        return
    with header:
        page_header(eyebrow, TITLE, lecture_context(windows))

    st.caption(f"Updated {datetime.now():%H:%M:%S}. Refreshes every {config.dashboard_refresh_seconds} s.")
    if not windows:
        st.info(f"No windows for {register_no} in the last {minutes} minutes. Is the worker running?")
        return

    df = to_frame(windows)
    latest = df.iloc[-1]
    with st.container(horizontal=True):
        st.metric("Latest attention", percent(latest["attention"]), border=True)
        st.metric("Latest presence", percent(latest["presence"]), border=True)
    for column, title in (("attention", "Attention"), ("presence", "Presence")):
        with st.container(border=True):
            st.subheader(title)
            st.altair_chart(
                line_chart(df, column),
                alt=f"{title} per 30-second window over the last {minutes} minutes",
            )


def lecture_context(windows: list[dict[str, Any]]) -> str:
    """The context line: the lecture of the newest window, or why there is none."""
    if not windows:
        return "No windows in this range"
    lecture = windows[-1].get("lecture")
    if lecture is None:
        return "No lecture matched the latest window"
    parts = [lecture["subject"], lecture["teacher"]]
    if lecture.get("section"):
        parts.append(lecture["section"])
    parts.append(f"room {lecture['room_id']}")
    return " · ".join(parts)


def to_frame(windows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(windows)
    # Show times in the viewer's local time zone.
    df["window_start"] = pd.to_datetime(df["window_start"], utc=True).dt.tz_convert(
        datetime.now().astimezone().tzinfo
    ).dt.tz_localize(None)
    return df


def line_chart(df: pd.DataFrame, column: str) -> alt.Chart:
    chart: alt.Chart = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("window_start:T", title="Window start"),
            y=alt.Y(f"{column}:Q", title=column.capitalize(), scale=alt.Scale(domain=[0, 1])),
            tooltip=[
                alt.Tooltip("window_start:T", title="Window start", format="%H:%M:%S"),
                alt.Tooltip(f"{column}:Q", title=column.capitalize(), format=".0%"),
            ],
        )
    )
    return chart


def percent(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:.0%}"
