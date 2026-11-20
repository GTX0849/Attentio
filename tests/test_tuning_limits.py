"""The pure tuning analysis: limits from labelled rows, with known answers worked out by hand."""

from dataclasses import replace

import pytest

from shared.config import load_config
from worker.limits import CurrentLimits, TuneRow, TuningConfig, compute_limits, label_seconds, missing_data

CFG = TuningConfig.from_config(load_config().raw)
CURRENT = CurrentLimits(pitch_attentive_min_deg=-30.0, yaw_max_deg=35.0, pitch_neutral_max_deg=38.0)

WATCHING, NOTES, PHONE = 1, 2, 3


def rows(label: int, pitches: list[float], yaws: list[float] | None = None) -> list[TuneRow]:
    yaws = yaws if yaws is not None else [0.0] * len(pitches)
    return [TuneRow(t=float(i) * 0.1, label=label, rel_pitch=p, rel_yaw=y) for i, (p, y) in enumerate(zip(pitches, yaws))]


def test_the_up_limit_is_the_95th_percentile_of_looking_up_plus_the_margin() -> None:
    # upward deviation 0.3, 0.6, ... 30.0 over 100 rows; the 95th of 100 (nearest rank) is 28.5
    watching = rows(WATCHING, [-0.3 * i for i in range(1, 101)])
    result = compute_limits(watching, CURRENT, CFG)
    assert result.up_percentile_deg == 28.5
    assert result.pitch_attentive_min_deg == -31.5  # 28.5 + 3 degrees, written as a negative pitch


def test_rows_looking_down_count_as_no_upward_deviation() -> None:
    # 80 rows looking down, 20 looking up by 1..20 degrees: the 95th of 100 is the 15th looking-up row, 15 degrees
    pitches = [5.0] * 80 + [-float(i) for i in range(1, 21)]
    result = compute_limits(rows(WATCHING, pitches), CURRENT, CFG)
    assert result.up_percentile_deg == 15.0
    assert result.pitch_attentive_min_deg == -18.0


def test_the_side_limit_is_the_95th_percentile_of_the_turn_either_way_plus_the_margin() -> None:
    # |rel_yaw| 0.2, 0.4, ... 20.0 (half of them turned left); the 95th of 100 is 19.0
    yaws = [(0.2 * i) * (1 if i % 2 else -1) for i in range(1, 101)]
    result = compute_limits(rows(WATCHING, [0.0] * 100, yaws), CURRENT, CFG)
    assert result.side_percentile_deg == 19.0
    assert result.yaw_max_deg == 22.0


def test_only_watching_the_teacher_rows_set_the_up_and_side_limits() -> None:
    watching = rows(WATCHING, [-12.0] * 20, [8.0] * 20)
    others = rows(NOTES, [-40.0] * 20, [60.0] * 20) + rows(4, [-40.0] * 20, [60.0] * 20) + rows(0, [-40.0] * 5, [60.0] * 5)
    result = compute_limits(watching + others, CURRENT, CFG)
    assert result.pitch_attentive_min_deg == -15.0  # 12 + 3
    assert result.yaw_max_deg == 11.0  # 8 + 3


def test_the_up_and_side_limits_are_clamped() -> None:
    wild = rows(WATCHING, [-70.0] * 20, [90.0] * 20)
    result = compute_limits(wild, CURRENT, CFG)
    assert result.pitch_attentive_min_deg == -CFG.max_up_deg == -45.0
    assert result.yaw_max_deg == CFG.max_side_deg == 50.0
    still = rows(WATCHING, [0.0] * 20, [0.0] * 20)
    result = compute_limits(still, CURRENT, CFG)
    assert result.pitch_attentive_min_deg == -CFG.min_up_deg == -10.0
    assert result.yaw_max_deg == CFG.min_side_deg == 10.0


def test_the_margin_is_a_config_value() -> None:
    watching = rows(WATCHING, [-12.0] * 20, [8.0] * 20)
    result = compute_limits(watching, CURRENT, replace(CFG, margin_deg=5.0))
    assert result.pitch_attentive_min_deg == -17.0
    assert result.yaw_max_deg == 13.0


def spread(label: int, start: float, stop: float, n: int) -> list[TuneRow]:
    """`n` rows with rel_pitch evenly from `start` to `stop`."""
    step = (stop - start) / (n - 1)
    return rows(label, [start + step * i for i in range(n)])


def test_the_note_phone_boundary_sits_in_the_gap_between_cleanly_separated_groups() -> None:
    notes = spread(NOTES, 20.0, 30.0, 41)  # looking down 20 to 30 degrees
    phone = spread(PHONE, 45.0, 55.0, 41)  # holding a phone, 45 to 55
    result = compute_limits(notes + phone, CURRENT, CFG)
    assert result.pitch_neutral_max_deg == 37.5  # halfway between the highest notes row and the lowest phone row
    assert result.separation_accuracy == 1.0
    assert result.boundary_kept is False


def test_overlapping_groups_keep_the_current_boundary_and_say_so() -> None:
    notes = spread(NOTES, 15.0, 45.0, 61)
    phone = spread(PHONE, 20.0, 50.0, 61)  # the same sort of angles: nothing separates them
    result = compute_limits(notes + phone, CURRENT, CFG)
    assert result.separation_accuracy < CFG.min_separation
    assert result.boundary_kept is True
    assert result.pitch_neutral_max_deg == CURRENT.pitch_neutral_max_deg == 38.0


def test_groups_that_are_partly_mixed_but_separate_well_enough_still_set_the_boundary() -> None:
    # 5 of 50 notes rows are low-ish phone angles (36) and 2 of 50 phone rows are note-like (32)
    notes = rows(NOTES, [20.0] * 45 + [36.0] * 5)
    phone = rows(PHONE, [32.0] * 2 + [50.0] * 48)
    result = compute_limits(notes + phone, CURRENT, CFG)
    # at 43 every notes row is right (100%) and 2 of 50 phone rows are wrong (96%): (1.0 + 0.96) / 2
    assert result.pitch_neutral_max_deg == 43.0
    assert result.separation_accuracy == pytest.approx(0.98)
    assert result.boundary_kept is False


def test_the_boundary_is_clamped_but_the_accuracy_reported_is_the_groups_own() -> None:
    low = compute_limits(spread(NOTES, 2.0, 4.0, 21) + spread(PHONE, 8.0, 10.0, 21), CURRENT, CFG)
    assert low.pitch_neutral_max_deg == CFG.min_neutral_max_deg == 20.0
    assert low.separation_accuracy == 1.0
    high = compute_limits(spread(NOTES, 50.0, 52.0, 21) + spread(PHONE, 70.0, 72.0, 21), CURRENT, CFG)
    assert high.pitch_neutral_max_deg == CFG.max_neutral_max_deg == 60.0


def test_phone_rows_not_further_down_than_notes_rows_keep_the_current_boundary() -> None:
    notes = spread(NOTES, 30.0, 40.0, 21)
    phone = spread(PHONE, 10.0, 20.0, 21)  # the phone is held higher than the notes: no boundary to find
    result = compute_limits(notes + phone, CURRENT, CFG)
    assert result.boundary_kept is True
    assert result.pitch_neutral_max_deg == 38.0
    assert result.separation_accuracy == 0.5


def test_with_no_notes_or_phone_rows_the_boundary_is_kept() -> None:
    result = compute_limits(rows(WATCHING, [0.0] * 10), CURRENT, CFG)
    assert result.boundary_kept is True
    assert result.pitch_neutral_max_deg == 38.0


def ticks(label: int, start: float, seconds: float, step: float = 0.1) -> list[TuneRow]:
    n = round(seconds / step)
    return [TuneRow(t=start + i * step, label=label, rel_pitch=0.0, rel_yaw=0.0) for i in range(n)]


def test_durations_come_from_row_timestamps_each_row_lasting_until_the_next() -> None:
    run = ticks(WATCHING, 1000.0, 10.0) + ticks(NOTES, 1010.0, 5.0) + [TuneRow(1015.0, NOTES, 0.0, 0.0)]
    seconds = label_seconds([run], CFG.max_gap_s)
    assert seconds[WATCHING] == pytest.approx(10.0)
    assert seconds[NOTES] == pytest.approx(5.0)
    assert seconds[PHONE] == 0.0


def test_gaps_over_two_seconds_do_not_count_as_recorded_time() -> None:
    # 5 s of watching, then the face is lost for 30 s, then 5 s more
    run = ticks(WATCHING, 1000.0, 5.0) + ticks(WATCHING, 1035.0, 5.0)
    assert label_seconds([run], CFG.max_gap_s)[WATCHING] == pytest.approx(9.8)
    gap_exactly_at_the_limit = [TuneRow(0.0, WATCHING, 0, 0), TuneRow(2.0, WATCHING, 0, 0), TuneRow(4.5, WATCHING, 0, 0)]
    assert label_seconds([gap_exactly_at_the_limit], 2.0)[WATCHING] == 2.0  # 2.0 counts, 2.5 does not


def test_runs_are_added_up_and_never_joined_across_a_run_boundary() -> None:
    first = ticks(WATCHING, 1000.0, 20.0)
    second = ticks(WATCHING, 1020.05, 20.0)  # starts 0.05 s after the first one's last tick: still a separate run
    seconds = label_seconds([first, second], CFG.max_gap_s)
    assert seconds[WATCHING] == pytest.approx(19.9 + 19.9)


def test_label_zero_and_the_other_labels_are_not_counted_as_data() -> None:
    run = ticks(0, 0.0, 50.0) + ticks(4, 50.0, 10.0) + ticks(5, 60.0, 10.0)
    assert sum(label_seconds([run], CFG.max_gap_s)[label] for label in (WATCHING, NOTES, PHONE)) == 0.0


def test_enough_data_has_nothing_missing() -> None:
    seconds = {WATCHING: 60.0, NOTES: 30.0, PHONE: 31.0}
    assert missing_data(seconds, CFG) == []


def test_what_is_missing_is_said_exactly() -> None:
    seconds = {WATCHING: 60.0, NOTES: 8.0, PHONE: 0.0}
    assert missing_data(seconds, CFG) == ["needs 22 s more of taking notes", "needs 30 s more of phone in lap"]
    seconds = {WATCHING: 12.3, NOTES: 30.0, PHONE: 30.0}
    assert missing_data(seconds, CFG) == ["needs 48 s more of watching the teacher"]  # 47.7 rounds up


def test_the_minimum_data_amounts_come_from_config() -> None:
    assert (CFG.min_watching_s, CFG.min_notes_s, CFG.min_phone_s) == (60.0, 30.0, 30.0)
    seconds = {WATCHING: 60.0, NOTES: 30.0, PHONE: 30.0}
    assert missing_data(seconds, replace(CFG, min_phone_s=45.0)) == ["needs 15 s more of phone in lap"]
