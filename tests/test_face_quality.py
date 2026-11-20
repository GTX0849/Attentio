"""The face quality readout and the calibration check, at the PerceptionSource seam.

A fake backend returns made-up landmark sets over time; no camera and no model files. The three
readout numbers (In frame, Size, Steady) come from the landmark points alone, and the calibration
countdown only advances while the person is easy to read (see PerceptionSource).
"""

import numpy as np
import pytest
from numpy.typing import NDArray

from tests.test_perception import FRAME, SETTINGS, FakeBackend, rotation_matrix
from worker.perception import Diagnostics, FaceObservation, PerceptionSource

ANOTHER_FACE = "Another face is about as close as you"
CENTRE = "Centre your face"
CLOSER = "Move closer"
STILL = "Hold still"


def landmark_face(
    x0: float, y0: float, x1: float, y1: float, pitch: float = 0.0, yaw: float = 0.0
) -> FaceObservation:
    """A made-up face: a 4 x 4 grid of landmark points spanning the box (points may lie off the picture)."""
    xs, ys = np.meshgrid(np.linspace(x0, x1, 4), np.linspace(y0, y1, 4))
    points: NDArray[np.float64] = np.column_stack([xs.ravel(), ys.ravel()])
    return FaceObservation(landmarks=points, head_matrix=rotation_matrix(pitch, yaw), blendshapes={})


def square(cx: float, cy: float, width: float, pitch: float = 0.0, yaw: float = 0.0) -> FaceObservation:
    """A face whose box is `width` wide and as tall (in normalised picture units)."""
    h = width / 2
    return landmark_face(cx - width / 2, cy - h, cx + width / 2, cy + h, pitch, yaw)


def feed(
    source: PerceptionSource,
    backend: FakeBackend,
    start: float,
    seconds: float,
    faces: list[FaceObservation],
    wobble: float = 0.0,
    step: float = 0.1,
) -> Diagnostics:
    """Feed the same faces for `seconds`; with `wobble` the first face's pitch alternates by +/- wobble."""
    diag = Diagnostics()
    for i in range(round(seconds / step)):
        shaken = [faces[0]]
        if wobble:
            f = faces[0]
            shaken = [
                FaceObservation(
                    landmarks=f.landmarks,
                    head_matrix=rotation_matrix(10 + (wobble if i % 2 == 0 else -wobble), 0.0),
                    blendshapes={},
                )
            ]
        backend.scene(shaken + faces[1:])
        _, diag = source.read_with_diagnostics(FRAME, start + i * step)
    return diag


GOOD = square(0.5, 0.5, 0.25, pitch=10)  # a clear, still, well-framed face


# -- the readout -------------------------------------------------------------------


def test_the_readout_of_a_well_framed_face() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    diag = feed(source, backend, 100.0, 2.0, [GOOD])
    assert diag.framing is not None
    assert diag.framing.in_frame_pct == pytest.approx(100.0)
    assert diag.framing.size_pct == pytest.approx(25.0)
    assert diag.framing.steady_deg == pytest.approx(0.0, abs=1e-6)


def test_a_face_cut_off_at_the_edge_is_less_than_fully_in_frame() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    # x runs 0.82, 0.92, 1.02, 1.12: two of the four columns of points are off the picture
    diag = feed(source, backend, 100.0, 0.5, [landmark_face(0.82, 0.3, 1.12, 0.6)])
    assert diag.framing is not None
    assert diag.framing.in_frame_pct == pytest.approx(50.0)
    # and a face cut off at the top
    diag = feed(PerceptionSource(backend, SETTINGS), backend, 100.0, 0.5, [landmark_face(0.4, -0.1, 0.6, 0.3)])
    assert diag.framing is not None
    assert diag.framing.in_frame_pct == pytest.approx(75.0)


def test_the_size_is_the_face_width_as_a_share_of_the_picture_width() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    diag = feed(source, backend, 100.0, 0.5, [square(0.3, 0.5, 0.08)])
    assert diag.framing is not None
    assert diag.framing.size_pct == pytest.approx(8.0)


def test_a_jittering_head_reads_as_unsteady_and_a_still_one_as_steady() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    diag = feed(source, backend, 100.0, 2.0, [GOOD], wobble=1.0)  # pitch alternates 9, 11, 9, 11 ...
    assert diag.framing is not None
    assert diag.framing.steady_deg == pytest.approx(1.0, abs=0.05)
    diag = feed(source, backend, 102.0, 2.0, [GOOD], wobble=3.0)
    assert diag.framing is not None
    assert diag.framing.steady_deg == pytest.approx(3.0, abs=0.1)
    diag = feed(source, backend, 104.0, 2.0, [GOOD])  # held still: the wobble leaves the 1 s window
    assert diag.framing is not None
    assert diag.framing.steady_deg == pytest.approx(0.0, abs=1e-6)


def test_yaw_jitter_counts_too_and_the_larger_of_pitch_and_yaw_is_reported() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    diag = Diagnostics()
    for i in range(20):
        backend.scene([square(0.5, 0.5, 0.25, pitch=10 + (0.5 if i % 2 else -0.5), yaw=-4 + (2.0 if i % 2 else -2.0))])
        _, diag = source.read_with_diagnostics(FRAME, 100.0 + i * 0.1)
    assert diag.framing is not None
    assert diag.framing.steady_deg == pytest.approx(2.0, abs=0.1)  # yaw swings 2 degrees, pitch only 0.5


def test_with_no_locked_face_there_is_no_framing_line() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    backend.scene([])
    _, diag = source.read_with_diagnostics(FRAME, 100.0)
    assert diag.framing is None
    feed(source, backend, 101.0, 1.0, [GOOD])
    backend.scene([])  # the scored face is gone
    _, diag = source.read_with_diagnostics(FRAME, 102.5)
    assert diag.framing is None


def test_the_readout_describes_the_scored_face_not_the_others() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    diag = feed(source, backend, 100.0, 0.5, [GOOD, landmark_face(0.82, 0.3, 1.12, 0.35, pitch=-30)])
    assert diag.framing is not None
    assert diag.framing.in_frame_pct == pytest.approx(100.0)
    assert diag.framing.size_pct == pytest.approx(25.0)


# -- the calibration check ---------------------------------------------------------


def test_calibration_completes_when_everything_holds() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    diag = feed(source, backend, 100.0, 2.9, [GOOD, square(0.2, 0.5, 0.1)])
    assert source.calibrating
    assert diag.calibration_reason is None
    feed(source, backend, 102.9, 0.4, [GOOD, square(0.2, 0.5, 0.1)])
    assert not source.calibrating


def test_a_lone_face_needs_no_size_ratio() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    feed(source, backend, 100.0, 3.5, [GOOD])
    assert not source.calibrating


def pauses_then_resumes(
    bad: list[FaceObservation], good: list[FaceObservation], reason: str, wobble_bad: float = 0.0
) -> Diagnostics:
    """Calibrate against `bad` for 5 s (longer than the countdown), then against `good`."""
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    diag = feed(source, backend, 100.0, 5.0, bad, wobble=wobble_bad)
    assert source.calibrating  # more than 3 s passed, and still waiting
    assert diag.calibration_reason == reason
    assert diag.calibration_detail
    assert "Calibration paused" in diag.calibration_detail
    assert source.calibration_remaining(105.0) == pytest.approx(3.0)  # the countdown has not moved
    # the problem clears (a wobble needs a moment to leave the 1 s steadiness window)
    diag = feed(source, backend, 105.0, 2.8, good)
    assert source.calibrating  # a full 3 s of good frames are needed, not what was there before
    feed(source, backend, 107.8, 2.5, good)
    assert not source.calibrating
    return diag


def test_another_face_about_as_close_pauses_calibration_until_it_goes_away() -> None:
    pauses_then_resumes(
        bad=[GOOD, square(0.2, 0.5, 0.19)],  # 0.25 vs 0.19 wide: under twice the area, and too small to take the lock
        good=[GOOD, square(0.2, 0.5, 0.1)],
        reason=ANOTHER_FACE,
    )


def test_a_face_cut_off_at_the_edge_pauses_calibration_with_centre_your_face() -> None:
    pauses_then_resumes(
        bad=[landmark_face(0.75, 0.3, 1.05, 0.6, pitch=10)],
        good=[landmark_face(0.70, 0.3, 0.98, 0.6, pitch=10)],  # the same person, moved in
        reason=CENTRE,
    )


def test_a_small_distant_face_pauses_calibration_with_move_closer() -> None:
    pauses_then_resumes(bad=[square(0.5, 0.5, 0.1, pitch=10)], good=[GOOD], reason=CLOSER)


def test_a_wobbling_head_pauses_calibration_with_hold_still() -> None:
    diag = pauses_then_resumes(bad=[GOOD], good=[GOOD], reason=STILL, wobble_bad=3.0)
    assert diag.calibration_reason is None


def test_the_details_say_what_is_wrong_and_what_is_needed() -> None:
    def detail_for(faces: list[FaceObservation], wobble: float = 0.0) -> str:
        backend = FakeBackend()
        source = PerceptionSource(backend, SETTINGS)
        diag = feed(source, backend, 100.0, 1.5, faces, wobble=wobble)
        assert diag.calibration_detail is not None
        return diag.calibration_detail

    assert "+/-3.0 deg" in detail_for([GOOD], wobble=3.0)
    assert "under +/-2.0 deg for 3 s" in detail_for([GOOD], wobble=3.0)
    assert "12%" in detail_for([square(0.5, 0.5, 0.08)])  # the size needed
    assert "8%" in detail_for([square(0.5, 0.5, 0.08)])  # the size it is
    assert "98%" in detail_for([landmark_face(0.75, 0.3, 1.05, 0.6)])  # needed
    assert "75%" in detail_for([landmark_face(0.75, 0.3, 1.05, 0.6)])  # actual
    assert "2.0x" in detail_for([GOOD, square(0.2, 0.5, 0.19)])  # needed


def test_a_pause_throws_away_the_samples_so_the_zero_comes_from_the_good_frames_only() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    feed(source, backend, 100.0, 2.0, [square(0.5, 0.5, 0.1, pitch=40)])  # too small: pitch 40 is not the pose
    feed(source, backend, 102.0, 5.0, [GOOD])  # pitch 10 (the jump from 40 shows as wobble for a second first)
    assert not source.calibrating
    backend.scene([square(0.5, 0.5, 0.25, pitch=25)])
    assert source.read(FRAME, 108.0).pitch_deg == pytest.approx(15.0)  # 25 - 10, not 25 - something near 40


def test_the_reasons_are_given_in_priority_order() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    other = landmark_face(0.10, 0.46, 0.16, 0.54)  # not much smaller than the scored face
    t = 100.0
    # small, cut off at the edge, wobbling, with another face nearly as large: the other face comes first
    cut_off_small = landmark_face(0.96, 0.46, 1.04, 0.54, pitch=10)
    diag = feed(source, backend, t, 1.5, [cut_off_small, other], wobble=3.0)
    assert diag.calibration_reason == ANOTHER_FACE
    t += 1.5
    diag = feed(source, backend, t, 1.5, [cut_off_small], wobble=3.0)  # the other face leaves
    assert diag.calibration_reason == CENTRE
    t += 1.5
    diag = feed(source, backend, t, 1.5, [landmark_face(0.91, 0.46, 0.99, 0.54, pitch=10)], wobble=3.0)  # moves in
    assert diag.calibration_reason == CLOSER
    t += 1.5
    diag = feed(source, backend, t, 1.5, [landmark_face(0.85, 0.43, 0.99, 0.57, pitch=10)], wobble=3.0)  # leans in
    assert diag.calibration_reason == STILL
    t += 1.5
    diag = feed(source, backend, t, 1.5, [landmark_face(0.85, 0.43, 0.99, 0.57, pitch=10)])  # and holds still
    assert diag.calibration_reason is None


def test_the_check_never_runs_after_calibration_has_ended() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    other = square(0.2, 0.5, 0.075)  # someone else, nearly as close as the student will be
    feed(source, backend, 100.0, 3.5, [GOOD, square(0.2, 0.5, 0.1)])
    assert not source.calibrating
    # in the lecture the student drifts to the edge of the picture, small and cut off, and fidgets
    start, end = np.array([0.375, 0.375, 0.625, 0.625]), np.array([0.93, 0.46, 1.01, 0.54])
    diag = Diagnostics()
    for i in range(100):
        box = start + (end - start) * min(1.0, i / 20)
        pitch = 14 + (4 if i % 2 else -4)
        shaky = landmark_face(box[0], box[1], box[2], box[3], pitch=pitch)
        backend.scene([shaky, other])
        r, diag = source.read_with_diagnostics(FRAME, 104.0 + i * 0.1)
        assert r.face_present  # no gaps
        assert r.pitch_deg == pytest.approx(pitch - 10)
        assert diag.calibration_reason is None
        assert diag.calibration_detail is None
        assert diag.framing is not None
    assert diag.framing is not None
    assert diag.framing.size_pct == pytest.approx(8.0)
    assert diag.framing.in_frame_pct == pytest.approx(75.0)
    assert diag.framing.steady_deg == pytest.approx(4.0, abs=0.2)
    assert not source.calibrating


def test_a_pause_does_not_apply_while_nobody_is_in_view() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    backend.scene([])
    _, diag = source.read_with_diagnostics(FRAME, 100.0)
    assert diag.calibration_reason is None
    assert diag.calibration_detail is None
