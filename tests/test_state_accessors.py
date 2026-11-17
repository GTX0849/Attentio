"""The read-only view the preview uses: current state and remaining look-down allowance."""

import pytest

from shared.config import load_config
from shared.schema import AttentionState
from worker.readings import Reading
from worker.states import StateThresholds
from worker.tracker import AttentionTracker

THRESHOLDS = StateThresholds.from_config(load_config().raw)  # allowance 8 s, grace 1.5 s, refill 2 s


def tracker() -> AttentionTracker:
    return AttentionTracker("CAM1", "STU001", 30, THRESHOLDS)


def reading(t: float, **kw: object) -> Reading:
    return Reading(timestamp=t, face_present=True, shoulders_present=True, **kw)  # type: ignore[arg-type]


def test_before_any_reading_there_is_no_state_and_a_full_allowance() -> None:
    t = tracker()
    assert t.current_state(0.0) is None
    assert t.look_down_remaining_s(0.0) == 8.0


def test_the_current_state_is_the_held_readings_state() -> None:
    t = tracker()
    t.observe(reading(0.0))
    assert t.current_state(5.0) is AttentionState.ATTENTIVE
    assert t.look_down_remaining_s(5.0) == 8.0


def test_looking_down_uses_the_allowance_second_by_second_until_distracted() -> None:
    t = tracker()
    t.observe(reading(0.0, pitch_deg=25))
    assert t.current_state(3.0) is AttentionState.NEUTRAL
    assert t.look_down_remaining_s(3.0) == pytest.approx(5.0)
    assert t.current_state(8.5) is AttentionState.DISTRACTED
    assert t.look_down_remaining_s(8.5) == 0.0
    assert t.look_down_remaining_s(60.0) == 0.0


def test_the_remaining_allowance_carries_over_to_the_next_reading() -> None:
    t = tracker()
    t.observe(reading(0.0, pitch_deg=25))
    t.observe(reading(3.0, pitch_deg=25))
    assert t.look_down_remaining_s(4.0) == pytest.approx(4.0)


def test_an_absent_face_does_not_use_the_allowance() -> None:
    t = tracker()
    t.observe(reading(0.0, pitch_deg=25))
    t.observe(Reading(timestamp=3.0, face_present=False, shoulders_present=False))
    assert t.current_state(10.0) is AttentionState.NOT_VISIBLE
    assert t.look_down_remaining_s(10.0) == pytest.approx(5.0)


def test_the_allowance_shows_as_refilled_after_continuous_attentive() -> None:
    t = tracker()
    t.observe(reading(0.0, pitch_deg=25))
    t.observe(reading(3.0))  # attentive again, 5 s left
    assert t.look_down_remaining_s(4.0) == pytest.approx(5.0)
    assert t.look_down_remaining_s(5.0) == 8.0  # 2 s of continuous attentive


def test_eyes_closed_become_distracted_after_the_grace_period() -> None:
    t = tracker()
    t.observe(reading(0.0, eyes_closed=True))
    assert t.current_state(1.0) is AttentionState.ATTENTIVE
    assert t.current_state(1.5) is AttentionState.DISTRACTED
