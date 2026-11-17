"""The admin view's teacher feedback: wording, the range bars and the built-in comparison.

Everything here is plain code over the answer of GET /api/admin/overview and the effects of
GET /api/simulation. The `*_html` functions return markup so tests can check it without Streamlit.
The view itself (`dashboard/views/admin.py`) only draws what these return.

Teachers are shown in the order the backend gives them (alphabetical) and never sorted by a score.
The only place a "first" teacher is named is the comparison with the built-in effects, which exists
to show whether each method finds the teacher the simulator made best.
"""

from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import Any

from dashboard.theme import (
    BORDER,
    CARD_BACKGROUND,
    PRIMARY,
    SECONDARY_TEXT,
    STATE_COLOURS,
    TEXT,
)

NO_DATA = "—"
MINUS = "−"
SCALE_POINTS = 10.0  # the range bars run from -10 to +10 points
TRACK_PX = 160
DOT_PX = 10
ZERO_PX = TRACK_PX / 2
PX_PER_POINT = TRACK_PX / (2 * SCALE_POINTS)
SIM_TEXT = "#7A4E00"  # status/sim-text in the design
RANGE_BAR = STATE_COLOURS["Neutral"]  # the pale blue
DISTRACTED = STATE_COLOURS["Distracted"]
COLUMNS = "minmax(0,1.4fr) minmax(0,0.6fr) minmax(0,0.9fr) minmax(0,1.9fr) minmax(0,1.9fr) minmax(0,0.7fr)"
BUILT_IN_COLUMN = " minmax(0,0.8fr)"

SCALE_NOTE = f"range bars: {MINUS}10 to +10 pts"
HEADERS = ["Teacher", "Lectures", "Class avg", "Vs own average", "Slot-adjusted", "Marks"]
NOTE = (
    "Results are feedback, not a ranking. The slot-adjusted figure cannot separate a teacher "
    "from a slot they always teach in."
)
NO_TEACHERS = "No teachers yet. Upload a timetable in the sidebar."
NO_SECTIONS = "No sections yet"
CHECK_TITLE = "Does each figure find the built-in best teacher?"
CHECK_SUBTITLE = "Highest value under each method, simulated data only"
NO_BUILT_IN = "Built-in effects are known only for simulated data."
NO_BUILT_IN_BEST = "No single built-in best teacher to compare with."


def hidden_text(min_lectures: int) -> str:
    return f"Figures appear after {min_lectures} lectures"


def signed(value: float) -> str:
    """"+4.8", "−0.6" (a true minus sign) and "0.0"."""
    text = f"{value:+.1f}"
    if text in ("+0.0", "-0.0"):
        return "0.0"
    return text.replace("-", MINUS)


def built_in_text(value: float) -> str:
    """The built-in effect in points: "+6", "0", "−2", or one decimal when it is not whole."""
    if value == 0:
        return "0"
    text = f"{value:+.0f}" if float(value).is_integer() else f"{value:+.1f}"
    return text.replace("-", MINUS)


def percent(value: float | None) -> str:
    return NO_DATA if value is None else f"{value:.1%}"


# ---- the range bar --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BarGeometry:
    """Pixel positions inside a 160 px track: the range bar and the dot (left edges)."""

    bar_left: float
    bar_width: float  # 0 when there is no range (one student)
    dot_left: float


def _px(points: float) -> float:
    """Pixels from the left of the track for a value in points, kept inside the track."""
    return min(float(TRACK_PX), max(0.0, ZERO_PX + points * PX_PER_POINT))


def bar_geometry(figure: dict[str, Any]) -> BarGeometry | None:
    """Where the range bar and dot go for a teacher figure; None when there is no value."""
    if figure["value"] is None:
        return None
    dot_left = _px(figure["value"]) - DOT_PX / 2
    if figure["low"] is None or figure["high"] is None:
        return BarGeometry(0.0, 0.0, dot_left)
    left = _px(figure["low"])
    return BarGeometry(left, _px(figure["high"]) - left, dot_left)


def range_cell_html(figure: dict[str, Any], dot_colour: str) -> str:
    """A track with a thin zero line, the range as a pale bar, the dot, and the value after it."""
    geometry = bar_geometry(figure)
    if geometry is None:
        return f'<div style="color:{SECONDARY_TEXT};font-size:14px;">{NO_DATA}</div>'
    bar = (
        f'<div style="position:absolute;left:{geometry.bar_left:.1f}px;top:5px;width:{geometry.bar_width:.1f}px;'
        f'height:4px;border-radius:2px;background:{RANGE_BAR};"></div>'
        if geometry.bar_width > 0
        else ""
    )
    track = (
        f'<div style="position:relative;flex:none;width:{TRACK_PX}px;height:14px;">'
        f'<div style="position:absolute;left:{ZERO_PX:.0f}px;top:0;width:1px;height:14px;background:{SECONDARY_TEXT};"></div>'
        f"{bar}"
        f'<div style="position:absolute;left:{geometry.dot_left:.1f}px;top:2px;width:{DOT_PX}px;height:{DOT_PX}px;'
        f'border-radius:50%;background:{dot_colour};"></div></div>'
    )
    return (
        f'<div style="display:flex;align-items:center;gap:10px;">{track}'
        f'<span style="font-size:14px;font-weight:500;color:{TEXT};font-variant-numeric:tabular-nums;">'
        f"{signed(figure['value'])}</span></div>"
    )


# ---- the figures table ------------------------------------------------------------------------------------------


def built_in_effects(effects: list[dict[str, Any]]) -> dict[str, float]:
    """Teacher name -> built-in effect in points, from the stored simulation effects."""
    return {e["key"]: float(e["value"]) for e in effects if e["kind"] == "teacher"}


def _cell(text: str, *, align: str = "left", colour: str = TEXT, weight: int = 400, size: int = 14) -> str:
    return (
        f'<div style="text-align:{align};font-size:{size}px;font-weight:{weight};color:{colour};'
        f'font-variant-numeric:tabular-nums;">{escape(text)}</div>'
    )


def _row_html(teacher: dict[str, Any], columns: str, min_lectures: int, effects: dict[str, float] | None) -> str:
    cells = [
        _cell(teacher["teacher"], weight=500),
        _cell(str(teacher["n_lectures"]), align="right"),
        _cell(percent(teacher["class_average"]), align="right", colour=SECONDARY_TEXT),
    ]
    if teacher["hidden"]:
        cells.append(
            f'<div style="grid-column:span 2;font-size:13px;color:{SECONDARY_TEXT};">'
            f"{escape(hidden_text(min_lectures))}</div>"
        )
    else:
        cells.append(range_cell_html(teacher["self_comparison"], PRIMARY))
        cells.append(range_cell_html(teacher["slot_adjusted"], TEXT))
    cells.append(_cell(NO_DATA, align="right", colour=SECONDARY_TEXT))  # marks: CLA-15
    if effects is not None:
        built_in = effects.get(teacher["teacher"], 0.0) if teacher["simulated"] else None
        cells.append(
            _cell(NO_DATA if built_in is None else built_in_text(built_in), align="right", colour=SIM_TEXT)
        )
    return (
        f'<div style="display:grid;grid-template-columns:{columns};align-items:center;gap:12px;'
        f'padding:11px 0;border-bottom:1px solid {BORDER};">{"".join(cells)}</div>'
    )


def figures_table_html(overview: dict[str, Any], effects: list[dict[str, Any]] | None) -> str:
    """The main card. `effects` is the simulation's effect list, or None when no simulation is loaded
    (then there is no Built-in column)."""
    built_in = None if effects is None else built_in_effects(effects)
    columns = COLUMNS + (BUILT_IN_COLUMN if built_in is not None else "")
    labels = [*HEADERS, *(["Built-in"] if built_in is not None else [])]
    aligns = ["left", "right", "right", "left", "left", "right", "right"]
    head = "".join(
        f'<div style="text-align:{aligns[i]};font-size:12px;font-weight:500;letter-spacing:0.03em;'
        f'text-transform:uppercase;color:{SIM_TEXT if label == "Built-in" else SECONDARY_TEXT};">{escape(label)}</div>'
        for i, label in enumerate(labels)
    )
    if overview["teachers"]:
        body = "".join(_row_html(t, columns, overview["min_lectures"], built_in) for t in overview["teachers"])
    else:
        body = f'<div style="padding:14px 0;font-size:14px;color:{SECONDARY_TEXT};">{escape(NO_TEACHERS)}</div>'
    level = f"{overview['range_level']:.0%}"
    return (
        f'<div style="border:1px solid {BORDER};border-radius:12px;padding:20px;overflow-x:auto;">'
        f'<div style="min-width:760px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;padding-bottom:6px;">'
        f'<span style="font-size:16px;font-weight:600;color:{TEXT};">Attention figures with {level} ranges</span>'
        f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:12px;color:{SECONDARY_TEXT};">'
        f"{escape(SCALE_NOTE)}</span></div>"
        f'<div style="display:grid;grid-template-columns:{columns};gap:12px;padding:8px 0;'
        f'border-bottom:1px solid {BORDER};">{head}</div>{body}</div></div>'
    )


# ---- attendance by section --------------------------------------------------------------------------------------


def students_text(count: int) -> str:
    return f"{count} student{'s' if count != 1 else ''}"


def windows_text(count: int) -> str:
    return f"{count} window{'s' if count != 1 else ''}"


def _line(left: str, right: str, *, secondary: bool, last: bool, strong: bool) -> str:
    border = "" if last else f"border-bottom:1px solid {BORDER};"
    left_colour = SECONDARY_TEXT if secondary else TEXT
    right_colour = SECONDARY_TEXT if secondary else TEXT
    return (
        f'<div style="display:flex;justify-content:space-between;padding:9px 0;{border}">'
        f'<span style="font-size:14px;color:{left_colour};">{escape(left)}</span>'
        f'<span style="font-size:14px;font-weight:{500 if strong else 400};color:{right_colour};'
        f'font-variant-numeric:tabular-nums;">{escape(right)}</span></div>'
    )


def attendance_card_html(overview: dict[str, Any]) -> str:
    """One row per section with its present rate, then the unassigned record windows."""
    lines = [
        _line(f"{s['section']} · {students_text(s['n_students'])}", percent(s["rate"]), secondary=False, last=False, strong=True)
        for s in overview["sections"]
    ]
    if not lines:
        lines.append(_line(NO_SECTIONS, NO_DATA, secondary=True, last=False, strong=False))
    lines.append(
        _line("Unassigned records", windows_text(overview["unassigned_windows"]), secondary=True, last=True, strong=False)
    )
    return (
        f'<div style="border:1px solid {BORDER};border-radius:12px;padding:20px;">'
        f'<div style="font-size:16px;font-weight:600;color:{TEXT};margin-bottom:10px;">Attendance by section</div>'
        f'{"".join(lines)}</div>'
    )


# ---- does each figure find the built-in best teacher? ---------------------------------------------------------------


@dataclass(frozen=True)
class CheckRow:
    method: str
    teacher: str | None  # the teacher with the highest value under this method; None with no figures
    matches: bool | None  # is it the built-in best teacher; None when there is no top teacher


METHODS: list[tuple[str, Callable[[dict[str, Any]], float | None]]] = [
    ("Plain class average", lambda t: t["class_average"]),
    ("Vs own average", lambda t: t["self_comparison"]["value"]),
    ("Slot-adjusted", lambda t: t["slot_adjusted"]["value"]),
]


def built_in_best(teachers: list[dict[str, Any]], effects: list[dict[str, Any]]) -> str | None:
    """The simulated teacher with the largest built-in effect (no stored effect counts as 0).

    None when there is no simulated teacher or the largest effect is shared, so nobody is "the best".
    """
    stored = built_in_effects(effects)
    simulated = {t["teacher"]: stored.get(t["teacher"], 0.0) for t in teachers if t["simulated"]}
    if not simulated:
        return None
    top = max(simulated.values())
    best = sorted(name for name, value in simulated.items() if value == top)
    return best[0] if len(best) == 1 else None


def order_check(teachers: list[dict[str, Any]], effects: list[dict[str, Any]]) -> list[CheckRow]:
    """For each method, the teacher with the highest value and whether that is the built-in best.

    Only teachers whose figures are shown take part, so a hidden teacher is never named. The
    built-in best does not depend on what is shown. Empty when there is no single built-in best.
    """
    best = built_in_best(teachers, effects)
    if best is None:
        return []
    shown = sorted((t for t in teachers if not t["hidden"]), key=lambda t: t["teacher"])
    rows = []
    for method, value_of in METHODS:
        scored = [(v, t["teacher"]) for t in shown if (v := value_of(t)) is not None]
        if not scored:
            rows.append(CheckRow(method, None, None))
            continue
        top = max(v for v, _ in scored)
        name = next(n for v, n in scored if v == top)  # alphabetical among equals
        rows.append(CheckRow(method, name, name == best))
    return rows


def check_card_html(rows: list[CheckRow]) -> str:
    """The card of three rows: "Dr. A — matches" in the primary colour or "Dr. B — wrong" in Distracted."""
    lines = []
    for row in rows:
        if row.teacher is None:
            result, colour = "No figures yet", SECONDARY_TEXT
        elif row.matches:
            result, colour = f"{row.teacher} — matches", PRIMARY
        else:
            result, colour = f"{row.teacher} — wrong", DISTRACTED
        lines.append(
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<span style="font-size:14px;color:{TEXT};">{escape(row.method)}</span>'
            f'<span style="font-size:14px;font-weight:500;color:{colour};">{escape(result)}</span></div>'
        )
    return (
        f'<div style="background:{CARD_BACKGROUND};border:1px solid {BORDER};border-radius:12px;padding:20px;">'
        f'<div style="font-size:16px;font-weight:600;color:{TEXT};">{escape(CHECK_TITLE)}</div>'
        f'<div style="font-size:13px;color:{SECONDARY_TEXT};margin:2px 0 12px 0;">{escape(CHECK_SUBTITLE)}</div>'
        f'<div style="display:flex;flex-direction:column;gap:8px;">{"".join(lines)}</div></div>'
    )
