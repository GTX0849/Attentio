"""State rules, tested through the public seam: timed readings in, window seconds out.

No camera, no database. Times start at 0, which is a window boundary.
"""

import random
from dataclasses import replace

import pytest

from shared.config import load_config
from shared.schema import AttentionRecord, AttentionState
from worker.readings import Reading
from worker.front_region import LearningConfig
from worker.states import StateThresholds
from worker.tracker import AttentionTracker

WINDOW = 30
THRESHOLDS = StateThresholds.from_config(load_config().raw)


def reading(t: float, **kw: object) -> Reading:
    """A reading of an attentive student with face and shoulders; override fields as needed."""
    base = Reading(timestamp=t, face_present=True, shoulders_present=True)
    return replace(base, **kw)  # type: ignore[arg-type]


def run(readings: list[Reading], end: float) -> list[AttentionRecord]:
    """Feed the readings, close the stream at `end`, return every window record."""
    tracker = AttentionTracker("CAM1", "STU001", WINDOW, THRESHOLDS)
    records: list[AttentionRecord] = []
    for r in readings:
        records += tracker.observe(r)
    records += tracker.flush(end)
    return records


def seconds(records: list[AttentionRecord]) -> dict[str, float]:
    """Seconds per state, summed over all windows."""
    return {
        "attentive": sum(r.attentive for r in records),
        "neutral": sum(r.neutral for r in records),
        "distracted": sum(r.distracted for r in records),
        "not_visible": sum(r.not_visible for r in records),
    }


def only(state: str, total: float) -> dict[str, float]:
    return {s.value: (total if s.value == state else 0.0) for s in AttentionState}


def test_the_thresholds_come_from_the_yaml_config() -> None:
    assert THRESHOLDS == StateThresholds(
        pitch_attentive_min_deg=-30,
        pitch_attentive_max_deg=15,
        pitch_neutral_max_deg=38,
        yaw_max_deg=35,
        eyes_closed_grace_s=1.5,
        look_down_allowance_s=8,
        allowance_refill_s=2,
        learning=LearningConfig(
            window_s=120,
            max_samples=2400,
            max_gap_s=2,
            update_interval_s=1,
            min_attentive_s=30,
            max_reference_pitch_deg=15,
            max_reference_yaw_deg=20,
            spread_percentile=90,
            spread_multiplier=2.5,
            min_up_extent_deg=15,
            min_side_extent_deg=15,
            extent_headroom_deg=10,
        ),
    )


# --- face and shoulders ---------------------------------------------------------


def test_face_and_shoulders_present_looking_straight_is_attentive() -> None:
    assert seconds(run([reading(0)], end=10)) == only("attentive", 10)


def test_face_lost_with_shoulders_present_is_distracted() -> None:
    records = run([reading(0), reading(4, face_present=False)], end=10)
    assert seconds(records) == {"attentive": 4, "neutral": 0, "distracted": 6, "not_visible": 0}


def test_face_and_shoulders_lost_is_not_visible() -> None:
    records = run(
        [reading(0), reading(4, face_present=False, shoulders_present=False)], end=10
    )
    assert seconds(records) == {"attentive": 4, "neutral": 0, "distracted": 0, "not_visible": 6}


# --- head direction bands -------------------------------------------------------


@pytest.mark.parametrize(
    ("pitch", "yaw", "expected"),
    [
        (-30, 0, "attentive"),  # lower pitch edge (looking up), inclusive
        (-30.5, 0, "distracted"),
        (-25, 0, "attentive"),  # a student looking up at a standing teacher
        (-35, 0, "distracted"),  # further up than the camera's limit, before any learning
        (15, 0, "attentive"),  # upper attentive edge, inclusive
        (15.5, 0, "neutral"),
        (38, 0, "neutral"),  # upper neutral edge, inclusive
        (38.5, 0, "distracted"),
        (0, 35, "attentive"),  # yaw just inside
        (0, -35, "attentive"),
        (0, 35.5, "distracted"),  # yaw just outside
        (0, -35.5, "distracted"),
        (0, 30, "attentive"),  # turning toward a teacher who walks across the room
        (30, 35, "neutral"),
        (30, 35.5, "distracted"),  # yaw beyond 35 is distracted whatever the pitch
        (90, 0, "distracted"),
        (-40, 0, "distracted"),
    ],
)
def test_head_direction_bands(pitch: float, yaw: float, expected: str) -> None:
    records = run([reading(0, pitch_deg=pitch, yaw_deg=yaw)], end=5)
    assert seconds(records) == only(expected, 5)


# --- eyes closed ----------------------------------------------------------------


def test_a_blink_under_the_grace_period_leaves_the_state_unchanged() -> None:
    records = run(
        [reading(0), reading(4, eyes_closed=True), reading(5.25), reading(6, eyes_closed=True), reading(7)],
        end=10,
    )
    assert seconds(records) == only("attentive", 10)


def test_eyes_closed_is_distracted_only_from_the_grace_period_onwards() -> None:
    # Closed from t=2 to t=6, sampled sparsely: attentive until 3.5, distracted from 3.5 until 6.
    records = run([reading(0), reading(2, eyes_closed=True), reading(6)], end=10)
    assert seconds(records) == {"attentive": 7.5, "neutral": 0, "distracted": 2.5, "not_visible": 0}


def test_eyes_closed_over_many_readings_turns_distracted_at_the_grace_period() -> None:
    closed = [reading(t, eyes_closed=True) for t in (2, 2.5, 3, 3.5, 4, 4.5)]
    records = run([reading(0), *closed, reading(5)], end=10)
    assert seconds(records) == {"attentive": 8.5, "neutral": 0, "distracted": 1.5, "not_visible": 0}


def test_the_closed_seconds_before_the_grace_period_follow_head_direction() -> None:
    # Looking down (neutral) with eyes closed from t=0 to t=4: neutral for 1.5 s, then distracted.
    records = run([reading(0, pitch_deg=20, eyes_closed=True), reading(4, pitch_deg=20)], end=4)
    assert seconds(records) == {"attentive": 0, "neutral": 1.5, "distracted": 2.5, "not_visible": 0}


def test_seconds_already_counted_are_not_rewritten_when_the_eyes_open() -> None:
    records = run([reading(0, eyes_closed=True), reading(1.25), reading(3, eyes_closed=True), reading(4)], end=4)
    assert seconds(records) == only("attentive", 4)


def test_face_lost_during_eye_closure_restarts_the_closure_count() -> None:
    records = run(
        [
            reading(0, eyes_closed=True),
            reading(1, face_present=False),  # closure so far is not carried across the lost face
            reading(2, eyes_closed=True),
            reading(3.25),
        ],
        end=5,
    )
    assert seconds(records) == {"attentive": 4, "neutral": 0, "distracted": 1, "not_visible": 0}


# --- looking down ---------------------------------------------------------------


def down(t: float) -> Reading:
    return reading(t, pitch_deg=25)


def test_looking_down_is_neutral_for_the_allowance_then_distracted() -> None:
    records = run([down(0)], end=12)
    assert seconds(records) == {"attentive": 0, "neutral": 8, "distracted": 4, "not_visible": 0}


def test_the_allowance_runs_out_at_the_right_moment_whatever_the_sampling() -> None:
    sparse = run([down(0), down(20)], end=20)
    dense = run([down(float(t)) for t in range(0, 20)], end=20)
    assert seconds(sparse) == seconds(dense) == {"attentive": 0, "neutral": 8, "distracted": 12, "not_visible": 0}


def test_distracted_after_the_allowance_lasts_until_the_person_leaves_the_band() -> None:
    records = run([down(0), reading(10), reading(14)], end=14)
    assert seconds(records) == {"attentive": 4, "neutral": 8, "distracted": 2, "not_visible": 0}


def test_a_glance_up_under_the_refill_time_does_not_refill_the_allowance() -> None:
    # 5 s down, 1.5 s attentive, then 5 s down: only 3 s of allowance were left.
    records = run([down(0), reading(5), down(6.5)], end=11.5)
    assert seconds(records) == {"attentive": 1.5, "neutral": 5 + 3, "distracted": 2, "not_visible": 0}


def test_a_glance_up_of_the_refill_time_refills_the_allowance() -> None:
    records = run([down(0), reading(5), down(7)], end=14)
    assert seconds(records) == {"attentive": 2, "neutral": 5 + 7, "distracted": 0, "not_visible": 0}


def test_a_full_refill_gives_a_whole_new_allowance() -> None:
    records = run([down(0), reading(5), down(7)], end=22)
    assert seconds(records) == {"attentive": 2, "neutral": 5 + 8, "distracted": 7, "not_visible": 0}


def test_the_allowance_stays_spent_after_a_short_glance_up() -> None:
    records = run([down(0), reading(10), down(11), reading(13)], end=13)
    # 8 neutral, 2 distracted, 1 attentive; back down: still spent so distracted at once.
    assert seconds(records) == {"attentive": 1, "neutral": 8, "distracted": 4, "not_visible": 0}


def test_face_lost_while_looking_down_keeps_the_remaining_allowance() -> None:
    # Face lost is not attentive, so it never refills, and it does not use up allowance.
    # 3 s down, 7 s face lost, then 7 s down: 5 s of allowance were left.
    records = run([down(0), reading(3, face_present=False), down(10)], end=17)
    assert seconds(records) == {"attentive": 0, "neutral": 8, "distracted": 9, "not_visible": 0}


def test_distracted_head_directions_do_not_use_or_refill_the_allowance() -> None:
    # Looking far down (distracted) between neutral looks neither consumes nor refills.
    # 3 s down, 10 s far down, then 7 s down: 5 s of allowance were left.
    records = run([down(0), reading(3, pitch_deg=60), down(13)], end=20)
    assert seconds(records) == {"attentive": 0, "neutral": 8, "distracted": 12, "not_visible": 0}


# --- windows --------------------------------------------------------------------


def test_a_sequence_crossing_a_window_boundary_splits_its_seconds() -> None:
    records = run([reading(20), reading(35, face_present=False, shoulders_present=False)], end=50)
    first, second = records
    assert first.window_start.timestamp() == 0 and second.window_start.timestamp() == 30
    assert (first.attentive, first.not_visible) == (10, 0)  # 20..30
    assert (second.attentive, second.not_visible) == (5, 15)  # 30..35 and 35..50


def test_a_state_change_inside_an_interval_can_fall_in_the_next_window() -> None:
    # Closed from t=25 (sampled once, held to t=40): distracted from 26.5, so 3.5 s in window 0.
    records = run([reading(25, eyes_closed=True), reading(40)], end=40)
    first, second = records
    assert (first.attentive, first.distracted) == (1.5, 3.5)
    assert (second.attentive, second.distracted) == (0, 10)


def test_a_window_s_state_seconds_add_up_to_its_length() -> None:
    rng = random.Random(1234)
    for _ in range(200):
        t = rng.uniform(0, 100)
        start = t
        readings = []
        for _ in range(rng.randint(1, 60)):
            readings.append(
                Reading(
                    timestamp=t,
                    face_present=rng.random() < 0.8,
                    shoulders_present=rng.random() < 0.5,
                    pitch_deg=rng.uniform(-30, 60),
                    yaw_deg=rng.uniform(-40, 40),
                    eyes_closed=rng.random() < 0.3,
                )
            )
            t += rng.choice([0.0, 0.1, 0.1, 0.5, 1.0, 3.0, 12.0])
        end = t + rng.uniform(0, 5)
        records = run(readings, end)
        assert records
        for rec in records:
            ws = rec.window_start.timestamp()
            expected = min(end, ws + WINDOW) - max(start, ws)  # the part of the window the stream covers
            total = rec.attentive + rec.neutral + rec.distracted + rec.not_visible
            assert total == pytest.approx(expected)


# --- the walking-skeleton reading source ------------------------------------------


class FakeDetector:
    def __init__(self, face: bool) -> None:
        self._face = face

    def has_face(self, frame: object) -> bool:
        return self._face


@pytest.mark.parametrize(("frame", "face"), [("frame", True), ("frame", False), (None, True)])
def test_the_haar_source_reports_only_a_face(frame: object, face: bool) -> None:
    from worker.detector import HaarReadingSource

    source = HaarReadingSource(FakeDetector(face))  # type: ignore[arg-type]
    got = source.read(frame, 12.5)
    assert got == Reading(
        timestamp=12.5, face_present=face and frame is not None, shoulders_present=False
    )
