"""The Admin view's teacher feedback renders from backend answers, with the backend calls faked."""

from typing import Any

import pytest

from dashboard import admin_figures, api
from tests.test_dashboard_render import (  # noqa: F401  (fake_backend is a fixture)
    BANNER,
    EMPTY_OVERVIEW,
    SIMULATION,
    fake_backend,
    html_of,
    run_app,
)


def figure(value: float | None, half: float = 2.0) -> dict[str, Any]:
    if value is None:
        return {"value": None, "low": None, "high": None, "n_students": 0}
    return {"value": value, "low": value - half, "high": value + half, "n_students": 58}


def teacher(name: str, lectures: int, average: float, own: float | None, slot: float | None) -> dict[str, Any]:
    hidden = own is None
    return {
        "teacher_id": 1,
        "teacher": name,
        "simulated": True,
        "n_lectures": lectures,
        "hidden": hidden,
        "reason": "fewer than 5 lectures" if hidden else None,
        "class_average": average,
        "self_comparison": figure(own),
        "slot_adjusted": figure(slot),
        "attendance": {"present": 10, "expected": 12, "rate": 10 / 12},
    }


# The names are listed as the backend sends them: alphabetical, which is not the order of any score.
TEACHERS = [
    teacher("Dr. Arjun Menon", 20, 0.768, 0.9, -0.6),
    teacher("Dr. Kavya Rao", 19, 0.735, -1.1, -2.2),
    teacher("Dr. Meera Iyer", 22, 0.709, 4.8, 3.1),
    teacher("Ms. Fatima Sheikh", 4, 0.69, None, None),
    teacher("Prof. Sanjay Pillai", 21, 0.642, -2.4, 0.8),
]
OVERVIEW = {
    **EMPTY_OVERVIEW,
    "teachers": TEACHERS,
    "sections": [
        {"section_id": 1, "section": "Section A", "simulated": True, "n_students": 30, "n_lectures": 80,
         "present": 2234, "expected": 2400, "rate": 0.931},
        {"section_id": 2, "section": "Section B", "simulated": True, "n_students": 30, "n_lectures": 80,
         "present": 2122, "expected": 2400, "rate": 0.884},
    ],
    "unassigned_windows": 36,
}  # fmt: skip
SIM: dict[str, Any] = {
    **SIMULATION,
    "effects": [
        {"kind": "teacher", "key": "Dr. Meera Iyer", "value": 4.0},
        {"kind": "teacher", "key": "Dr. Arjun Menon", "value": 0.0},
        {"kind": "teacher", "key": "Prof. Sanjay Pillai", "value": 0.0},
        {"kind": "teacher", "key": "Dr. Kavya Rao", "value": -2.0},
        {"kind": "teacher", "key": "Ms. Fatima Sheikh", "value": 1.0},
        {"kind": "slot", "key": "14:00", "value": -12.0},
    ],
}


def table_of(page: str) -> str:
    """Just the figures card (the sidebar legend uses some of the same colours)."""
    start = page.index("Attention figures with")
    return page[start : page.index("Attendance by section")]


def admin_page() -> tuple[Any, str]:
    at = run_app("admin")
    assert not at.exception
    return at, html_of(at)


@pytest.fixture
def simulated(fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:  # noqa: F811
    monkeypatch.setattr(api, "get_simulation", lambda url: SIM)
    monkeypatch.setattr(api, "get_admin_overview", lambda url: OVERVIEW)
    return fake_backend


@pytest.fixture
def live(fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:  # noqa: F811
    monkeypatch.setattr(api, "get_admin_overview", lambda url: OVERVIEW)
    return fake_backend


# ---- with simulated data -----------------------------------------------------------------------------------------


def test_the_banner_and_header_come_first_and_the_timetable_stays_below(simulated: dict[str, list[Any]]) -> None:
    at, page = admin_page()
    assert page.index(BANNER) < page.index("Teacher feedback, all teachers")
    assert "Listed alphabetically, not ranked · figures hidden below 5 lectures" in page
    assert page.index("Teacher feedback, all teachers") < page.index("Timetable and sessions")
    assert page.index("Attention figures with 95% ranges") < page.index("Timetable and sessions")
    assert [s.value for s in at.subheader] == ["Weekly timetable", "Manual sessions", "Recent lectures", "Accounts"]


def test_the_figures_table_lists_teachers_as_given_with_range_bars_and_built_in_values(
    simulated: dict[str, list[Any]],
) -> None:
    _, page = admin_page()
    for heading in ("Teacher", "Lectures", "Class avg", "Vs own average", "Slot-adjusted", "Marks", "Built-in"):
        assert f">{heading}<" in page
    assert "range bars: −10 to +10 pts" in page
    names = [t["teacher"] for t in TEACHERS]
    assert [page.index(n) for n in names] == sorted(page.index(n) for n in names)  # in the backend's order
    for text in ("76.8%", "70.9%", "+4.8", "+3.1", "−2.4", "−0.6", "+0.8", "−1.1", "−2.2"):
        assert text in page
    assert "+4" in page and "−2<" in page  # the built-in column
    table = table_of(page)
    assert table.count("background:#9DB3D6") == 8  # a range bar for each of the 4 teachers with figures, two methods
    assert table.count("background:#1B5FD0;\"></div></div>") == 4  # primary dots for vs own average
    assert table.count("background:#18212B;\"></div></div>") == 4  # dark dots for slot-adjusted
    assert "Figures appear after 5 lectures" in page
    assert page.count("Figures appear after 5 lectures") == 1  # one hidden teacher


def test_a_hidden_teacher_keeps_the_class_average_and_loses_the_figures(simulated: dict[str, list[Any]]) -> None:
    _, page = admin_page()
    row = page[page.index("Ms. Fatima Sheikh") :]
    row = row[: row.index("Prof. Sanjay Pillai")]
    assert "69.0%" in row and "Figures appear after 5 lectures" in row
    assert "background:#9DB3D6" not in row


def test_the_check_card_says_which_figures_find_the_built_in_best_teacher(simulated: dict[str, list[Any]]) -> None:
    _, page = admin_page()
    assert "Does each figure find the built-in best teacher?" in page
    assert "Highest value under each method, simulated data only" in page
    assert "Plain class average" in page and "Vs own average" in page
    wrong = "color:#D4622A;\">Dr. Arjun Menon — wrong"
    assert wrong in page
    assert page.count("Dr. Meera Iyer — matches") == 2
    assert page.index("Dr. Arjun Menon — wrong") < page.index("Dr. Meera Iyer — matches")
    assert "color:#1B5FD0;\">Dr. Meera Iyer — matches" in page


def test_attendance_by_section_and_unassigned_windows(simulated: dict[str, list[Any]]) -> None:
    _, page = admin_page()
    assert "Attendance by section" in page
    assert "Section A · 30 students" in page and "93.1%" in page
    assert "Section B · 30 students" in page and "88.4%" in page
    assert "Unassigned records" in page and "36 windows" in page
    assert page.index("Attention figures with 95% ranges") < page.index("Attendance by section")


def test_the_page_says_it_is_feedback_and_names_the_slot_limit(simulated: dict[str, list[Any]]) -> None:
    at, _ = admin_page()
    captions = [c.value for c in at.caption]
    assert any(
        "feedback, not a ranking" in c and "cannot separate a teacher from a slot they always teach in" in c
        for c in captions
    )


def test_no_student_attention_appears_on_the_view(simulated: dict[str, list[Any]]) -> None:
    at, page = admin_page()
    assert "STU001" not in page and "SIM-A01" not in page
    assert len(at.dataframe) == 3  # the weekly timetable, the recent lectures and the accounts (emails only)


def test_the_copy_has_no_exclamation_marks_emoji_or_final_periods_on_labels(
    simulated: dict[str, list[Any]],
) -> None:
    _, page = admin_page()
    assert "!" not in page
    assert not any(ch in page for ch in "😀👍✅")
    for label in (
        "Teacher feedback, all teachers",
        "Attention figures with 95% ranges",
        "Attendance by section",
        "Does each figure find the built-in best teacher?",
        "Highest value under each method, simulated data only",
        "Unassigned records",
    ):
        assert label in page and f"{label}." not in page


# ---- without a simulation ---------------------------------------------------------------------------------------


def test_without_a_simulation_there_is_no_built_in_column_and_no_check_card(live: dict[str, list[Any]]) -> None:
    at, page = admin_page()
    assert "Simulated data" not in page
    assert ">Built-in<" not in page
    assert "Does each figure find the built-in best teacher?" not in page
    assert any(c.value == admin_figures.NO_BUILT_IN for c in at.caption)
    assert "+4.8" in page and "Attendance by section" in page  # the rest is unchanged


# ---- hidden teachers and an empty database -----------------------------------------------------------------------


def test_when_every_teacher_is_hidden_no_figures_or_top_teachers_are_shown(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = {**OVERVIEW, "teachers": [teacher(f"Dr. {n}", 3, 0.7, None, None) for n in ("Ash", "Birch")]}
    best_ash = {**SIM, "effects": [{"kind": "teacher", "key": "Dr. Ash", "value": 3.0}]}
    monkeypatch.setattr(api, "get_simulation", lambda url: best_ash)
    monkeypatch.setattr(api, "get_admin_overview", lambda url: hidden)
    _, page = admin_page()
    assert page.count("Figures appear after 5 lectures") == 2
    assert "background:#9DB3D6" not in table_of(page)
    assert "— matches" not in page and "— wrong" not in page
    assert page.count("No figures yet") == 3


def test_without_a_single_built_in_best_the_card_gives_way_to_a_plain_line(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    flat = {**SIM, "effects": [{**e, "value": 0.0} for e in SIM["effects"]]}
    monkeypatch.setattr(api, "get_simulation", lambda url: flat)
    monkeypatch.setattr(api, "get_admin_overview", lambda url: OVERVIEW)
    at, page = admin_page()
    assert "Does each figure find the built-in best teacher?" not in page
    assert any(c.value == admin_figures.NO_BUILT_IN_BEST for c in at.caption)


def test_an_empty_database_renders_with_plain_empty_state_lines(fake_backend: dict[str, list[Any]]) -> None:
    at, page = admin_page()
    assert "Teacher feedback, all teachers" in page
    assert admin_figures.NO_TEACHERS in page
    assert "No sections yet" in page and "0 windows" in page
    assert "Timetable and sessions" in page
    assert not at.error


def test_an_unreachable_overview_shows_an_error_and_still_the_timetable(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(url: str) -> dict[str, Any]:
        raise api.BackendError("Cannot reach the backend")

    monkeypatch.setattr(api, "get_admin_overview", broken)
    at, page = admin_page()
    assert any("Cannot reach the backend" in e.value for e in at.error)
    assert "Teacher feedback, all teachers" in page
    assert [s.value for s in at.subheader] == ["Weekly timetable", "Manual sessions", "Recent lectures", "Accounts"]
