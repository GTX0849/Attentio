"""The Teacher view renders from backend answers, with the backend calls faked (no server needed)."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from dashboard import api
from dashboard.views import teacher
from tests.test_dashboard_render import (  # noqa: F401  (fake_backend is a fixture)
    BANNER,
    NO_TEACHER_FEEDBACK,
    SIMULATION,
    fake_backend,
    html_of,
    run_app,
)

NEWEST = datetime.fromisoformat("2026-11-10T04:30:00+00:00")  # Tuesday 10:00 in Asia/Kolkata


def lecture_row(k: int) -> dict[str, Any]:
    """Lecture number k, newest first: one a day at 10:00 for the even ones (section A), 14:00 for the odd (B)."""
    start = NEWEST - timedelta(days=k) + (timedelta(hours=4) if k % 2 else timedelta())
    return {
        "lecture": {
            "id": 100 - k,
            "source": "timetable",
            "room_id": "R102" if k % 2 else "R101",
            "teacher_id": 1,
            "teacher": "Dr. Meera Iyer",
            "subject_id": 1,
            "subject": "Data Structures",
            "section_id": 2 if k % 2 else 1,
            "section": "B" if k % 2 else "A",
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": (start + timedelta(minutes=50)).isoformat().replace("+00:00", "Z"),
            "open": False,
            "simulated": True,
        },
        "present": 29 - k % 3,
        "expected": 30,
        "in_progress": False,
        "attention": {
            "value": 0.742 - 0.01 * k,
            "reason": None,
            "presence": 0.9,
            "attentive": 100,
            "neutral": 0,
            "distracted": 20,
            "not_visible": 0,
        },
    }


FEEDBACK = {
    **NO_TEACHER_FEEDBACK,
    "teacher": "Dr. Meera Iyer",
    "simulated": True,
    "n_lectures": 22,
    "hidden": False,
    "reason": None,
    "self_comparison": {"value": 4.8, "low": 1.9, "high": 7.6, "n_students": 58},
    "slot_adjusted": {"value": 3.1, "low": 0.4, "high": 5.9, "n_students": 58},
    "attendance": {"present": 1206, "expected": 1320, "rate": 1206 / 1320},
    "lectures": [lecture_row(k) for k in range(22)],
}
HIDDEN = {
    **FEEDBACK,
    "n_lectures": 3,
    "hidden": True,
    "reason": "fewer than 5 lectures",
    "self_comparison": {"value": None, "low": None, "high": None, "n_students": 0},
    "slot_adjusted": {"value": None, "low": None, "high": None, "n_students": 0},
    "lectures": FEEDBACK["lectures"][:3],  # type: ignore[index]
}
TIMELINE = {
    "lecture": FEEDBACK["lectures"][0]["lecture"],  # type: ignore[index]
    "window_seconds": 30,
    "points": [
        {
            "window": w,
            "offset_seconds": 30 * w,
            "students": 29,
            "attentive": 600,
            "distracted": 100,
            "attention": 0.86 - 0.002 * w,
        }
        for w in range(100)
    ],
}


@pytest.fixture
def teacher_data(fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:  # noqa: F811
    """A simulated teacher with 22 lectures. The returned dict records which lectures' timelines were asked for."""
    asked: dict[str, list[Any]] = {"timeline": [], "feedback": []}
    monkeypatch.setattr(api, "get_simulation", lambda url: SIMULATION)
    monkeypatch.setattr(api, "list_teachers", lambda url: [{"id": 1, "name": "Dr. Meera Iyer", "simulated": True}])

    def get_feedback(url: str, teacher_id: int) -> dict[str, Any]:
        asked["feedback"].append(teacher_id)
        return FEEDBACK

    def get_timeline(url: str, lecture_id: int) -> dict[str, Any]:
        asked["timeline"].append(lecture_id)
        return TIMELINE

    monkeypatch.setattr(api, "get_teacher_feedback", get_feedback)
    monkeypatch.setattr(api, "get_lecture_timeline", get_timeline)
    return asked


def teacher_page() -> tuple[Any, str]:
    at = run_app("teacher")
    assert not at.exception
    return at, html_of(at)


# ---- with data ----------------------------------------------------------------------------------------


def test_the_header_and_banner_come_from_the_teachers_data(teacher_data: dict[str, list[Any]]) -> None:
    at, page = teacher_page()
    assert "teacher_name" not in [s.key for s in at.sidebar.selectbox]  # no teacher picker
    assert [b.key for b in at.sidebar.button if b.key == "sign_out"] == ["sign_out"]
    assert teacher_data["feedback"] == [1]
    assert page.index(BANNER) < page.index("Teacher · Dr. Meera Iyer")  # the banner is at the top
    assert "Feedback on your lectures" in page
    assert "Data Structures · sections A and B · 22 lectures since 20 Oct" in page


def test_the_four_stat_cards_show_the_figures_with_their_ranges(teacher_data: dict[str, list[Any]]) -> None:
    _, page = teacher_page()
    assert "Students vs their own average" in page
    assert "+4.8 pts" in page and "95% range +1.9 to +7.6 · 22 lectures" in page
    assert "After removing time-slot effect" in page
    assert "+3.1 pts" in page and "95% range +0.4 to +5.9" in page
    assert "Marks vs own percentile" in page and "No marks uploaded yet" in page
    assert "Attendance" in page and "91.4%" in page and "Across 22 lectures" in page
    assert page.index("#1B5FD0") < page.index("+4.8 pts")  # the first figure is in the primary colour


def test_the_lead_card_charts_class_attention_for_the_chosen_lecture(
    teacher_data: dict[str, list[Any]],
) -> None:
    at, page = teacher_page()
    assert "Class attention within the lecture" in [s.value for s in at.subheader]
    assert "Attentive ÷ (attentive + distracted), all present students, per 30-second window" in [
        c.value for c in at.caption
    ]
    picker = at.selectbox(key="teacher_lecture_1")
    assert picker.options[0] == "Tue 10 Nov · 10:00–10:50 · R101"
    assert picker.options[1] == "Mon 9 Nov · 14:00–14:50 · R102"
    assert teacher_data["timeline"] == [100]  # the newest lecture first
    assert len(at.get("vega_lite_chart")) == 1

    at.selectbox(key="teacher_lecture_1").select(99).run()
    assert not at.exception
    assert teacher_data["timeline"][-1] == 99


def test_recent_lectures_and_the_two_placeholders_for_marks(teacher_data: dict[str, list[Any]]) -> None:
    at, page = teacher_page()
    subheaders = [s.value for s in at.subheader]
    assert "Recent lectures" in subheaders and "Student groups" in subheaders
    frame = at.dataframe[0].value
    assert list(frame.columns) == ["Lecture", "Section", "Present", "Attention"]
    assert len(frame) == teacher.RECENT_LECTURES
    assert frame.iloc[0].tolist() == ["Tue 10 Nov, 10:00", "A", "29 of 30", "74.2%"]
    assert frame.iloc[1].tolist() == ["Mon 9 Nov, 14:00", "B", "28 of 30", "73.2%"]
    captions = [c.value for c in at.caption]
    assert teacher.NO_GROUPS in captions
    assert "The slot-adjusted figure cannot separate you from a slot you always teach in." in captions


def test_the_copy_has_no_exclamation_marks_and_labels_have_no_final_periods(
    teacher_data: dict[str, list[Any]],
) -> None:
    at, page = teacher_page()
    assert "!" not in page
    for label in (
        "Students vs their own average",
        "After removing time-slot effect",
        "Marks vs own percentile",
        "Feedback on your lectures",
    ):
        assert label in page and f"{label}." not in page
    assert not any(ch in page for ch in "😀👍✅")


# ---- hidden figures -------------------------------------------------------------------------------------


def test_figures_stay_hidden_until_the_minimum_number_of_lectures(
    teacher_data: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "get_teacher_feedback", lambda url, teacher_id: HIDDEN)
    at, page = teacher_page()
    assert "+4.8 pts" not in page and "range" not in page
    assert page.count("Figures appear after 5 lectures") == 2  # both figure cards
    assert "Data Structures · sections A and B · 3 lectures since 8 Nov" in page
    assert len(at.dataframe[0].value) == 3  # the lectures are still listed
    assert len(at.get("vega_lite_chart")) == 1


# ---- no data -----------------------------------------------------------------------------------------------


def test_a_teacher_with_no_lectures_gets_plain_empty_states(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    at, page = teacher_page()  # the fixture's feedback has no lectures at all
    assert "Teacher · Dr Rao" in page and "Feedback on your lectures" in page
    assert "No lectures with records yet" in page
    assert page.count("Figures appear after 5 lectures") == 2
    assert teacher.NO_GROUPS in [c.value for c in at.caption]
    assert "No marks uploaded yet" in page
    infos = [i.value for i in at.info]
    assert infos.count(teacher.NO_LECTURES) >= 2  # the chart and the table
    assert len(at.get("vega_lite_chart")) == 0 and len(at.dataframe) == 0
    assert "Simulated data" not in page


def test_a_lecture_with_no_timeline_says_so(
    teacher_data: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "get_lecture_timeline", lambda url, lecture_id: {**TIMELINE, "points": []})
    at, _ = teacher_page()
    assert teacher.NO_TIMELINE in [i.value for i in at.info]
    assert len(at.get("vega_lite_chart")) == 0


def test_a_backend_error_is_shown_in_place(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    def down(url: str, teacher_id: int) -> dict[str, Any]:
        raise api.BackendError("Cannot reach the backend at http://x")

    monkeypatch.setattr(api, "get_teacher_feedback", down)
    at, page = teacher_page()
    assert any("Cannot reach the backend" in e.value for e in at.error)
    assert "Feedback on your lectures" in page


def test_a_teacher_account_that_is_not_linked_to_a_teacher_says_so(
    fake_backend: dict[str, list[Any]],  # noqa: F811
) -> None:
    at = run_app("teacher", teacher_id=None)
    assert not at.exception
    assert any("not linked to a teacher" in e.value for e in at.error)


# ---- wording helpers -------------------------------------------------------------------------------------------


def test_the_wording_helpers() -> None:
    assert teacher.points(4.84) == "+4.8 pts"
    assert teacher.points(-2.5) == "-2.5 pts"
    assert teacher.range_text({"value": 1.0, "low": -0.45, "high": 3.2, "n_students": 5}, 0.95) == (
        "95% range -0.5 to +3.2"
    )
    assert teacher.range_text({"value": 1.0, "low": None, "high": None, "n_students": 1}, 0.95) == (
        "Range needs two or more students"
    )
    assert teacher.join_names(["A"]) == "A"
    assert teacher.join_names(["A", "B"]) == "A and B"
    assert teacher.join_names(["A", "B", "C"]) == "A, B and C"
