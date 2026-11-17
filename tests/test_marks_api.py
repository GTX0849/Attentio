"""Marks through the backend HTTP API: uploads, coverage, the four groups, the marks figure and
a student's own marks.

The cohort is hand-built on the Monday timetable of Asia/Kolkata used by the other API tests:
Dr Iyer teaches Physics at 10:00 and Dr Rao Mathematics at 11:00, both to CSE-A, on the five
Mondays 02-23, 03-02, 03-09, 03-16 and 03-23. Students S1 to S4 are in CSE-A. Every window is in
view, so attention in Physics is each student's level below ("attention p" is p% attentive).

  student   02-23  03-02  03-09  03-16  03-23   Physics attention in test 1 (to 03-09) and test 2 (after)
  S1          80     80     80     80     80       0.8 and 0.8
  S2          50     50     50     70     70       0.5 and 0.7
  S3          70     70     70     70     70       0.7 and 0.7
  S4          40     40     40     40     40       0.4 and 0.4

Physics test 1 (03-09, out of 50): S1 45, S2 40, S3 20, S4 10, scores .9 .8 .4 .2, median .6,
percentiles S1 87.5, S2 62.5, S3 37.5, S4 12.5.
Physics test 2 (03-23, out of 100): S1 90, S2 50, S3 50, S4 30, median .5, percentiles 87.5, 50, 50, 12.5.
Mathematics test (03-23, out of 50, S4 away): S1 30, S2 45, S3 40, scores .6 .9 .8, percentiles
S1 16.67, S3 50, S2 83.33.
"""

import io
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeClock
from tests.test_student_profile_api import MATHS, PHYSICS, send
from tests.test_timetable_api import HEADER, upload

TIMETABLE = HEADER + (
    "Mon,10:00,10:50,R101,CSE-A,Physics,Dr Iyer\n"
    "Mon,11:00,11:50,R101,CSE-A,Mathematics,Dr Rao\n"
)
MONDAYS = ["2026-02-23", "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
PHYSICS_ATTENTION = {
    "S1": [80, 80, 80, 80, 80],
    "S2": [50, 50, 50, 70, 70],
    "S3": [70, 70, 70, 70, 70],
    "S4": [40, 40, 40, 40, 40],
}
TEST_1 = {"S1": 45, "S2": 40, "S3": 20, "S4": 10}  # out of 50
TEST_2 = {"S1": 90, "S2": 50, "S3": 50, "S4": 30}  # out of 100
MATHS_TEST = {"S1": 30, "S2": 45, "S3": 40}  # out of 50


def csv_of(marks: Mapping[str, float], maximum: float, header: str = "register_no,marks,max_marks") -> bytes:
    return (header + "\n" + "".join(f"{r},{m},{maximum}\n" for r, m in marks.items())).encode()


def xlsx_of(marks: Mapping[str, float], maximum: float) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Register No", "Marks", "Max Marks"])
    for register_no, mark in marks.items():
        sheet.append([register_no, mark, maximum])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def ids(client: TestClient, path: str) -> dict[str, int]:
    return {r["name"]: r["id"] for r in client.get(path).json()}


def post_marks(
    client: TestClient,
    teacher: str,
    subject: str,
    test_date: str,
    content: bytes,
    *,
    filename: str = "marks.csv",
    name: str = "",
    **extra: Any,
) -> Any:
    return client.post(
        "/api/marks/tests",
        files={"file": (filename, content, "application/octet-stream")},
        data={
            "teacher_id": ids(client, "/api/teachers")[teacher],
            "subject_id": ids(client, "/api/subjects")[subject],
            "test_date": test_date,
            "name": name,
            **extra,
        },
    )


def upload_ok(client: TestClient, *args: Any, **kwargs: Any) -> dict[str, Any]:
    resp = post_marks(client, *args, **kwargs)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert body["applied"] is True, body
    return body


@pytest.fixture
def cohort(client: TestClient, clock: FakeClock) -> TestClient:
    upload(client, TIMETABLE)
    for register_no in PHYSICS_ATTENTION:
        assert client.post("/api/students", json={"register_no": register_no, "section": "CSE-A"}).status_code == 201
    for index, day in enumerate(MONDAYS):
        for student, levels in PHYSICS_ATTENTION.items():
            p = levels[index]
            send(client, f"{day}T{PHYSICS}", 60, student=student, attentive=0.3 * p, distracted=0.3 * (100 - p))
            send(client, f"{day}T{MATHS}", 60, student=student, attentive=18, distracted=12)
    clock.set(datetime(2026, 3, 25, 4, 0, tzinfo=UTC))
    return client


def groups(client: TestClient, test_id: int, students: bool = True) -> dict[str, Any]:
    resp = client.get(f"/api/marks/tests/{test_id}/groups", params={"students": students})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def group_by_student(body: dict[str, Any]) -> dict[str, str | None]:
    return {s["register_no"]: s["group"] for s in body["students"]}


def teacher_id(client: TestClient, name: str) -> int:
    return ids(client, "/api/teachers")[name]


def summary(client: TestClient, name: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/api/teachers/{teacher_id(client, name)}/feedback").json()
    return body


# ---- uploading ---------------------------------------------------------------------------------


def test_a_csv_upload_stores_a_test_for_the_teacher_with_percentiles(cohort: TestClient) -> None:
    body = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), name="Mid-term 1")
    assert body["rows"] == 4 and body["replaced"] is False and body["errors"] == []

    tests = cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json()
    assert len(tests) == 1
    test = tests[0]
    assert (test["id"], test["name"], test["subject"], test["teacher"]) == (body["test_id"], "Mid-term 1", "Physics", "Dr Iyer")
    assert test["test_date"] == "2026-03-09" and test["n_students"] == 4 and test["sections"] == ["CSE-A"]
    assert test["simulated"] is False
    # The test belongs to its teacher only.
    assert cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Rao')}/marks/tests").json() == []

    rows = {r["register_no"]: r for r in groups(cohort, body["test_id"])["students"]}
    assert {s: r["marks_percentile"] for s, r in rows.items()} == {"S1": 87.5, "S2": 62.5, "S3": 37.5, "S4": 12.5}


def test_an_xlsx_upload_gives_the_same_test_as_the_csv(cohort: TestClient) -> None:
    body = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", xlsx_of(TEST_1, 50), filename="marks.xlsx")
    assert body["rows"] == 4
    rows = {r["register_no"]: r for r in groups(cohort, body["test_id"])["students"]}
    assert rows["S2"]["marks"] == 40 and rows["S2"]["max_marks"] == 50 and rows["S2"]["marks_percentile"] == 62.5


def test_register_numbers_match_ignoring_case_and_the_stored_one_is_the_students(cohort: TestClient) -> None:
    body = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of({"s1": 45, "S2": 40}, 50))
    assert {s["register_no"] for s in groups(cohort, body["test_id"])["students"]} == {"S1", "S2"}


def test_an_unknown_register_number_rejects_the_whole_upload_and_is_reported_by_line(cohort: TestClient) -> None:
    content = b"register_no,marks,max_marks\nS1,45,50\nNOPE1,30,50\nS3,20,50\nNOPE2,10,50\n"
    resp = post_marks(cohort, "Dr Iyer", "Physics", "2026-03-09", content)
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False and body["test_id"] is None and body["rows"] == 0
    assert [(e["row"], "NOPE" in e["message"] and "not a known student" in e["message"]) for e in body["errors"]] == [(3, True), (5, True)]
    # Nothing was stored, not even the good rows.
    assert cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json() == []


def test_missing_register_numbers_and_invalid_rows_are_all_reported_at_once(cohort: TestClient) -> None:
    content = (
        "register_no,marks,max_marks\n"
        "S1,45,50\n"
        ",30,50\n"  # line 3: no register number
        "S2,abc,50\n"  # line 4: not a number
        "S3,60,50\n"  # line 5: above the maximum
        "NOPE,10,50\n"  # line 6: unknown
        "S4,10,0\n"  # line 7: maximum not above zero
        "S1,40,50\n"  # line 8: repeated
    ).encode()
    body = post_marks(cohort, "Dr Iyer", "Physics", "2026-03-09", content).json()
    assert body["applied"] is False
    problems = {e["row"]: e["message"] for e in body["errors"]}
    assert sorted(problems) == [3, 4, 5, 6, 7, 8]
    assert "register number is empty" in problems[3]
    assert "'abc' is not a number" in problems[4]
    assert "above the maximum 50" in problems[5]
    assert "not a known student" in problems[6]
    assert "max_marks must be above zero" in problems[7]
    assert "already on line 2" in problems[8]
    assert cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json() == []


def test_a_file_that_cannot_be_read_and_unknown_teachers_subjects_and_dates_are_refused(cohort: TestClient) -> None:
    bad_columns = post_marks(cohort, "Dr Iyer", "Physics", "2026-03-09", b"register_no,marks\nS1,45\n")
    assert bad_columns.status_code == 422 and "max_marks" in bad_columns.json()["detail"]
    assert post_marks(cohort, "Dr Iyer", "Physics", "2026-03-09", b"").status_code == 422
    assert post_marks(cohort, "Dr Iyer", "Physics", "not a date", csv_of(TEST_1, 50)).status_code == 422
    ok = csv_of(TEST_1, 50)
    unknown_teacher = cohort.post(
        "/api/marks/tests",
        files={"file": ("m.csv", ok, "text/csv")},
        data={"teacher_id": 9999, "subject_id": ids(cohort, "/api/subjects")["Physics"], "test_date": "2026-03-09"},
    )
    assert unknown_teacher.status_code == 404
    unknown_subject = cohort.post(
        "/api/marks/tests",
        files={"file": ("m.csv", ok, "text/csv")},
        data={"teacher_id": teacher_id(cohort, "Dr Iyer"), "subject_id": 9999, "test_date": "2026-03-09"},
    )
    assert unknown_subject.status_code == 404
    assert cohort.get("/api/teachers/9999/marks/tests").status_code == 404


def test_uploading_the_same_test_again_replaces_its_marks_instead_of_adding_a_second_test(cohort: TestClient) -> None:
    first = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), name="Mid-term 1")
    second = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of({"S1": 10, "S2": 20}, 50), name="  Mid-term   1 ")
    assert second["replaced"] is True and second["test_id"] == first["test_id"] and second["rows"] == 2
    tests = cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json()
    assert [(t["n_students"], t["name"]) for t in tests] == [(2, "Mid-term 1")]
    # A different name or date is a different test.
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), name="Mid-term 1 (resit)")
    assert len(cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json()) == 2


def test_tests_are_listed_newest_first(cohort: TestClient) -> None:
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-23", csv_of(TEST_2, 100), name="Mid-term 2")
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), name="Mid-term 1")
    tests = cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json()
    assert [t["name"] for t in tests] == ["Mid-term 2", "Mid-term 1"]


# ---- covered lectures and the four groups ---------------------------------------------------------------


def test_a_first_test_covers_the_lectures_from_the_beginning_up_to_its_date(cohort: TestClient) -> None:
    body = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))
    assert groups(cohort, body["test_id"])["n_covered_lectures"] == 3  # 02-23, 03-02 and 03-09


def test_the_next_test_covers_only_the_lectures_since_the_previous_one(cohort: TestClient) -> None:
    second = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-23", csv_of(TEST_2, 100))
    assert groups(cohort, second["test_id"])["n_covered_lectures"] == 5  # no earlier test yet: from the beginning
    first = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))
    assert groups(cohort, first["test_id"])["n_covered_lectures"] == 3
    assert groups(cohort, second["test_id"])["n_covered_lectures"] == 2  # 03-16 and 03-23 now


def test_another_teachers_test_does_not_shorten_the_coverage(cohort: TestClient) -> None:
    upload_ok(cohort, "Dr Rao", "Mathematics", "2026-03-09", csv_of(MATHS_TEST, 50))
    iyer = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-23", csv_of(TEST_2, 100))
    body = groups(cohort, iyer["test_id"])
    assert body["n_covered_lectures"] == 5


def test_students_fall_in_the_four_groups_of_the_first_test(cohort: TestClient) -> None:
    body = groups(cohort, upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), name="Mid-term 1")["test_id"])
    assert body["attention_line"] == 0.6 and body["n_students"] == 4
    assert body["median_score"] == pytest.approx(0.6)
    assert group_by_student(body) == {
        "S1": "On track",  # attention 0.8, marks 0.9 above the median 0.6
        "S2": "Knows it already",  # attention 0.5, marks 0.8
        "S3": "Attentive but struggling",  # attention 0.7, marks 0.4
        "S4": "At risk",  # attention 0.4, marks 0.2
    }
    assert body["counts"] == {
        "On track": 1,
        "Attentive but struggling": 1,
        "Knows it already": 1,
        "At risk": 1,
        "Insufficient data": 0,
    }
    attention = {s["register_no"]: s["attention"] for s in body["students"]}
    assert attention == pytest.approx({"S1": 0.8, "S2": 0.5, "S3": 0.7, "S4": 0.4})


def test_the_second_test_uses_only_its_own_lectures_and_marks_at_the_median_are_not_above(cohort: TestClient) -> None:
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))
    body = groups(cohort, upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-23", csv_of(TEST_2, 100), name="Mid-term 2")["test_id"])
    assert body["median_score"] == pytest.approx(0.5)
    attention = {s["register_no"]: s["attention"] for s in body["students"]}
    assert attention == pytest.approx({"S1": 0.8, "S2": 0.7, "S3": 0.7, "S4": 0.4})  # S2 improved in the new lectures
    assert group_by_student(body) == {
        "S1": "On track",
        "S2": "Attentive but struggling",  # marks 0.5 equal to the median
        "S3": "Attentive but struggling",
        "S4": "At risk",
    }


def test_a_student_with_no_records_in_the_covered_lectures_is_insufficient_data_and_not_grouped(cohort: TestClient) -> None:
    assert cohort.post("/api/students", json={"register_no": "S5", "section": "CSE-A"}).status_code == 201
    marks = {**TEST_1, "S5": 25}
    body = groups(cohort, upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(marks, 50))["test_id"])
    assert body["counts"]["Insufficient data"] == 1 and sum(body["counts"].values()) == 5
    s5 = next(s for s in body["students"] if s["register_no"] == "S5")
    assert s5["group"] is None and s5["attention"] is None and s5["reason"] == "no_records"
    assert s5["marks_percentile"] == 50.0  # still counted in the test: 25 / 50 = 0.5 is 3rd of 5


def test_a_student_in_view_for_under_half_the_time_is_insufficient_data(cohort: TestClient, clock: FakeClock) -> None:
    assert cohort.post("/api/students", json={"register_no": "S5", "section": "CSE-A"}).status_code == 201
    send(cohort, f"2026-03-09T{PHYSICS}", 60, student="S5", attentive=3, distracted=2, not_visible=25)  # in view 1/6
    body = groups(cohort, upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of({**TEST_1, "S5": 25}, 50))["test_id"])
    s5 = next(s for s in body["students"] if s["register_no"] == "S5")
    assert s5["group"] is None and s5["reason"] == "low_presence"


def test_the_list_of_students_is_only_given_when_asked_for(cohort: TestClient) -> None:
    test_id = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))["test_id"]
    plain = groups(cohort, test_id, students=False)
    assert plain["students"] is None and plain["counts"]["On track"] == 1
    assert "S1" not in str(plain)
    assert cohort.get("/api/marks/tests/9999/groups").status_code == 404


# ---- the marks figure ------------------------------------------------------------------------------


def test_the_teachers_marks_figure_is_in_every_teacher_answer(cohort: TestClient) -> None:
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-23", csv_of(TEST_2, 100))
    upload_ok(cohort, "Dr Rao", "Mathematics", "2026-03-23", csv_of(MATHS_TEST, 50))
    # Dr Iyer: S1 87.5 against (87.5 + 87.5 + 16.67) / 3 is +23.61, S2 56.25 against 65.28 is -9.03,
    # S3 43.75 against 45.83 is -2.08, S4 12.5 against 12.5 is 0. The mean over the four is 3.125.
    # Dr Rao: S1 16.67 - 63.89 = -47.22, S2 83.33 - 65.28 = +18.06, S3 50 - 45.83 = +4.17, mean -8.33.
    iyer = summary(cohort, "Dr Iyer")["marks"]
    assert iyer["value"] == pytest.approx(3.125)
    assert iyer["n_tests"] == 2 and iyer["n_students"] == 4 and iyer["reason"] is None
    assert iyer["low"] is not None and iyer["low"] < 3.125 < iyer["high"]
    rao = summary(cohort, "Dr Rao")["marks"]
    assert rao["value"] == pytest.approx(-25 / 3) and rao["n_tests"] == 1 and rao["n_students"] == 3

    by_name = {t["teacher"]: t for t in cohort.get("/api/teachers/feedback").json()}
    assert by_name["Dr Iyer"]["marks"] == iyer and by_name["Dr Rao"]["marks"] == rao
    overview = {t["teacher"]: t for t in cohort.get("/api/admin/overview").json()["teachers"]}
    assert overview["Dr Iyer"]["marks"] == iyer and overview["Dr Rao"]["marks"] == rao


def test_the_figure_is_hidden_with_a_reason_for_a_teacher_with_no_tests(cohort: TestClient) -> None:
    assert summary(cohort, "Dr Iyer")["marks"] == {
        "value": None,
        "low": None,
        "high": None,
        "n_students": 0,
        "n_tests": 0,
        "reason": "no marks uploaded",
    }
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))
    assert summary(cohort, "Dr Rao")["marks"]["reason"] == "no marks uploaded"  # another teacher's tests do not count
    assert summary(cohort, "Dr Iyer")["marks"]["value"] == pytest.approx(0.0)  # only one subject: nothing to compare with


def test_the_admin_overview_has_the_figure_but_no_student(cohort: TestClient) -> None:
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))
    text = cohort.get("/api/admin/overview").text
    assert '"marks"' in text
    for register_no in PHYSICS_ATTENTION:
        assert f'"{register_no}"' not in text


def test_the_marks_figure_does_not_wait_for_five_lectures(client: TestClient) -> None:
    upload(client, TIMETABLE)
    assert client.post("/api/students", json={"register_no": "S1", "section": "CSE-A"}).status_code == 201
    upload_ok(client, "Dr Iyer", "Physics", "2026-03-09", csv_of({"S1": 40}, 50))
    body = summary(client, "Dr Iyer")
    assert body["hidden"] is True and body["marks"]["n_tests"] == 1 and body["marks"]["value"] == pytest.approx(0.0)


# ---- a student's own marks ----------------------------------------------------------------------------------


def test_a_student_sees_only_their_own_marks_and_percentiles(cohort: TestClient) -> None:
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), name="Mid-term 1")
    upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-23", csv_of(TEST_2, 100), name="Mid-term 2")
    upload_ok(cohort, "Dr Rao", "Mathematics", "2026-03-23", csv_of(MATHS_TEST, 50))
    resp = cohort.get("/api/students/S2/marks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["register_no"] == "S2" and body["simulated"] is False
    rows = body["tests"]
    # Newest date first; two tests on the same date, the later upload first.
    assert [(r["subject"], r["name"], r["test_date"]) for r in rows] == [
        ("Mathematics", "", "2026-03-23"),
        ("Physics", "Mid-term 2", "2026-03-23"),
        ("Physics", "Mid-term 1", "2026-03-09"),
    ]
    by_test = {(r["subject"], r["name"]): r for r in rows}
    first = by_test[("Physics", "Mid-term 1")]
    assert (first["marks"], first["max_marks"], first["percentile"], first["n_students"], first["teacher"]) == (40, 50, 62.5, 4, "Dr Iyer")
    assert by_test[("Physics", "Mid-term 2")]["percentile"] == 50.0
    assert by_test[("Mathematics", "")]["percentile"] == pytest.approx(250 / 3)
    # Isolation: nothing about the classmates, whose marks differ.
    text = resp.text
    for other in ("S1", "S3", "S4"):
        assert f'"{other}"' not in text
    for other_marks in ("20.0", "87.5"):  # S3 and S1 in the first test
        assert other_marks not in text
    s4 = cohort.get("/api/students/S4/marks").json()["tests"]
    assert len(s4) == 2 and {r["subject"] for r in s4} == {"Physics"}  # S4 was away from the Mathematics test


def test_a_student_with_no_marks_gets_an_empty_list_and_an_unknown_student_is_a_404(cohort: TestClient) -> None:
    assert cohort.get("/api/students/S1/marks").json()["tests"] == []
    assert cohort.get("/api/students/NOPE/marks").status_code == 404


# ---- simulated tests and the wipe -----------------------------------------------------------------------------


def test_a_simulated_upload_is_flagged_and_the_wipe_removes_simulated_tests_and_their_marks(cohort: TestClient) -> None:
    live = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), name="Live test")
    flagged = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-23", csv_of(TEST_2, 100), name="Simulated test", simulated="true")
    assert flagged["test_id"] != live["test_id"]
    flags = {t["name"]: t["simulated"] for t in cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json()}
    assert flags == {"Live test": False, "Simulated test": True}

    removed = cohort.delete("/api/simulation").json()
    assert removed["tests"] == 1 and removed["marks"] == 4
    left = cohort.get(f"/api/teachers/{teacher_id(cohort, 'Dr Iyer')}/marks/tests").json()
    assert [t["name"] for t in left] == ["Live test"] and left[0]["n_students"] == 4
    assert [r["name"] for r in cohort.get("/api/students/S1/marks").json()["tests"]] == ["Live test"]


def test_the_same_key_can_be_a_live_and_a_simulated_test_without_clashing(cohort: TestClient) -> None:
    live = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50))
    simulated = upload_ok(cohort, "Dr Iyer", "Physics", "2026-03-09", csv_of(TEST_1, 50), simulated="true")
    assert live["test_id"] != simulated["test_id"] and simulated["replaced"] is False


def test_a_simulated_teachers_test_is_simulated_without_asking(client: TestClient, clock: FakeClock) -> None:
    resp = client.post(
        "/api/timetable",
        files={"file": ("sim.csv", (HEADER + "Mon,10:00,10:50,SIM-R201,SIM-A,Algorithms,Dr Sim\n").encode(), "text/csv")},
        data={"simulated": "true"},
    )
    assert resp.status_code == 200
    assert client.post("/api/students", json={"register_no": "SIM-A01", "section": "SIM-A", "simulated": True}).status_code == 201
    upload_ok(client, "Dr Sim", "Algorithms", "2026-03-09", csv_of({"SIM-A01": 40}, 50))
    tests = client.get(f"/api/teachers/{teacher_id(client, 'Dr Sim')}/marks/tests").json()
    assert [t["simulated"] for t in tests] == [True]
    removed = client.delete("/api/simulation").json()
    assert removed["tests"] == 1 and removed["marks"] == 1 and removed["teachers"] == 1
    assert client.get("/api/subjects").json() == []  # the simulated subject went too


def test_the_wipe_removes_the_marks_of_a_simulated_student_from_a_live_test(client: TestClient) -> None:
    upload(client, TIMETABLE)
    assert client.post("/api/students", json={"register_no": "L1", "section": "CSE-A"}).status_code == 201
    assert client.post("/api/students", json={"register_no": "SIM1", "section": "CSE-A", "simulated": True}).status_code == 201
    upload_ok(client, "Dr Iyer", "Physics", "2026-03-09", csv_of({"L1": 40, "SIM1": 30}, 50))
    removed = client.delete("/api/simulation").json()
    assert removed["marks"] == 1 and removed["tests"] == 0
    tests = client.get(f"/api/teachers/{teacher_id(client, 'Dr Iyer')}/marks/tests").json()
    assert [t["n_students"] for t in tests] == [1]
