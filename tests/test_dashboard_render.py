"""The dashboard renders and its controls work, with the backend calls faked (no server needed)."""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import api, theme
from dashboard.views import student

APP = str(Path(__file__).resolve().parent.parent / "dashboard" / "app.py")

SLOT = {
    "id": 1,
    "day": "Mon",
    "day_index": 0,
    "start": "10:00",
    "end": "10:50",
    "room": "R101",
    "section": "CSE-A",
    "subject": "Physics",
    "teacher": "Dr Iyer",
}
LECTURE = {
    "id": 7,
    "source": "manual",
    "room_id": "R101",
    "teacher_id": 1,
    "teacher": "Dr Rao",
    "subject_id": 1,
    "subject": "Mathematics",
    "section_id": 1,
    "section": "CSE-A",
    "start": "2026-03-02T04:32:00Z",
    "end": None,
    "open": True,
}


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace the dashboard's HTTP calls; the returned dict records what was sent."""
    calls: dict[str, list[Any]] = {"upload": [], "start": [], "stop": []}
    open_sessions: list[dict[str, Any]] = [LECTURE]

    monkeypatch.setattr(api, "get_timetable", lambda url: {"timezone": "Asia/Kolkata", "slots": [SLOT]})
    monkeypatch.setattr(api, "list_rooms", lambda url: [{"id": "R101", "cameras": ["CAM1"]}])
    monkeypatch.setattr(api, "list_teachers", lambda url: [{"id": 1, "name": "Dr Rao"}])
    monkeypatch.setattr(api, "list_subjects", lambda url: [{"id": 1, "name": "Mathematics"}])
    monkeypatch.setattr(api, "list_sections", lambda url: [{"id": 1, "name": "CSE-A"}])
    monkeypatch.setattr(api, "list_lectures", lambda url, limit=20: [LECTURE])
    monkeypatch.setattr(api, "list_sessions", lambda url, open_only=False: list(open_sessions))

    def upload(url: str, name: str, content: bytes) -> dict[str, Any]:
        calls["upload"].append((name, content))
        return {
            "applied": False,
            "slots_total": 0,
            "slots_added": 0,
            "slots_kept": 0,
            "slots_removed": 0,
            "created": {"teachers": 0, "subjects": 0, "rooms": 0, "sections": 0},
            "errors": [{"row": 3, "message": "day 'Funday' is not one of Mon, Tue"}],
        }

    def start(url: str, room: str, teacher: int, subject: int, section: int | None) -> dict[str, Any]:
        calls["start"].append((room, teacher, subject, section))
        return LECTURE

    def stop(url: str, session_id: int) -> dict[str, Any]:
        calls["stop"].append(session_id)
        open_sessions.clear()
        return {**LECTURE, "open": False}

    monkeypatch.setattr(api, "upload_timetable", upload)
    monkeypatch.setattr(api, "start_session", start)
    monkeypatch.setattr(api, "stop_session", stop)
    return calls


def window(lecture: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "window_start": "2026-03-02T04:35:00Z",
        "attentive": 20,
        "neutral": 5,
        "distracted": 3,
        "not_visible": 2,
        "attention": 20 / 23,
        "presence": 0.93,
        "lecture": lecture,
    }


def run_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=20).run()


def html_of(at: AppTest) -> str:
    return " ".join(str(el.proto.body) for el in at.get("html"))


def test_student_view_names_the_current_lecture_in_its_header(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(student, "get_live", lambda url, reg, minutes: [window(LECTURE)])
    at = run_app()
    assert not at.exception
    page = html_of(at)
    assert "Student · STU001" in page
    assert "Your attention, live" in page
    assert "Mathematics · Dr Rao · CSE-A · room R101" in page


def test_student_view_says_so_when_the_latest_window_has_no_lecture(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(student, "get_live", lambda url, reg, minutes: [window(None)])
    at = run_app()
    assert not at.exception
    assert "No lecture matched the latest window" in html_of(at)


def test_sidebar_has_the_role_switcher_and_the_states_legend(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(student, "get_live", lambda url, reg, minutes: [])
    at = run_app()
    assert at.session_state["role"] == "Student"
    page = html_of(at)
    for name, description in theme.STATE_DESCRIPTIONS.items():
        assert name in page and description in page
    assert at.sidebar.text_input(key="student_register_no").value == "STU001"


def test_admin_view_shows_timetable_sessions_and_lectures(
    fake_backend: dict[str, list[Any]],
) -> None:
    at = run_app()
    at.session_state["role"] = "Admin"
    at.run()
    assert not at.exception
    assert "Timetable and sessions" in html_of(at)
    assert [s.value for s in at.subheader] == ["Weekly timetable", "Manual sessions", "Recent lectures"]
    assert len(at.dataframe) == 2  # the weekly timetable and the recent lectures
    assert any("R101" in m.value and "Mathematics" in m.value for m in at.markdown)


def test_admin_can_start_and_stop_a_session(fake_backend: dict[str, list[Any]]) -> None:
    at = run_app()
    at.session_state["role"] = "Admin"
    at.run()
    assert at.sidebar.button(key="session_start").disabled  # nothing chosen yet

    at.sidebar.selectbox(key="session_room").select("R101")
    at.sidebar.selectbox(key="session_teacher").select("Dr Rao")
    at.sidebar.selectbox(key="session_subject").select("Mathematics")
    at.run()
    at.sidebar.button(key="session_start").click().run()
    assert fake_backend["start"] == [("R101", 1, 1, None)]
    assert any("Session started in R101" in s.value for s in at.success)

    at.button(key="stop_session_7").click().run()
    assert fake_backend["stop"] == [7]
    assert any("Session stopped in R101" in s.value for s in at.success)


def test_admin_upload_shows_a_preview_and_reports_bad_rows(
    fake_backend: dict[str, list[Any]],
) -> None:
    at = run_app()
    at.session_state["role"] = "Admin"
    at.run()
    csv_bytes = b"day,start,end,room,section,subject,teacher\nFunday,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"
    at.sidebar.file_uploader(key="timetable_file").upload("t.csv", csv_bytes).run()
    assert not at.exception
    assert any("Preview of t.csv" in m.value for m in at.markdown)

    at.button(key="timetable_apply").click().run()
    assert fake_backend["upload"] == [("t.csv", csv_bytes)]
    assert any("Nothing was changed. 1 rows need fixing" in e.value for e in at.error)


def test_teacher_view_renders_with_a_teacher_select(fake_backend: dict[str, list[Any]]) -> None:
    at = run_app()
    at.session_state["role"] = "Teacher"
    at.run()
    assert not at.exception
    assert "Your lectures" in html_of(at)
    at.sidebar.selectbox(key="teacher_name").select("Dr Rao").run()
    assert "Showing Dr Rao" in html_of(at)


def test_theme_helpers_use_the_design_colours_and_escape_text() -> None:
    card = theme.stat_card_html("Attention", "78%", "Class average 71%", accent=True)
    assert "#F8F9F7" in card and "#DFE2DD" in card and "border-radius:12px" in card
    assert "font-size:30px" in card and "tabular-nums" in card and "#1B5FD0" in card
    assert "color:#18212B" in theme.stat_card_html("A", "1", "b")  # not accented

    header = theme.page_header_html("Admin", "<b>Title</b>", "Context")
    assert "&lt;b&gt;Title&lt;/b&gt;" in header and "text-transform:uppercase" in header

    banner = theme.simulated_banner_html("Simulated data")
    assert "#FFF4DA" in banner and "#7A4E00" in banner
    assert list(theme.STATE_COLOURS.values()) == ["#1B5FD0", "#9DB3D6", "#D4622A", "#C9CDD2"]
