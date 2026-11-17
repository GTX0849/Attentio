"""Marks in the three views, with the backend calls faked (no server needed).

Teacher view: the marks card, the Student groups card with its 2 x 2 grid and the upload in the
sidebar. Admin view: the Marks column. Student view: the student's own marks.
"""

from datetime import date
from typing import Any

import pytest

from dashboard import admin_figures, api
from dashboard.views import student, teacher
from tests.test_admin_view_render import OVERVIEW, TEACHERS, admin_page
from tests.test_dashboard_render import (  # noqa: F401  (fake_backend is a fixture)
    CURRENT,
    NO_STUDENT_MARKS,
    PROFILE,
    fake_backend,
    fake_student,
    html_of,
    run_app,
    window,
)
from tests.test_teacher_view_render import FEEDBACK, teacher_data, teacher_page  # noqa: F401

MARKS = {"value": 6.4, "low": 2.1, "high": 10.7, "n_students": 58, "n_tests": 2, "reason": None}
TESTS = [
    {
        "id": 12, "name": "Mid-term 2 (SIM-A)", "teacher_id": 1, "teacher": "Dr. Meera Iyer", "subject_id": 1,
        "subject": "Data Structures", "test_date": "2026-03-18", "simulated": True, "n_students": 25, "sections": ["SIM-A"],
    },
    {
        "id": 11, "name": "Mid-term 1 (SIM-A)", "teacher_id": 1, "teacher": "Dr. Meera Iyer", "subject_id": 1,
        "subject": "Data Structures", "test_date": "2026-03-04", "simulated": True, "n_students": 27, "sections": ["SIM-A"],
    },
    {
        "id": 10, "name": "", "teacher_id": 1, "teacher": "Dr. Meera Iyer", "subject_id": 1,
        "subject": "Data Structures", "test_date": "2026-02-20", "simulated": False, "n_students": 20, "sections": [],
    },
]  # fmt: skip


def student_row(register_no: str, group: str | None, attention: float | None, marks: float = 30) -> dict[str, Any]:
    return {
        "register_no": register_no,
        "name": f"Student {register_no}",
        "section": "SIM-A",
        "marks": marks,
        "max_marks": 50,
        "marks_percentile": 62.5,
        "attention": attention,
        "attention_percentile": 40.0 if attention is not None else None,
        "reason": None if attention is not None else "low_presence",
        "group": group,
    }


GROUPS = {
    "test": TESTS[0],
    "attention_line": 0.6,
    "median_score": 0.6,
    "n_students": 25,
    "n_covered_lectures": 8,
    "counts": {
        "On track": 9,
        "Attentive but struggling": 6,
        "Knows it already": 4,
        "At risk": 5,
        "Insufficient data": 1,
    },
    "students": [
        student_row("SIM-A02", "At risk", 0.41),
        student_row("SIM-A01", "On track", 0.82, 45),
        student_row("SIM-A03", "Attentive but struggling", 0.71, 20),
        student_row("SIM-A04", None, None),
    ],
}


@pytest.fixture
def marks_data(teacher_data: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:  # noqa: F811
    """The teacher with a marks figure and three tests; the returned dict records which tests' groups were asked for."""
    asked: dict[str, list[Any]] = {"groups": [], "upload": []}
    monkeypatch.setattr(api, "get_teacher_feedback", lambda url, teacher_id: {**FEEDBACK, "marks": MARKS})
    monkeypatch.setattr(api, "list_teacher_marks_tests", lambda url, teacher_id: TESTS)

    def get_groups(url: str, test_id: int) -> dict[str, Any]:
        asked["groups"].append(test_id)
        return {**GROUPS, "test": next(t for t in TESTS if t["id"] == test_id)}

    monkeypatch.setattr(api, "get_marks_groups", get_groups)
    monkeypatch.setattr(api, "list_subjects", lambda url: [{"id": 5, "name": "Data Structures"}, {"id": 6, "name": "Physics"}])
    monkeypatch.setattr(
        api,
        "get_timetable",
        lambda url: {"timezone": "Asia/Kolkata", "slots": [{"teacher": "Dr. Meera Iyer", "subject": "Data Structures"}]},
    )

    def upload(url: str, teacher_id: int, subject_id: int, test_date: date, name: str, filename: str, content: bytes) -> dict[str, Any]:
        asked["upload"].append((teacher_id, subject_id, test_date, name, filename, content))
        return {"applied": True, "test_id": 12, "rows": 4, "replaced": False, "errors": []}

    monkeypatch.setattr(api, "upload_marks", upload)
    return asked


# ---- the marks card --------------------------------------------------------------------------------------


def test_the_marks_card_shows_the_figure_in_percentile_points_and_the_number_of_tests(marks_data: dict[str, list[Any]]) -> None:
    _, page = teacher_page()
    assert "Marks vs own percentile" in page
    assert "+6 pctl" in page and "From 2 tests" in page
    assert "No marks uploaded yet" not in page


def test_the_marks_figure_carries_a_caveat_that_it_reflects_who_sat_each_test(marks_data: dict[str, list[Any]]) -> None:
    at, _ = teacher_page()
    assert teacher.MARKS_CAVEAT in [c.value for c in at.caption]
    assert "who sat" in teacher.MARKS_CAVEAT and "not" in teacher.MARKS_CAVEAT


def test_the_wording_of_the_marks_card() -> None:
    def card(marks: dict[str, Any] | None) -> tuple[str, str]:
        return teacher.marks_card({"marks": marks})

    assert card({**MARKS, "value": -2.4, "n_tests": 1}) == ("-2 pctl", "From 1 test")
    assert card({**MARKS, "value": 0.3}) == ("0 pctl", "From 2 tests")
    assert card({**MARKS, "value": None, "n_tests": 0}) == ("—", "No marks uploaded yet")
    assert card(None) == ("—", "No marks uploaded yet")


def test_a_teacher_without_marks_sees_a_dash_and_says_no_marks_yet(teacher_data: dict[str, list[Any]]) -> None:
    at, page = teacher_page()
    assert "No marks uploaded yet" in page
    assert teacher.NO_GROUPS in [c.value for c in at.caption]
    assert "Student groups" in [s.value for s in at.subheader]
    assert "pctl" not in page
    assert teacher.MARKS_CAVEAT not in [c.value for c in at.caption]


# ---- the student groups card -------------------------------------------------------------------------------


def test_the_groups_card_is_titled_with_the_newest_test_and_states_the_two_lines(marks_data: dict[str, list[Any]]) -> None:
    at, page = teacher_page()
    assert marks_data["groups"] == [12]  # the newest test first
    assert "Student groups, Mid-term 2 (SIM-A)" in [s.value for s in at.subheader]
    assert "Attention above 60% × marks above class median" in [c.value for c in at.caption]
    picker = at.selectbox(key="marks_test_1")
    assert picker.options == [
        "Mid-term 2 (SIM-A) · Data Structures · 18 Mar",
        "Mid-term 1 (SIM-A) · Data Structures · 4 Mar",
        "Data Structures, 20 Feb · 20 Feb",
    ]


def test_the_grid_has_a_count_and_a_name_in_each_cell_and_at_risk_is_in_the_distracted_colour(
    marks_data: dict[str, list[Any]],
) -> None:
    _, page = teacher_page()
    grid = page[page.index("display:grid;grid-template-columns:1fr 1fr") :]
    end = "At risk</div></div></div>"
    grid = grid[: grid.index(end) + len(end)]  # the last cell is At risk
    names = ["On track", "Attentive but struggling", "Knows it already", "At risk"]
    positions = [grid.index(f">{n}</div>") for n in names]
    assert positions == sorted(positions)  # attention above the line on the top row, marks above the median first
    for count, name in ((9, "On track"), (6, "Attentive but struggling"), (4, "Knows it already"), (5, "At risk")):
        assert f">{count}</div><div" in grid and f">{name}</div>" in grid
    assert grid.count("border:1px solid #D4622A") == 1  # only At risk
    assert 'color:#D4622A;">5</div>' in page  # its count
    assert grid.count("background:#F8F9F7") == 3  # the other cells use the card background
    assert grid.count('color:#18212B;">') == 3  # the other counts are in the text colour


def test_choosing_another_test_shows_its_groups(marks_data: dict[str, list[Any]]) -> None:
    at, _ = teacher_page()
    at.selectbox(key="marks_test_1").select(11).run()
    assert not at.exception
    assert marks_data["groups"][-1] == 11
    assert "Student groups, Mid-term 1 (SIM-A)" in [s.value for s in at.subheader]


def test_a_test_with_no_name_is_titled_with_its_subject_and_date(marks_data: dict[str, list[Any]]) -> None:
    at, _ = teacher_page()
    at.selectbox(key="marks_test_1").select(10).run()
    assert "Student groups, Data Structures, 20 Feb" in [s.value for s in at.subheader]


def test_the_students_in_each_group_are_listed_for_the_teacher_in_group_order(marks_data: dict[str, list[Any]]) -> None:
    at, _ = teacher_page()
    assert [e.label for e in at.expander] == ["Students in each group"]
    frame = at.dataframe[-1].value
    assert list(frame.columns) == ["Group", "Register no", "Name", "Marks", "Percentile", "Attention"]
    assert frame["Group"].tolist() == ["On track", "Attentive but struggling", "At risk", "Insufficient data"]
    assert frame.iloc[0].tolist() == ["On track", "SIM-A01", "Student SIM-A01", "45 of 50", "62", "82%"]
    assert frame.iloc[3].tolist()[-1] == "—"


def test_students_who_could_not_be_grouped_are_counted_in_a_line(marks_data: dict[str, list[Any]]) -> None:
    at, _ = teacher_page()
    assert "1 student not grouped: not enough time in view over the covered lectures" in [c.value for c in at.caption]


def test_a_test_with_no_covered_lectures_says_so(marks_data: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "get_marks_groups", lambda url, test_id: {**GROUPS, "n_covered_lectures": 0, "students": []})
    at, _ = teacher_page()
    assert teacher.NO_GROUP_DATA in [c.value for c in at.caption]
    assert not at.exception


def test_the_marks_copy_has_no_exclamation_marks_emoji_or_final_periods_on_labels(marks_data: dict[str, list[Any]]) -> None:
    at, page = teacher_page()
    assert "!" not in page and not any(ch in page for ch in "😀👍✅")
    for label in ("Marks vs own percentile", "Student groups, Mid-term 2 (SIM-A)", "Attention above 60% × marks above class median"):
        assert label in page or label in [s.value for s in at.subheader] + [c.value for c in at.caption]
        assert not label.endswith(".")
    for caption in at.caption:
        assert "!" not in caption.value


def test_the_groups_card_says_when_the_backend_cannot_be_reached(marks_data: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    def down(url: str, teacher_id: int) -> list[dict[str, Any]]:
        raise api.BackendError("Cannot reach the backend at http://x")

    monkeypatch.setattr(api, "list_teacher_marks_tests", down)
    at, page = teacher_page()
    assert any("Cannot reach the backend" in e.value for e in at.error)
    assert "+6 pctl" in page  # the rest of the page is still drawn


# ---- the upload ------------------------------------------------------------------------------------------


def test_the_sidebar_has_the_upload_controls_for_the_selected_teacher(marks_data: dict[str, list[Any]]) -> None:
    at, _ = teacher_page()
    assert at.sidebar.file_uploader(key="marks_file").label == "Marks file"
    subject = at.sidebar.selectbox(key="marks_subject_1")
    assert subject.value == "Data Structures"  # the subject the teacher is timetabled for
    assert subject.options == ["Data Structures", "Physics"]
    assert at.sidebar.date_input(key="marks_date").label == "Test date"
    assert at.sidebar.text_input(key="marks_name").label == "Test name (optional)"
    button = at.sidebar.button(key="marks_upload")
    assert button.label == "Upload marks" and button.disabled  # no file chosen yet


def test_uploading_a_file_sends_it_for_the_teacher_subject_and_date_and_reports_the_result(
    marks_data: dict[str, list[Any]],
) -> None:
    at, _ = teacher_page()
    content = b"register_no,marks,max_marks\nSIM-A01,45,50\n"
    at.sidebar.file_uploader(key="marks_file").upload("midterm.csv", content).run()
    at.sidebar.date_input(key="marks_date").set_value(date(2026, 3, 9)).run()
    at.sidebar.text_input(key="marks_name").set_value("Mid-term 3").run()
    assert not at.sidebar.button(key="marks_upload").disabled
    at.sidebar.button(key="marks_upload").click().run()
    assert not at.exception
    assert marks_data["upload"] == [(1, 5, date(2026, 3, 9), "Mid-term 3", "midterm.csv", content)]
    assert "Marks uploaded: 4 students in Data Structures" in [s.value for s in at.success]


def test_a_refused_upload_lists_every_bad_row_and_says_nothing_was_stored(
    marks_data: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = {
        "applied": False, "test_id": None, "rows": 0, "replaced": False,
        "errors": [{"row": 3, "message": "register number NOPE is not a known student"}, {"row": 5, "message": "marks 60 is above the maximum 50"}],
    }  # fmt: skip
    monkeypatch.setattr(api, "upload_marks", lambda *args: refused)
    at, _ = teacher_page()
    at.sidebar.file_uploader(key="marks_file").upload("m.csv", b"x").run()
    at.sidebar.button(key="marks_upload").click().run()
    assert "Nothing was stored. 2 rows need fixing, then upload the file again" in [e.value for e in at.error]
    problems = at.dataframe[0].value
    assert list(problems.columns) == ["Row", "Problem"] and problems["Row"].tolist() == [3, 5]


def test_an_upload_the_backend_cannot_read_shows_its_message(marks_data: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> dict[str, Any]:
        raise api.BackendError("Missing column(s): max_marks. Expected: register_no, marks, max_marks")

    monkeypatch.setattr(api, "upload_marks", broken)
    at, _ = teacher_page()
    at.sidebar.file_uploader(key="marks_file").upload("m.csv", b"x").run()
    at.sidebar.button(key="marks_upload").click().run()
    assert any("Missing column(s): max_marks" in e.value for e in at.error)


def test_the_wording_of_the_upload_results() -> None:
    ok = {"subject": "Physics", "result": {"applied": True, "rows": 1, "replaced": True, "errors": []}}
    assert teacher.upload_message(ok) == ("success", "Marks uploaded: 1 student in Physics. It replaced the earlier upload of this test")
    one = {"result": {"applied": False, "errors": [{"row": 2, "message": "x"}]}}
    assert teacher.upload_message(one) == ("error", "Nothing was stored. 1 row needs fixing, then upload the file again")


# ---- the admin view --------------------------------------------------------------------------------------------


def with_marks(values: list[float | None]) -> dict[str, Any]:
    teachers = []
    for t, value in zip(TEACHERS, values, strict=True):
        marks = (
            {"value": None, "low": None, "high": None, "n_students": 0, "n_tests": 0, "reason": "no marks uploaded"}
            if value is None
            else {"value": value, "low": value - 3, "high": value + 3, "n_students": 50, "n_tests": 2, "reason": None}
        )
        teachers.append({**t, "marks": marks})
    return {**OVERVIEW, "teachers": teachers}


def test_the_admin_marks_column_shows_each_teachers_figure(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(api, "get_admin_overview", lambda url: with_marks([6.4, -2.2, None, 0.2, 12.6]))
    monkeypatch.setattr(api, "get_marks_groups", lambda *args: pytest.fail("the admin view never asks for students"))
    at, page = admin_page()
    table = page[page.index("Attention figures with") : page.index("Attendance by section")]
    row = lambda name: table[table.index(name) :].split("border-bottom:1px solid")[0]  # noqa: E731
    assert ">+6<" in row("Dr. Arjun Menon")
    assert ">−2<" in row("Dr. Kavya Rao")
    assert ">—<" in row("Dr. Meera Iyer")
    assert ">0<" in row("Ms. Fatima Sheikh")
    assert ">+13<" in row("Prof. Sanjay Pillai")
    assert admin_figures.MARKS_NOTE in [c.value for c in at.caption]
    assert "who sat each test" in admin_figures.MARKS_NOTE
    assert admin_figures.NO_MARKS not in [c.value for c in at.caption]
    assert "STU001" not in page and len(at.dataframe) == 2  # still no student on the view


def test_the_admin_view_says_no_marks_yet_when_nobody_has_uploaded_any(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(api, "get_admin_overview", lambda url: with_marks([None] * 5))
    at, page = admin_page()
    assert admin_figures.NO_MARKS in [c.value for c in at.caption]
    assert admin_figures.NO_MARKS.startswith("No marks yet")
    assert not at.exception


def test_the_marks_text_of_the_admin_column() -> None:
    assert admin_figures.marks_text({"value": 5.6}) == "+6"
    assert admin_figures.marks_text({"value": -1.6}) == "−2"
    assert admin_figures.marks_text({"value": 0.4}) == "0"
    assert admin_figures.marks_text({"value": None}) == "—"
    assert admin_figures.marks_text(None) == "—"


# ---- the student view --------------------------------------------------------------------------------------------


STUDENT_MARKS = {
    "register_no": "STU001",
    "simulated": False,
    "tests": [
        {"test_id": 2, "name": "Mid-term 2", "subject": "Physics", "teacher": "Dr. Meera Iyer", "test_date": "2026-03-18",
         "marks": 61, "max_marks": 100, "percentile": 62.5, "n_students": 25},
        {"test_id": 1, "name": "", "subject": "Data Structures", "teacher": "Dr. Meera Iyer", "test_date": "2026-03-04",
         "marks": 42.5, "max_marks": 50, "percentile": 87.5, "n_students": 27},
    ],
}  # fmt: skip


def test_the_student_sees_their_own_marks_and_percentile_for_each_test(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    fake_student(monkeypatch, CURRENT, PROFILE, [window(None)])
    monkeypatch.setattr(api, "get_student_marks", lambda url, register_no: STUDENT_MARKS)
    at = run_app()
    assert not at.exception
    assert "Marks" in [s.value for s in at.subheader]
    frame = at.dataframe[0].value  # before the attendance list
    assert list(frame.columns) == ["Date", "Test", "Subject", "Teacher", "Marks", "Percentile"]
    assert frame.iloc[0].tolist() == ["Wed 18 Mar", "Mid-term 2", "Physics", "Dr. Meera Iyer", "61 of 100", "62"]
    assert frame.iloc[1].tolist() == ["Wed 4 Mar", "—", "Data Structures", "Dr. Meera Iyer", "42.5 of 50", "88"]
    assert student.NO_MARKS not in [c.value for c in at.caption]


def test_a_student_with_no_marks_sees_no_marks_yet(fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    fake_student(monkeypatch, CURRENT, PROFILE, [window(None)])
    at = run_app()
    assert not at.exception
    assert student.NO_MARKS == "No marks yet"
    assert "No marks yet" in [c.value for c in at.caption]
    assert NO_STUDENT_MARKS["tests"] == []


def test_a_backend_error_on_the_marks_is_shown_in_place_and_the_rest_of_the_view_stays(
    fake_backend: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    def down(url: str, register_no: str) -> dict[str, Any]:
        raise api.BackendError("Cannot reach the backend at http://x")

    fake_student(monkeypatch, CURRENT, PROFILE, [window(None)])
    monkeypatch.setattr(api, "get_student_marks", down)
    at = run_app()
    assert not at.exception
    assert "Cannot reach the backend at http://x" in [c.value for c in at.caption]
    assert "Attendance" in [e.label for e in at.expander]
    assert "Your attention, live" in html_of(at)
