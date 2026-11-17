"""Worker window aggregation, tested without a camera."""

from datetime import UTC, datetime

from shared.schema import AttentionState
from worker.windows import WindowAggregator

A = AttentionState.ATTENTIVE
N = AttentionState.NOT_VISIBLE

# 1_700_000_010 is an exact multiple of 30, i.e. a window boundary.
T0 = 1_700_000_010.0


def make() -> WindowAggregator:
    return WindowAggregator(camera_id="CAM1", student_id="STU001", window_seconds=30)


def test_observations_inside_one_window_emit_nothing_until_flushed() -> None:
    agg = make()
    assert agg.observe(T0 + 0, A) == []
    assert agg.observe(T0 + 1, A) == []
    records = agg.flush(T0 + 2)
    assert len(records) == 1
    rec = records[0]
    assert rec.window_start == datetime.fromtimestamp(T0, tz=UTC)
    assert rec.camera_id == "CAM1"
    assert rec.student_id == "STU001"
    assert (rec.attentive, rec.not_visible) == (2.0, 0.0)


def test_each_state_holds_until_the_next_observation() -> None:
    agg = make()
    agg.observe(T0 + 0, A)
    agg.observe(T0 + 10, N)  # attentive for 10 s
    [rec] = agg.flush(T0 + 25)  # not visible for 15 s
    assert (rec.attentive, rec.not_visible) == (10.0, 15.0)
    assert rec.neutral == 0.0 and rec.distracted == 0.0


def test_crossing_a_boundary_closes_the_window_and_splits_the_interval() -> None:
    agg = make()
    agg.observe(T0 + 20, A)
    finished = agg.observe(T0 + 35, N)  # attentive from +20 to +35 spans the boundary at +30
    assert len(finished) == 1
    assert finished[0].window_start == datetime.fromtimestamp(T0, tz=UTC)
    assert finished[0].attentive == 10.0  # +20..+30
    [partial] = agg.flush(T0 + 40)
    assert partial.window_start == datetime.fromtimestamp(T0 + 30, tz=UTC)
    assert partial.attentive == 5.0  # +30..+35
    assert partial.not_visible == 5.0  # +35..+40


def test_a_long_gap_emits_every_window_it_covers() -> None:
    agg = make()
    agg.observe(T0 + 0, A)
    finished = agg.observe(T0 + 95, N)  # attentive throughout +0..+95
    assert [r.window_start.timestamp() for r in finished] == [T0, T0 + 30, T0 + 60]
    assert [r.attentive for r in finished] == [30.0, 30.0, 30.0]


def test_flush_with_nothing_observed_returns_nothing_and_flush_twice_is_safe() -> None:
    agg = make()
    assert agg.flush(T0) == []
    agg.observe(T0, A)
    assert len(agg.flush(T0 + 5)) == 1
    assert agg.flush(T0 + 6) == []
