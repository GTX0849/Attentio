"""Reading a marks file: a CSV or an Excel (.xlsx) sheet with register_no, marks and max_marks.

Header names are matched ignoring case, spaces and punctuation, with a few common variants
("Register No", "Roll No", "Marks Obtained", "Out of", ...). Extra columns are ignored. For an
.xlsx file the first sheet is read, and its first non-empty row is the header. A file is taken as
Excel when it starts like a zip archive or its name ends in .xlsx, else as UTF-8 CSV.

Row problems (an empty or repeated register number, marks that are not numbers, below zero or
above the maximum, a maximum not above zero) are collected with their line numbers and returned
with the good rows; whether the upload goes ahead is decided by the caller. A file that cannot be
read as a whole raises `MarksFileError`.
"""

import csv
import io
import math
import re
from dataclasses import dataclass, field

import openpyxl

COLUMNS = ["register_no", "marks", "max_marks"]
# Normalised header (lower case, letters and digits only) -> the column it stands for.
HEADER_VARIANTS: dict[str, str] = {
    **dict.fromkeys(
        ["registerno", "registernumber", "regno", "registrationno", "registrationnumber", "rollno", "rollnumber", "studentid", "studentno"],
        "register_no",
    ),
    **dict.fromkeys(["marks", "mark", "score", "marksobtained", "obtained", "obtainedmarks", "marksscored"], "marks"),
    **dict.fromkeys(
        ["maxmarks", "maxmark", "maximummarks", "maximum", "max", "outof", "total", "totalmarks", "fullmarks", "maxscore"],
        "max_marks",
    ),
}
_ZIP_MAGIC = b"PK\x03\x04"


class MarksFileError(Exception):
    """The file as a whole cannot be read (missing columns, wrong encoding, empty)."""


@dataclass(frozen=True)
class RowError:
    row: int  # line number in the file; the header is the first line
    message: str


@dataclass(frozen=True)
class MarksRow:
    row: int
    register_no: str
    marks: float
    max_marks: float


@dataclass
class ParsedMarks:
    rows: list[MarksRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)


def _clean(value: object) -> str:
    """Text of a cell: whole numbers lose their ".0" (a register number typed as a number in Excel)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


def _normal(header: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean(header).lower())


def _read_xlsx(raw: bytes) -> list[tuple[int, list[str]]]:
    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:  # openpyxl and zipfile raise many kinds of errors for a damaged file
        raise MarksFileError(f"This file cannot be read as an Excel (.xlsx) file: {e}") from e
    try:
        sheet = book.worksheets[0]
        return [(number, [_clean(cell) for cell in values]) for number, values in enumerate(sheet.iter_rows(values_only=True), start=1)]
    finally:
        book.close()


def _read_csv(raw: bytes) -> list[tuple[int, list[str]]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise MarksFileError("The file is not UTF-8 text. Save it as CSV (UTF-8) or upload an Excel (.xlsx) file") from e
    return [(number, [_clean(cell) for cell in values]) for number, values in enumerate(csv.reader(io.StringIO(text)), start=1)]


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _fmt(value: float) -> str:
    return f"{value:g}"


def _row(line: int, values: dict[str, str]) -> MarksRow | str:
    """A valid row, or the sentence that says what is wrong with it."""
    problems: list[str] = []
    register_no = values["register_no"]
    if not register_no:
        problems.append("register number is empty")
    marks = max_marks = None
    if not values["marks"]:
        problems.append("marks is empty")
    else:
        marks = _number(values["marks"])
        if marks is None:
            problems.append(f"marks '{values['marks']}' is not a number")
        elif marks < 0:
            problems.append(f"marks {_fmt(marks)} is below zero")
    if not values["max_marks"]:
        problems.append("max_marks is empty")
    else:
        max_marks = _number(values["max_marks"])
        if max_marks is None:
            problems.append(f"max_marks '{values['max_marks']}' is not a number")
        elif max_marks <= 0:
            problems.append("max_marks must be above zero")
    if marks is not None and max_marks is not None and marks >= 0 and max_marks > 0 and marks > max_marks:
        problems.append(f"marks {_fmt(marks)} is above the maximum {_fmt(max_marks)}")
    if problems or marks is None or max_marks is None:
        return "; ".join(problems)
    return MarksRow(line, register_no, marks, max_marks)


def _is_xlsx(raw: bytes, filename: str) -> bool:
    return raw.startswith(_ZIP_MAGIC) or filename.lower().endswith(".xlsx")


def parse_marks_file(raw: bytes, filename: str) -> ParsedMarks:
    """Read and validate the file. Row problems are collected, not raised."""
    if not raw.strip():
        raise MarksFileError("The file is empty")
    lines = _read_xlsx(raw) if _is_xlsx(raw, filename) else _read_csv(raw)
    header: dict[str, int] | None = None
    parsed = ParsedMarks()
    seen: dict[str, int] = {}
    for number, cells in lines:
        if header is None:
            if not any(cells):
                continue
            header = {}
            for index, cell in enumerate(cells):
                column = HEADER_VARIANTS.get(_normal(cell))
                if column is not None and column not in header:
                    header[column] = index
            missing = [c for c in COLUMNS if c not in header]
            if missing:
                raise MarksFileError(f"Missing column(s): {', '.join(missing)}. Expected: {', '.join(COLUMNS)}")
            continue
        if not any(cells):
            continue  # blank line
        values = {c: (cells[i] if i < len(cells) else "") for c, i in header.items()}
        row = _row(number, values)
        if isinstance(row, str):
            parsed.errors.append(RowError(number, row))
            continue
        key = row.register_no.casefold()
        if key in seen:
            parsed.errors.append(RowError(number, f"register number {row.register_no} is already on line {seen[key]}"))
            continue
        seen[key] = number
        parsed.rows.append(row)
    if header is None:
        raise MarksFileError("The file is empty")
    if not parsed.rows and not parsed.errors:
        raise MarksFileError("The file has no rows")
    return parsed

