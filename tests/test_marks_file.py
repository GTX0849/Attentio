"""Reading a marks file (CSV or xlsx): header variants, rows and row problems. No database."""

import io
from pathlib import Path

import openpyxl
import pytest

from backend.marks_file import MarksFileError, parse_marks_file


def xlsx(rows: list[list[object]], extra_sheet: bool = False) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        book.create_sheet("Other").append(["register_no", "marks", "max_marks"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_a_csv_with_the_three_columns_is_read_row_by_row() -> None:
    parsed = parse_marks_file(b"register_no,marks,max_marks\nA1,45,50\nA2,38.5,50\n", "marks.csv")
    assert parsed.errors == []
    assert [(r.row, r.register_no, r.marks, r.max_marks) for r in parsed.rows] == [(2, "A1", 45.0, 50.0), (3, "A2", 38.5, 50.0)]


@pytest.mark.parametrize(
    "header",
    [
        "REGISTER_NO,Marks,MAX_MARKS",
        "Register No,Marks Obtained,Max Marks",
        "Register Number,Score,Maximum Marks",
        "  reg no , mark , out of ",
        "Roll No,Marks,Total Marks",
        "register-no;marks;max-marks".replace(";", ","),
    ],
)
def test_headers_are_matched_ignoring_case_spaces_and_common_variants(header: str) -> None:
    parsed = parse_marks_file(f"{header}\nA1,45,50\n".encode(), "marks.csv")
    assert parsed.errors == []
    assert [(r.register_no, r.marks, r.max_marks) for r in parsed.rows] == [("A1", 45.0, 50.0)]


def test_a_byte_order_mark_blank_lines_and_extra_columns_are_tolerated() -> None:
    raw = "﻿register_no,name,marks,max_marks\nA1,Asha,45,50\n\n,,,\nA2,Bo,30,50\n".encode()
    parsed = parse_marks_file(raw, "m.csv")
    assert parsed.errors == []
    assert [r.register_no for r in parsed.rows] == ["A1", "A2"]
    assert [r.row for r in parsed.rows] == [2, 5]  # line numbers count the blank ones


def test_each_bad_row_is_reported_with_its_line_and_the_good_rows_are_kept() -> None:
    raw = (
        "register_no,marks,max_marks\n"
        "A1,45,50\n"  # line 2 fine
        ",30,50\n"  # line 3 no register number
        "A3,abc,50\n"  # line 4 not a number
        "A4,60,50\n"  # line 5 above the maximum
        "A5,-1,50\n"  # line 6 negative
        "A6,10,0\n"  # line 7 maximum not above zero
        "A7,,50\n"  # line 8 marks empty
        "A8,10,\n"  # line 9 maximum empty
        "A1,40,50\n"  # line 10 repeated register number
        "A9,nan,50\n"  # line 11 not a finite number
    ).encode()
    parsed = parse_marks_file(raw, "m.csv")
    assert [r.register_no for r in parsed.rows] == ["A1"]
    problems = {e.row: e.message for e in parsed.errors}
    assert sorted(problems) == [3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert "register number is empty" in problems[3]
    assert "marks 'abc' is not a number" in problems[4]
    assert "above the maximum" in problems[5]
    assert "below zero" in problems[6]
    assert "max_marks must be above zero" in problems[7]
    assert "marks is empty" in problems[8]
    assert "max_marks is empty" in problems[9]
    assert "already on line 2" in problems[10]
    assert "not a number" in problems[11]


def test_marks_equal_to_the_maximum_and_zero_marks_are_valid() -> None:
    parsed = parse_marks_file(b"register_no,marks,max_marks\nA1,50,50\nA2,0,50\n", "m.csv")
    assert parsed.errors == [] and [r.marks for r in parsed.rows] == [50.0, 0.0]


def test_an_xlsx_file_is_read_from_its_first_sheet_with_numbers_and_text() -> None:
    raw = xlsx([["Register No", "Marks", "Max Marks"], ["A1", 45, 50], ["A2", "38.5", 50.0], [101, 20, 50]], extra_sheet=True)
    parsed = parse_marks_file(raw, "marks.xlsx")
    assert parsed.errors == []
    assert [(r.register_no, r.marks, r.max_marks) for r in parsed.rows] == [("A1", 45.0, 50.0), ("A2", 38.5, 50.0), ("101", 20.0, 50.0)]


def test_an_xlsx_is_recognised_by_its_content_not_only_its_name() -> None:
    raw = xlsx([["register_no", "marks", "max_marks"], ["A1", 1, 2]])
    assert parse_marks_file(raw, "upload.dat").rows[0].register_no == "A1"


def test_an_xlsx_skips_blank_rows_and_reports_bad_ones_by_row_number() -> None:
    raw = xlsx([["register_no", "marks", "max_marks"], ["A1", 45, 50], [None, None, None], ["A3", "x", 50]])
    parsed = parse_marks_file(raw, "m.xlsx")
    assert [r.row for r in parsed.rows] == [2]
    assert [(e.row, "not a number" in e.message) for e in parsed.errors] == [(4, True)]


def test_a_file_without_the_required_columns_is_refused_naming_them() -> None:
    with pytest.raises(MarksFileError, match="Missing column.*max_marks"):
        parse_marks_file(b"register_no,marks\nA1,45\n", "m.csv")


def test_an_empty_file_a_header_only_file_and_a_non_text_file_are_refused() -> None:
    with pytest.raises(MarksFileError, match="empty"):
        parse_marks_file(b"", "m.csv")
    with pytest.raises(MarksFileError, match="no rows"):
        parse_marks_file(b"register_no,marks,max_marks\n", "m.csv")
    with pytest.raises(MarksFileError, match="UTF-8"):
        parse_marks_file(b"register_no,marks,max_marks\n\xff\xfe\n", "m.csv")
    with pytest.raises(MarksFileError, match="Excel"):
        parse_marks_file(b"PK\x03\x04 not really a workbook", "m.xlsx")


def test_the_sample_marks_file_in_the_samples_folder_is_valid() -> None:
    sample = Path(__file__).resolve().parent.parent / "samples" / "marks.csv"
    parsed = parse_marks_file(sample.read_bytes(), sample.name)
    assert parsed.errors == [] and len(parsed.rows) >= 20
    assert all(r.register_no.startswith("SIM-A") and r.max_marks == 50 for r in parsed.rows)
