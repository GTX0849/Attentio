"""The adaptive front region, tested at the attention seam: timed readings in, records and states out.

A student counts as Attentive while facing the front of the room in any natural way. The
reference direction and the personal extents are learned from Attentive readings only; the
downward rules are fixed. Readings come at 10 a second, as in the worker.
"""

import math
from collections.abc import Callable
from dataclasses import replace

import pytest

from shared.config import load_config
from shared.schema import AttentionRecord, AttentionState
from worker.front_region import InMemoryFrontRegionProfile, LearningConfig
from worker.readings import Reading
from worker.states import StateThresholds
from worker.tracker import AttentionTracker

CONFIG = load_config().raw
THRESHOLDS = StateThresholds.from_config(CONFIG)
HZ = 10

Angle = float | Callable[[float], float]  # constant, or a function of seconds since the start of the stretch


class Session:
    """Feeds one tracker a stream of readings and keeps the records it produces."""

    def __init__(self, thresholds: StateThresholds = THRESHOLDS) -> None:
        self.tracker = AttentionTracker("CAM1", "STU001", 30, thresholds)
        self.t = 0.0
        self.records: list[AttentionRecord] = []

    def feed(
        self,
        seconds: float,
        pitch: Angle = 0.0,
        yaw: Angle = 0.0,
        **fields: bool,
    ) -> None:
        start = self.t
        for i in range(round(seconds * HZ)):
            rel = i / HZ
            reading = Reading(
                timestamp=start + rel,
                face_present=fields.get("face_present", True),
                shoulders_present=fields.get("shoulders_present", True),
                pitch_deg=pitch(rel) if callable(pitch) else pitch,
                yaw_deg=yaw(rel) if callable(yaw) else yaw,
                eyes_closed=fields.get("eyes_closed", False),
                calibrating=fields.get("calibrating", False),
            )
            self.records += self.tracker.observe(reading)
        self.t = start + seconds

    def finish(self) -> dict[str, float]:
        self.records += self.tracker.flush(self.t)
        return {
            "attentive": sum(r.attentive for r in self.records),
            "neutral": sum(r.neutral for r in self.records),
            "distracted": sum(r.distracted for r in self.records),
            "not_visible": sum(r.not_visible for r in self.records),
        }


def swing(amplitude: float, period_s: float, centre: float = 0.0) -> Callable[[float], float]:
    return lambda t: centre + amplitude * math.sin(2 * math.pi * t / period_s)


def bursts(value: float, every_s: float = 20.0, for_s: float = 3.0) -> Callable[[float], float]:
    """Mostly 0, with a burst at `value` for `for_s` of every `every_s` seconds (15 % of the time by default)."""
    return lambda t: value if t % every_s < for_s else 0.0


DEFAULT_EXTENTS = (-THRESHOLDS.pitch_attentive_min_deg, THRESHOLDS.yaw_max_deg)  # (30, 35)


# --- a moving reference direction -------------------------------------------------


def test_a_slow_posture_shift_stays_attentive_and_the_reference_follows_it() -> None:
    s = Session()
    s.feed(180, pitch=lambda t: -12 * t / 180)  # slouching back from 0 to -12 degrees over 3 minutes
    totals = s.finish()
    assert totals == {"attentive": 180, "neutral": 0, "distracted": 0, "not_visible": 0}
    ref_pitch, ref_yaw = s.tracker.reference()
    assert ref_pitch == pytest.approx(-8, abs=0.5)  # the median of the last two minutes (-4 to -12)
    assert ref_yaw == 0


def test_turning_toward_a_teacher_walking_across_the_room_stays_attentive() -> None:
    s = Session()
    s.feed(30)
    s.feed(60, yaw=lambda t: 30 * t / 60)  # yaw sweeps to 30 degrees
    s.feed(60, yaw=30)  # and stays there while the teacher is on that side
    assert s.finish() == {"attentive": 150, "neutral": 0, "distracted": 0, "not_visible": 0}
    assert s.tracker.reference()[1] > 0  # the reference moved toward the teacher


def test_looking_up_at_a_standing_teacher_is_attentive_but_further_up_is_distracted_before_learning() -> None:
    up = Session()
    up.feed(20, pitch=-25)
    assert up.finish() == {"attentive": 20, "neutral": 0, "distracted": 0, "not_visible": 0}
    too_far = Session()
    too_far.feed(20, pitch=-35)
    assert too_far.finish() == {"attentive": 0, "neutral": 0, "distracted": 20, "not_visible": 0}


def test_the_reference_lets_a_student_look_further_to_the_side_than_the_camera_limit() -> None:
    s = Session()
    s.feed(120, yaw=swing(12, 7, centre=20))  # sits turned 20 degrees and moves about 12 either way
    assert s.tracker.reference()[1] == pytest.approx(20, abs=0.5)
    s.feed(10, yaw=45)  # 45 degrees is beyond the camera's 35, but only 25 from this student's reference
    totals = s.finish()
    assert totals["distracted"] == 0 and totals["attentive"] == 130


def test_a_sustained_look_at_a_phone_is_neutral_for_the_allowance_then_distracted_and_teaches_nothing() -> None:
    s = Session()
    s.feed(60, pitch=3)
    assert s.tracker.reference()[0] == pytest.approx(3)
    s.feed(120, pitch=25)  # 22 degrees below the reference: the neutral band, 8 s allowance
    assert s.tracker.reference() == (pytest.approx(3), 0)  # the reference does not move downward
    totals = s.finish()
    assert totals["attentive"] == 60
    assert totals["neutral"] == pytest.approx(8)
    assert totals["distracted"] == pytest.approx(112)


def test_a_phone_held_far_below_is_distracted_at_once_and_teaches_nothing() -> None:
    s = Session()
    s.feed(60)
    s.feed(120, pitch=45)  # beyond +38 from the reference
    assert s.tracker.reference() == (0, 0)
    totals = s.finish()
    assert totals == {"attentive": 60, "neutral": 0, "distracted": 120, "not_visible": 0}


def test_the_reference_stops_at_its_caps_under_a_sustained_extreme_direction() -> None:
    s = Session()
    s.feed(300, pitch=-28, yaw=33)  # attentive by the camera's limits, but far from calibration
    assert s.tracker.reference() == (-15, 20)  # the caps
    totals = s.finish()
    assert totals["attentive"] == 300


def test_the_downward_limits_are_never_learned() -> None:
    s = Session()
    s.feed(120, pitch=swing(5, 6, centre=10))  # sits looking low, reference about +10
    s.feed(3, pitch=30)  # 20 degrees below the reference: neutral, uses the allowance as always
    s.feed(10, pitch=60)  # more than 38 below the reference: distracted
    assert s.tracker.current_state(s.t) is AttentionState.DISTRACTED
    assert s.tracker.look_down_remaining_s(s.t) == pytest.approx(8 - 3, abs=0.2)


def test_readings_without_a_face_or_with_eyes_closed_teach_nothing() -> None:
    s = Session()
    s.feed(60, pitch=-20, yaw=15, face_present=False)  # the angles mean nothing without a face
    s.feed(60, pitch=-20, yaw=15, eyes_closed=True)  # distracted after the 1.5 s grace
    assert s.tracker.reference() == (0, 0)
    assert s.tracker.extents() == DEFAULT_EXTENTS


# --- personal spread ----------------------------------------------------------------


def test_before_enough_attentive_data_the_camera_limits_are_used() -> None:
    s = Session()
    assert s.tracker.reference() == (0, 0)
    assert s.tracker.extents() == DEFAULT_EXTENTS
    s.feed(25, pitch=swing(12, 5), yaw=swing(8, 6))  # fidgety, but under the 30 s needed
    assert s.tracker.extents() == DEFAULT_EXTENTS
    assert s.tracker.reference() == (0, 0)
    s.feed(10, pitch=swing(12, 5), yaw=swing(8, 6))
    assert s.tracker.extents() != DEFAULT_EXTENTS


def test_a_fidgety_student_gets_wider_extents_within_the_maximums() -> None:
    s = Session()
    s.feed(90, pitch=swing(14, 6), yaw=swing(22, 7))
    up, side = s.tracker.extents()
    assert DEFAULT_EXTENTS[0] < up <= 40
    assert side == 45  # the maximum sideways extent
    # A 40 degree turn is outside the camera's 35 but inside this student's habits.
    s.feed(5, yaw=40)
    assert s.tracker.current_state(s.t) is AttentionState.ATTENTIVE


# --- the camera's limits anchor the learned range ----------------------------------------

TUNED = replace(THRESHOLDS, pitch_attentive_min_deg=-25, yaw_max_deg=45)  # a camera tuned to up 25, side 45


def test_with_the_default_limits_the_learned_extents_never_pass_40_up_and_45_sideways() -> None:
    s = Session()
    s.feed(120, pitch=bursts(-22), yaw=swing(34, 7))  # looks up a lot and turns a lot, inside the 30 / 35 limits
    assert s.tracker.extents() == (40, 45)  # the limit plus the 10 degree headroom (30 + 10, 35 + 10)


def test_a_tuned_camera_limit_bounds_the_learned_extents_for_the_whole_lecture() -> None:
    s = Session(TUNED)
    assert s.tracker.extents() == (25, 45)  # until enough is learned the extents are the camera's limits
    s.feed(300, pitch=bursts(-22), yaw=swing(25, 7))  # a fidgety student, inside the limits
    assert s.tracker.extents() == (35, 55)  # 25 + 10 and 45 + 10: widened by the headroom, and no further


def test_a_tuned_camera_still_tightens_a_still_student_to_the_minimums() -> None:
    s = Session(TUNED)
    s.feed(90, pitch=swing(1, 5), yaw=swing(1, 6))
    assert s.tracker.extents() == (15, 15)


def test_a_student_is_never_held_below_a_tuned_limit_by_the_old_fixed_caps() -> None:
    wide = replace(THRESHOLDS, pitch_attentive_min_deg=-45, yaw_max_deg=50)  # the tuning maximums
    s = Session(wide)
    s.feed(300, pitch=bursts(-30), yaw=swing(30, 7))
    up, side = s.tracker.extents()
    assert up > 40 and side > 45  # more than the default caps: 45 + 10 and 50 + 10 are the ceilings
    assert up <= 55 and side <= 60


# --- calibration readings teach nothing --------------------------------------------------


def test_calibration_readings_do_not_move_the_reference_or_the_spread() -> None:
    s = Session()
    s.feed(300, pitch=0, yaw=0, calibrating=True)  # what perception sends while calibrating: zero angles
    assert s.tracker.reference() == (0, 0)
    assert s.tracker.extents() == DEFAULT_EXTENTS  # nothing learned, however long it went on
    s.feed(60, pitch=swing(1, 5, centre=-10), yaw=swing(1, 6, centre=8))  # then the real student
    assert s.tracker.reference()[0] == pytest.approx(-10, abs=0.3)
    assert s.tracker.reference()[1] == pytest.approx(8, abs=0.3)
    assert s.tracker.extents() == (15, 15)  # the spread is theirs alone: calibration zeros did not widen it


def test_calibration_readings_still_count_as_attentive_seconds() -> None:
    s = Session()
    s.feed(10, calibrating=True)
    assert s.finish() == {"attentive": 10, "neutral": 0, "distracted": 0, "not_visible": 0}


def test_a_still_student_gets_tighter_extents_down_to_the_minimums() -> None:
    s = Session()
    s.feed(90, pitch=swing(1, 5), yaw=swing(1, 6))
    assert s.tracker.extents() == (15, 15)
    s.feed(5, pitch=-20)  # looking up 20 degrees is now beyond this student's habits
    assert s.tracker.current_state(s.t) is AttentionState.DISTRACTED
    s.feed(5, yaw=20)
    assert s.tracker.current_state(s.t) is AttentionState.DISTRACTED


def test_the_extents_are_measured_from_the_reference() -> None:
    s = Session()
    s.feed(120, pitch=swing(1, 5, centre=-10))  # sits leaning back 10 degrees, hardly moving
    assert s.tracker.reference()[0] == pytest.approx(-10, abs=0.3)
    s.feed(5, pitch=-22)  # 12 degrees above the reference: inside the 15 minimum
    assert s.tracker.current_state(s.t) is AttentionState.ATTENTIVE
    s.feed(5, pitch=-28)  # 18 above the reference: outside it, though inside the camera's 30
    assert s.tracker.current_state(s.t) is AttentionState.DISTRACTED


# --- recalibration -------------------------------------------------------------------


def test_resetting_the_learning_clears_the_reference_and_the_extents() -> None:
    s = Session()
    s.feed(90, pitch=swing(1, 5, centre=-10), yaw=swing(1, 6, centre=8))
    assert s.tracker.reference() != (0, 0)
    assert s.tracker.extents() != DEFAULT_EXTENTS
    s.tracker.reset_learning()
    assert s.tracker.reference() == (0, 0)
    assert s.tracker.extents() == DEFAULT_EXTENTS


# --- the profile behind its interface --------------------------------------------------


def test_the_in_memory_profile_clamps_extents_to_the_camera_limit_plus_headroom() -> None:
    config = LearningConfig.from_config(CONFIG["states"]["learning"])
    profile = InMemoryFrontRegionProfile(config, up_limit_deg=25, side_limit_deg=45)
    assert profile.extents() is None
    for i in range(600):  # 60 s of readings spread widely around a median of zero
        angle = 50.0 * math.sin(i)
        profile.learn(i / HZ, angle, angle)
    assert profile.extents() == (25 + config.extent_headroom_deg, 45 + config.extent_headroom_deg)
    profile.reset()
    assert profile.extents() is None
    assert profile.reference() == (0, 0)


def test_the_reference_forgets_what_is_older_than_the_window() -> None:
    s = Session()
    s.feed(150, pitch=swing(1, 5, centre=10))
    assert s.tracker.reference()[0] == pytest.approx(10, abs=0.3)
    s.feed(150, pitch=swing(1, 5, centre=2))  # sits differently (a step this size is learned; a jump beyond the extents is not)
    assert s.tracker.reference()[0] == pytest.approx(2, abs=0.3)
