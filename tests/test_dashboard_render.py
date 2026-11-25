"""The dashboard renders and its controls work, with the backend calls faked (no server needed)."""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import api, session, theme
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


def figure(
    value: float | None,
    reason: str | None = None,
    presence: float | None = 0.9,
    attentive: float = 0,
    distracted: float = 0,
) -> dict[str, Any]:
    return {
        "value": value,
        "reason": reason,
        "presence": presence,
        "attentive": attentive,
        "neutral": 0,
        "distracted": distracted,
        "not_visible": 0,
    }


STUDENT = {
    "register_no": "STU001",
    "name": "Demo Student",
    "section": None,
    "simulated": False,
    "archetype": None,
}
SIM_STUDENT = {
    "register_no": "SIM-A01",
    "name": "Aarav Sharma",
    "section": "SIM-A",
    "simulated": True,
    "archetype": "engaged",
}
SIMULATION = {"students": 60, "weeks": 4, "teachers": 5, "sections": ["SIM-A", "SIM-B"], "effects": []}
BANNER = "Simulated data — generated cohort of 60 students over 4 weeks, not real classroom records"

DS_LECTURE = {
    **LECTURE,
    "id": 9,
    "source": "timetable",
    "teacher": "Dr. Meera Iyer",
    "subject": "Data Structures",
    "start": "2026-03-02T04:32:00Z",  # 10:02 in Asia/Kolkata
    "end": "2026-03-02T05:22:00Z",
    "open": False,
}
MONTH = {"label": "March 2026", "present": 17, "total": 19, "min_presence_ratio": 0.5}
CURRENT = {
    "register_no": "STU001",
    "timezone": "Asia/Kolkata",
    "now": "2026-03-02T05:06:00Z",
    "min_presence_ratio": 0.5,
    "lecture": DS_LECTURE,
    "attention_last_5_min": figure(0.782),
    "attention_average": figure(0.72),
    "presence_this_lecture": {"ratio": 33 / 34, "visible_seconds": 1980, "elapsed_seconds": 2040},
    "distracted_this_lecture": {"seconds": 250, "share_of_visible": 0.12},
    "month": MONTH,
}
NO_CURRENT = {
    **CURRENT,
    "lecture": None,
    "attention_last_5_min": figure(None, "no_records", None),
    "attention_average": figure(None, "no_records", None),
    "presence_this_lecture": None,
    "distracted_this_lecture": None,
    "month": {**MONTH, "present": 0, "total": 0},
}
PROFILE = {
    "register_no": "STU001",
    "timezone": "Asia/Kolkata",
    "now": "2026-03-02T05:06:00Z",
    "min_presence_ratio": 0.5,
    "overall": figure(0.72),
    "by_subject": [
        {"id": 1, "name": "Data Structures", "figure": figure(0.81)},
        {"id": 2, "name": "Physics", "figure": figure(None, "low_presence", 0.3)},
    ],
    "by_teacher": [{"id": 1, "name": "Dr. Meera Iyer", "figure": figure(0.81)}],
    "by_time_of_day": [{"hour": 10, "label": "10:00", "figure": figure(0.8)}],
    "weekly": [
        {"week_start": "2026-02-23", "figure": figure(0.7)},
        {"week_start": "2026-03-02", "figure": figure(None, "low_presence", 0.2)},
    ],
    "attendance": [
        {
            "lecture": DS_LECTURE,
            "presence": 0.58,
            "present": True,
            "in_progress": True,
            "figure": figure(0.8),
        },
        {
            "lecture": {**DS_LECTURE, "id": 8, "start": "2026-02-23T04:32:00Z"},
            "presence": 0.2,
            "present": False,
            "in_progress": False,
            "figure": figure(None, "low_presence", 0.2),
        },
    ],
    "month": MONTH,
}
NO_PROFILE = {
    **PROFILE,
    "overall": figure(None, "no_records", None),
    "by_subject": [],
    "by_teacher": [],
    "by_time_of_day": [],
    "weekly": [],
    "attendance": [],
    "month": {**MONTH, "present": 0, "total": 0},
}


NO_TEACHER_FEEDBACK = {
    "teacher_id": 1,
    "teacher": "Dr Rao",
    "simulated": False,
    "timezone": "Asia/Kolkata",
    "now": "2026-03-02T05:06:00Z",
    "min_lectures": 5,
    "range_level": 0.95,
    "n_lectures": 0,
    "hidden": True,
    "reason": "fewer than 5 lectures",
    "self_comparison": {"value": None, "low": None, "high": None, "n_students": 0},
    "slot_adjusted": {"value": None, "low": None, "high": None, "n_students": 0},
    "marks": {"value": None, "low": None, "high": None, "n_students": 0, "n_tests": 0, "reason": "no marks uploaded"},
    "attendance": {"present": 0, "expected": 0, "rate": None},
    "lectures": [],
}
NO_STUDENT_MARKS = {"register_no": "STU001", "simulated": False, "tests": []}


EMPTY_OVERVIEW = {
    "timezone": "Asia/Kolkata",
    "now": "2026-03-02T05:06:00Z",
    "min_lectures": 5,
    "range_level": 0.95,
    "teachers": [],
    "sections": [],
    "unassigned_windows": 0,
}


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace the dashboard's HTTP calls; the returned dict records what was sent."""
    calls: dict[str, list[Any]] = {"upload": [], "start": [], "stop": [], "marks": [], "logout": [], "accounts": []}
    open_sessions: list[dict[str, Any]] = [LECTURE]

    monkeypatch.setattr(api, "get_timetable", lambda url: {"timezone": "Asia/Kolkata", "slots": [SLOT]})
    monkeypatch.setattr(api, "list_rooms", lambda url: [{"id": "R101", "cameras": ["CAM1"]}])
    monkeypatch.setattr(api, "list_teachers", lambda url: [{"id": 1, "name": "Dr Rao"}])
    monkeypatch.setattr(api, "get_simulation", lambda url: None)
    monkeypatch.setattr(api, "list_students", lambda url: [STUDENT])
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

    monkeypatch.setattr(api, "logout", lambda url: calls["logout"].append(url))
    monkeypatch.setattr(api, "list_accounts", lambda url: list(ACCOUNTS))
    monkeypatch.setattr(api, "list_teacher_subjects", lambda url, teacher_id: [{"id": 1, "name": "Mathematics"}])

    def create_account(url: str, email: str, password: str, role: str, register_no: str | None, teacher_id: int | None) -> dict[str, Any]:
        calls["accounts"].append((email, role, register_no, teacher_id))
        return {"email": email.lower()}

    monkeypatch.setattr(api, "create_account", create_account)
    monkeypatch.setattr(api, "get_admin_overview", lambda url: EMPTY_OVERVIEW)
    monkeypatch.setattr(api, "get_teacher_feedback", lambda url, teacher_id: NO_TEACHER_FEEDBACK)
    monkeypatch.setattr(api, "get_lecture_timeline", lambda url, lecture_id: {"points": []})
    monkeypatch.setattr(api, "list_teacher_marks_tests", lambda url, teacher_id: [])
    monkeypatch.setattr(api, "get_student_marks", lambda url, register_no: NO_STUDENT_MARKS)
    monkeypatch.setattr(student, "get_live", lambda url, reg, minutes: [])
    monkeypatch.setattr(student, "get_current", lambda url, reg: NO_CURRENT)
    monkeypatch.setattr(student, "get_profile", lambda url, reg: NO_PROFILE)
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


ACCOUNTS = [
    {
        "id": 1,
        "email": "admin@example.edu",
        "role": "admin",
        "student_register_no": None,
        "teacher_id": None,
        "linked_name": None,
        "created_at": "2026-03-01T09:00:00Z",
        "simulated": False,
    },
    {
        "id": 2,
        "email": "rao@example.edu",
        "role": "teacher",
        "student_register_no": None,
        "teacher_id": 1,
        "linked_name": "Dr Rao",
        "created_at": "2026-03-01T09:05:00Z",
        "simulated": False,
    },
]
# The signed-in identity each role starts from (the sign-in itself is tested in test_signin_render.py).
IDENTITIES: dict[str, dict[str, Any]] = {
    "student": {
        "token": "t-student",
        "role": "student",
        "email": "asha@example.edu",
        "display_name": "Demo Student",
        "student_register_no": "STU001",
        "teacher_id": None,
    },
    "teacher": {
        "token": "t-teacher",
        "role": "teacher",
        "email": "rao@example.edu",
        "display_name": "Dr Rao",
        "student_register_no": None,
        "teacher_id": 1,
    },
    "admin": {
        "token": "t-admin",
        "role": "admin",
        "email": "admin@example.edu",
        "display_name": "admin@example.edu",
        "student_register_no": None,
        "teacher_id": None,
    },
}


def signed_in(role: str = "student", **identity: Any) -> AppTest:
    """The app before its first run, already signed in as `role` (override identity fields by name)."""
    at = AppTest.from_file(APP, default_timeout=20)
    at.session_state[session.AUTH_KEY] = {**IDENTITIES[role], **identity}
    return at


def run_app(role: str = "student", **identity: Any) -> AppTest:
    """Run the app signed in as `role` (student by default), landing in that role's view."""
    return signed_in(role, **identity).run()


def html_of(at: AppTest) -> str:
    return " ".join(str(el.proto.body) for el in at.get("html"))


def fake_student(
    monkeypatch: pytest.MonkeyPatch,
    current: dict[str, Any],
    profile: dict[str, Any],
    windows: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(student, "get_current", lambda url, reg: current)
    monkeypatch.setattr(student, "get_profile", lambda url, reg: profile)
    monkeypatch.setattr(student, "get_live", lambda url, reg, minutes: windows)


def test_student_view_with_data_shows_header_cards_and_charts(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_student(monkeypatch, CURRENT, PROFILE, [window(DS_LECTURE)])
    at = run_app()
    assert not at.exception
    page = html_of(at)
    assert "Student · STU001" in page
    assert "Your attention, live" in page
    assert "Data Structures · Dr. Meera Iyer · CSE-A · Room R101 · started 10:02" in page
    assert "Live · updated" in page

    for label in (
        "Attention, last 5 min",
        "Presence, this lecture",
        "Distracted, this lecture",
        "Attendance, this month",
    ):
        assert label in page
    assert "78%" in page and "+6.2 pts vs your average" in page
    assert "97%" in page and "In view 33 of 34 min" in page
    assert "4 min 10 s" in page and "12% of visible time" in page
    assert "17 of 19" in page and "Present at 50% in view or more" in page

    assert [s.value for s in at.subheader] == [
        "Seconds in each state, per 30-second window",
        "Attention by subject",
        "Weekly attention this semester",
        "Marks",
    ]
    captions = [c.value for c in at.caption]
    assert "Last 15 minutes · each column is one record sent by the worker" in captions
    assert "This semester, from summed seconds" in captions
    assert "All subjects · weeks with under 50% presence are left blank" in captions
    assert "Not enough time in view: Physics" in captions  # a subject that fails the gate
    assert len(at.get("vega_lite_chart")) == 5  # states, subject, week, teacher, hour
    assert len(at.dataframe) == 1  # the attendance list
    assert [t.label for t in at.tabs] == ["By teacher", "By time of day"]
    assert [e.label for e in at.expander] == ["Attendance"]


def test_student_view_with_no_data_shows_dashes_and_plain_reasons(
    fake_backend: dict[str, list[Any]],
) -> None:
    at = run_app()
    assert not at.exception
    page = html_of(at)
    assert "No lecture in progress" in page
    assert "Live · waiting for records" in page
    assert page.count("—") >= 4  # every card shows a dash
    assert "No records yet" in page  # attention reason
    assert "No lectures this month" in page
    infos = [i.value for i in at.info]
    assert "No records in the last 15 minutes. Is the worker running?" in infos
    assert infos.count(student.EMPTY_LECTURES) >= 3  # subject, teacher and attendance panels
    assert "No weeks to show yet" in infos
    assert len(at.get("vega_lite_chart")) == 0


def test_student_card_says_when_attention_has_too_little_time_in_view(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    current = {**CURRENT, "attention_last_5_min": figure(None, "low_presence", 0.3)}
    fake_student(monkeypatch, current, PROFILE, [window(DS_LECTURE)])
    at = run_app()
    assert not at.exception
    assert "Not enough time in view" in html_of(at)


def test_student_view_reports_a_backend_error_in_place(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(url: str, reg: str) -> dict[str, Any]:
        raise api.BackendError("Cannot reach the backend at http://x")

    monkeypatch.setattr(student, "get_current", down)
    monkeypatch.setattr(student, "get_profile", down)
    at = run_app()
    assert not at.exception
    assert any("Cannot reach the backend" in e.value for e in at.error)
    assert "Your attention, live" in html_of(at)


def test_student_helpers_word_the_cards_from_the_api_figures() -> None:
    assert student.format_duration(250) == "4 min 10 s"
    assert student.format_duration(45) == "45 s"
    assert student.minutes_pair(20, 40) == "In view 20 of 40 s"
    assert student.attention_card(CURRENT) == ("78%", "+6.2 pts vs your average")
    below = {**CURRENT, "attention_last_5_min": figure(0.6)}
    assert student.attention_card(below) == ("60%", "-12.0 pts vs your average")
    assert student.distracted_card(NO_CURRENT) == ("—", "No lecture in progress")
    assert student.attendance_card(CURRENT) == ("17 of 19", "Present at 50% in view or more")


def test_the_sidebar_shows_who_is_signed_in_a_sign_out_button_and_the_states_legend_but_no_role_switcher(
    fake_backend: dict[str, list[Any]],
) -> None:
    for role, label, name in (("student", "Student", "Demo Student"), ("teacher", "Teacher", "Dr Rao"), ("admin", "Admin", "admin@example.edu")):
        at = run_app(role)
        assert not at.exception
        sidebar = " ".join(str(el.proto.body) for el in at.sidebar.get("html"))
        assert label in sidebar and name in sidebar
        assert [b.label for b in at.sidebar.button if b.key == "sign_out"] == ["Sign out"]
        for state, description in theme.STATE_DESCRIPTIONS.items():
            assert state in sidebar and description in sidebar
        assert not at.sidebar.get("button_group")  # no segmented "View as" control
        assert "role" not in at.session_state


def test_admin_view_shows_timetable_sessions_and_lectures(
    fake_backend: dict[str, list[Any]],
) -> None:
    at = run_app("admin")
    assert not at.exception
    assert "Timetable and sessions" in html_of(at)
    assert [s.value for s in at.subheader] == ["Weekly timetable", "Manual sessions", "Recent lectures", "Accounts"]
    assert len(at.dataframe) == 3  # the weekly timetable, the recent lectures and the accounts
    assert any("R101" in m.value and "Mathematics" in m.value for m in at.markdown)


def test_admin_can_start_and_stop_a_session(fake_backend: dict[str, list[Any]]) -> None:
    at = run_app("admin")
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
    at = run_app("admin")
    csv_bytes = b"day,start,end,room,section,subject,teacher\nFunday,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"
    at.sidebar.file_uploader(key="timetable_file").upload("t.csv", csv_bytes).run()
    assert not at.exception
    assert any("Preview of t.csv" in m.value for m in at.markdown)

    at.button(key="timetable_apply").click().run()
    assert fake_backend["upload"] == [("t.csv", csv_bytes)]
    assert any("Nothing was changed. 1 rows need fixing" in e.value for e in at.error)


def test_teacher_view_is_the_signed_in_teachers_own_with_no_picker(fake_backend: dict[str, list[Any]]) -> None:
    at = run_app("teacher")
    assert not at.exception
    page = html_of(at)
    assert "Teacher · Dr Rao" in page and "Feedback on your lectures" in page
    assert [s.key for s in at.sidebar.selectbox if s.key == "teacher_name"] == []


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


# ---- simulated data -----------------------------------------------------------------------------------


def test_the_banner_names_the_cohort_from_the_simulation_endpoint() -> None:
    from dashboard.simulation import FALLBACK, banner_text

    assert banner_text(SIMULATION) == BANNER
    assert banner_text({**SIMULATION, "students": 12, "weeks": 1}) == (
        "Simulated data — generated cohort of 12 students over 1 week, not real classroom records"
    )
    assert banner_text(None) == FALLBACK


def test_the_student_view_is_the_signed_in_students_own_with_no_picker(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(api, "list_students", lambda url: pytest.fail("a student never lists students"))
    def get_current(url: str, reg: str) -> dict[str, Any]:
        asked.append(reg)
        return NO_CURRENT

    monkeypatch.setattr(student, "get_current", get_current)
    at = run_app("student", student_register_no="SIM-A01")
    assert not at.exception
    assert "Student · SIM-A01" in html_of(at)
    assert not list(at.sidebar.selectbox)  # no register number picker
    assert asked and set(asked) == {"SIM-A01"}


def test_a_student_account_without_a_link_says_so(fake_backend: dict[str, list[Any]]) -> None:
    at = run_app("student", student_register_no=None)
    assert not at.exception
    assert any("not linked to a student" in e.value for e in at.error)


def test_a_simulated_student_shows_the_banner_and_a_live_one_does_not(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "get_simulation", lambda url: SIMULATION)
    monkeypatch.setattr(api, "list_students", lambda url: [STUDENT, SIM_STUDENT])
    fake_student(monkeypatch, {**CURRENT, "simulated": True}, PROFILE, [window(DS_LECTURE)])
    at = run_app("student", student_register_no="SIM-A01")
    assert not at.exception
    page = html_of(at)
    assert BANNER in page
    assert page.index(BANNER) < page.index("Your attention, live")  # at the top

    fake_student(monkeypatch, {**CURRENT, "simulated": False}, PROFILE, [window(DS_LECTURE)])
    at = run_app()
    assert "Simulated data" not in html_of(at)


def test_the_admin_view_shows_the_banner_while_a_simulation_is_loaded(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    at = run_app("admin")
    assert "Simulated data" not in html_of(at)

    monkeypatch.setattr(api, "get_simulation", lambda url: SIMULATION)
    at = run_app("admin")
    assert BANNER in html_of(at)


def test_the_teacher_view_shows_the_banner_for_a_simulated_teacher(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "get_simulation", lambda url: SIMULATION)
    monkeypatch.setattr(
        api,
        "get_teacher_feedback",
        lambda url, teacher_id: {**NO_TEACHER_FEEDBACK, "teacher_id": teacher_id, "simulated": teacher_id == 2},
    )
    assert "Simulated data" not in html_of(run_app("teacher", teacher_id=1))
    assert BANNER in html_of(run_app("teacher", teacher_id=2, display_name="Dr Sim"))


def test_the_sidebar_wordmark_says_attentio_and_nothing_of_the_old_name() -> None:
    html = theme.wordmark_html()
    assert ">Attentio<" in html
    for old in ("ClassPulse", "Class", "Pulse"):
        assert old not in html
