"""The admin view's pure helpers: who comes first under each method, and the range bars.

No Streamlit and no backend here; the inputs are shaped like GET /api/admin/overview.
"""

from typing import Any

from dashboard import admin_figures
from dashboard.admin_figures import CheckRow, order_check


def teacher(
    name: str,
    average: float | None,
    own: float | None,
    slot: float | None,
    *,
    hidden: bool = False,
    simulated: bool = True,
) -> dict[str, Any]:
    def figure(value: float | None) -> dict[str, Any]:
        return {"value": value, "low": None if value is None else value - 1, "high": None if value is None else value + 1, "n_students": 20}

    return {
        "teacher": name,
        "simulated": simulated,
        "hidden": hidden,
        "n_lectures": 3 if hidden else 20,
        "class_average": average,
        "self_comparison": figure(None if hidden else own),
        "slot_adjusted": figure(None if hidden else slot),
    }


EFFECTS = [
    {"kind": "teacher", "key": "Menon", "value": 6.0},
    {"kind": "teacher", "key": "Bhat", "value": 0.0},
    {"kind": "teacher", "key": "Kulkarni", "value": 0.0},
    {"kind": "slot", "key": "14:00", "value": -12.0},
    {"kind": "section", "key": "SIM-A", "value": 5.0},
]
# Menon is built best. The class average puts Bhat first, the other two methods put Menon first.
TEACHERS = [
    teacher("Bhat", 0.73, 1.1, -0.9),
    teacher("Kulkarni", 0.70, 1.0, 0.0),
    teacher("Menon", 0.65, -2.5, 4.1),
]


def test_each_method_names_its_top_teacher_and_whether_it_is_the_built_in_best() -> None:
    assert order_check(TEACHERS, EFFECTS) == [
        CheckRow("Plain class average", "Bhat", False),
        CheckRow("Vs own average", "Bhat", False),
        CheckRow("Slot-adjusted", "Menon", True),
    ]


def test_a_method_that_puts_the_built_in_best_teacher_first_matches() -> None:
    teachers = [teacher("Bhat", 0.60, 0.0, 0.0), teacher("Kulkarni", 0.61, 0.5, 0.5), teacher("Menon", 0.70, 3.0, 2.0)]
    assert [r.matches for r in order_check(teachers, EFFECTS)] == [True, True, True]


def test_hidden_teachers_are_left_out_of_every_method_but_still_count_for_the_built_in_best() -> None:
    teachers = [teacher("Bhat", 0.90, 5.0, 5.0, hidden=True), *TEACHERS[1:]]
    rows = order_check(teachers, EFFECTS)
    assert [r.teacher for r in rows] == ["Kulkarni", "Kulkarni", "Menon"]
    # Menon hidden: the built-in best is still Menon, and nobody visible can match
    hidden_best = [TEACHERS[0], TEACHERS[1], teacher("Menon", 0.95, None, None, hidden=True)]
    assert [r.matches for r in order_check(hidden_best, EFFECTS)] == [False, False, False]


def test_a_teacher_with_no_stored_effect_counts_as_zero_and_live_teachers_are_not_compared() -> None:
    effects = [{"kind": "teacher", "key": "Menon", "value": 6.0}]
    teachers = [teacher("Live", 0.99, 9.0, 9.0, simulated=False), *TEACHERS]
    rows = order_check(teachers, effects)
    assert [r.teacher for r in rows] == ["Live", "Live", "Live"]  # figures of every shown teacher compete
    assert [r.matches for r in rows] == [False, False, False]  # the built-in best is simulated Menon

    assert [r.matches for r in order_check(TEACHERS, effects)] == [False, False, True]


def test_there_is_nothing_to_check_without_a_unique_built_in_best() -> None:
    flat = [{"kind": "teacher", "key": name, "value": 0.0} for name in ("Bhat", "Kulkarni", "Menon")]
    assert order_check(TEACHERS, flat) == []
    assert order_check(TEACHERS, []) == []
    assert order_check([], EFFECTS) == []
    assert order_check([teacher("Live", 0.7, 1.0, 1.0, simulated=False)], EFFECTS) == []


def test_a_method_with_no_figures_has_no_top_teacher() -> None:
    teachers = [teacher("Menon", None, None, None), teacher("Bhat", None, None, None)]
    rows = order_check(teachers, EFFECTS)
    assert rows == [
        CheckRow("Plain class average", None, None),
        CheckRow("Vs own average", None, None),
        CheckRow("Slot-adjusted", None, None),
    ]


def test_the_order_check_never_depends_on_list_order() -> None:
    assert order_check(list(reversed(TEACHERS)), EFFECTS) == order_check(TEACHERS, EFFECTS)


# ---- wording ----------------------------------------------------------------------------------------------


def test_signed_values_use_a_true_minus_and_one_decimal() -> None:
    assert admin_figures.signed(4.84) == "+4.8"
    assert admin_figures.signed(-0.62) == "−0.6"
    assert admin_figures.signed(0.0) == "0.0"


def test_built_in_effects_read_as_whole_points_where_they_can() -> None:
    assert admin_figures.built_in_text(6.0) == "+6"
    assert admin_figures.built_in_text(0.0) == "0"
    assert admin_figures.built_in_text(-2.0) == "−2"
    assert admin_figures.built_in_text(2.5) == "+2.5"


# ---- the range bar ----------------------------------------------------------------------------------------------


def test_the_bar_is_laid_out_on_a_scale_of_minus_ten_to_plus_ten_points_over_160_pixels() -> None:
    geometry = admin_figures.bar_geometry({"value": 4.8, "low": 1.9, "high": 7.6, "n_students": 58})
    assert geometry is not None
    assert abs(geometry.bar_left - 95.2) < 1e-9 and abs(geometry.bar_width - 45.6) < 1e-9  # 80 + 1.9 x 8, 5.7 x 8
    assert abs(geometry.dot_left - 113.4) < 1e-9  # 80 + 4.8 x 8 - 5


def test_a_bar_past_the_scale_is_cut_at_the_edge_and_one_student_has_a_dot_only() -> None:
    wide = admin_figures.bar_geometry({"value": 12.0, "low": -14.0, "high": 15.0, "n_students": 5})
    assert wide is not None and wide.bar_left == 0 and wide.bar_width == 160
    assert wide.dot_left == 155  # the dot stays inside the track
    single = admin_figures.bar_geometry({"value": -1.0, "low": None, "high": None, "n_students": 1})
    assert single is not None and single.bar_width == 0
    assert admin_figures.bar_geometry({"value": None, "low": None, "high": None, "n_students": 0}) is None
