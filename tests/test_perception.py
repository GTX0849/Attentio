"""Perception, tested without a camera or model files.

The MediaPipe calls sit behind a backend (worker/mediapipe_source.py); here a
fake backend feeds the source raw observations.
"""

import math

import numpy as np
import pytest

from typing import Any

from numpy.typing import NDArray

from worker.perception import (
    FaceObservation,
    PerceptionSettings,
    PerceptionSource,
    PoseObservation,
    RawObservation,
    eyes_closed_from_blendshapes,
    head_angles_from_matrix,
    shoulders_from_visibility,
)


def rotation_matrix(pitch_down_deg: float = 0.0, yaw_deg: float = 0.0) -> np.ndarray:
    """A 4x4 face-to-camera matrix (camera: x right, y up, z towards the viewer).

    The face's forward axis is +z in the face frame. Pitching the head down
    swings its forward axis to negative y; yaw swings it to positive x.
    """
    p, y = math.radians(pitch_down_deg), math.radians(yaw_deg)
    pitch = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    yaw = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    m = np.eye(4)
    m[:3, :3] = yaw @ pitch
    m[:3, 3] = [3.0, -2.0, -40.0]  # translation must not matter
    return m


def test_a_head_facing_the_camera_has_zero_pitch_and_yaw() -> None:
    assert head_angles_from_matrix(rotation_matrix()) == pytest.approx((0.0, 0.0))


def test_looking_down_is_positive_pitch() -> None:
    pitch, yaw = head_angles_from_matrix(rotation_matrix(pitch_down_deg=20))
    assert pitch == pytest.approx(20.0)
    assert yaw == pytest.approx(0.0, abs=1e-9)


def test_looking_up_is_negative_pitch() -> None:
    pitch, _ = head_angles_from_matrix(rotation_matrix(pitch_down_deg=-12))
    assert pitch == pytest.approx(-12.0)


def test_turning_towards_image_right_is_positive_yaw() -> None:
    pitch, yaw = head_angles_from_matrix(rotation_matrix(yaw_deg=30))
    assert yaw == pytest.approx(30.0)
    assert pitch == pytest.approx(0.0, abs=1e-9)


def test_a_scaled_matrix_gives_the_same_angles() -> None:
    m = rotation_matrix(pitch_down_deg=10, yaw_deg=-15)
    m[:3, :3] *= 7.5
    assert head_angles_from_matrix(m) == pytest.approx((head_angles_from_matrix(rotation_matrix(10, -15))))


def test_eyes_are_closed_only_when_both_blinks_reach_the_threshold() -> None:
    assert eyes_closed_from_blendshapes({"eyeBlinkLeft": 0.8, "eyeBlinkRight": 0.7}, 0.5)
    assert not eyes_closed_from_blendshapes({"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.2}, 0.5)  # a wink
    assert not eyes_closed_from_blendshapes({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}, 0.5)


def test_the_blink_threshold_is_inclusive_and_configurable() -> None:
    scores = {"eyeBlinkLeft": 0.6, "eyeBlinkRight": 0.6}
    assert eyes_closed_from_blendshapes(scores, 0.6)
    assert not eyes_closed_from_blendshapes(scores, 0.61)


def test_missing_blink_scores_mean_eyes_open() -> None:
    assert not eyes_closed_from_blendshapes({}, 0.5)
    assert not eyes_closed_from_blendshapes({"eyeBlinkLeft": 0.9}, 0.5)


def test_shoulders_are_present_when_both_are_visible_enough() -> None:
    assert shoulders_from_visibility((0.9, 0.8), 0.5)
    assert not shoulders_from_visibility((0.9, 0.3), 0.5)  # one shoulder out of frame
    assert not shoulders_from_visibility((0.2, 0.1), 0.5)
    assert shoulders_from_visibility((0.5, 0.5), 0.5)


def test_no_pose_means_no_shoulders() -> None:
    assert not shoulders_from_visibility(None, 0.5)


# -- the source: calibration, sign flags, absent face -----------------------------

SETTINGS = PerceptionSettings(
    calibration_seconds=3.0,
    recalibrate_key="c",
    face_model="models/face_landmarker.task",
    pose_model="models/pose_landmarker_lite.task",
    blink_threshold=0.5,
    shoulder_visibility_threshold=0.5,
    invert_pitch=False,
    invert_yaw=False,
)


def face_obs(
    cx: float = 0.5,
    cy: float = 0.5,
    size: float = 0.2,
    pitch: float = 0.0,
    yaw: float = 0.0,
    blink: float = 0.0,
) -> FaceObservation:
    """A made-up face whose landmark extent is a square of side `size` centred on (cx, cy)."""
    h = size / 2
    points = np.array([[cx - h, cy - h], [cx + h, cy + h], [cx, cy]])
    return FaceObservation(
        landmarks=points,
        head_matrix=rotation_matrix(pitch, yaw),
        blendshapes={"eyeBlinkLeft": blink, "eyeBlinkRight": blink},
    )


def pose_obs(
    nose: tuple[float, float] = (0.5, 0.5),
    shoulders: tuple[float, float] = (0.9, 0.9),
) -> PoseObservation:
    """A made-up body: nose at `nose`, shoulders 0.2 below it with the given visibilities."""
    lm: NDArray[np.float64] = np.zeros((33, 3))
    lm[:, 0], lm[:, 1] = nose
    lm[11] = (nose[0] - 0.15, nose[1] + 0.2, shoulders[0])
    lm[12] = (nose[0] + 0.15, nose[1] + 0.2, shoulders[1])
    return PoseObservation(landmarks=lm)


class FakeBackend:
    """Returns whatever observation was queued last; records how often it was asked."""

    def __init__(self) -> None:
        self.next = RawObservation(faces=(), poses=())
        self.calls = 0

    def face(
        self,
        pitch: float = 0.0,
        yaw: float = 0.0,
        blink: float = 0.0,
        shoulders: tuple[float, float] | None = (0.9, 0.9),
    ) -> None:
        """One person, centred: a face, plus a body behind it unless `shoulders` is None."""
        poses = () if shoulders is None else (pose_obs(shoulders=shoulders),)
        self.next = RawObservation(faces=(face_obs(pitch=pitch, yaw=yaw, blink=blink),), poses=poses)

    def scene(self, faces: list[FaceObservation], poses: list[PoseObservation] | None = None) -> None:
        self.next = RawObservation(faces=tuple(faces), poses=tuple(poses or ()))

    def detect(self, frame: Any, timestamp: float) -> RawObservation:
        self.calls += 1
        return self.next


FRAME = object()


def calibrated_source(backend: FakeBackend, settings: PerceptionSettings = SETTINGS) -> PerceptionSource:
    """A source whose calibration has finished on a head held at pitch 10, yaw -4 (times 0 to 3.2)."""
    source = PerceptionSource(backend, settings)
    backend.face(pitch=10, yaw=-4)
    for i in range(33):
        source.read(FRAME, 100.0 + i * 0.1)
    assert not source.calibrating
    return source


def test_a_missing_frame_gives_a_face_absent_reading_without_asking_the_backend() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    r = source.read(None, 5.0)
    assert (r.timestamp, r.face_present, r.shoulders_present) == (5.0, False, False)
    assert backend.calls == 0


def test_readings_during_calibration_are_relative_zero_and_it_ends_after_the_configured_time() -> None:
    backend = FakeBackend()
    backend.face(pitch=10, yaw=-4)
    source = PerceptionSource(backend, SETTINGS)
    r = source.read(FRAME, 100.0)
    assert source.calibrating
    assert (r.face_present, r.pitch_deg, r.yaw_deg) == (True, 0.0, 0.0)
    assert source.calibration_remaining(101.0) == pytest.approx(2.0)
    source.read(FRAME, 102.9)
    assert source.calibrating
    for i in range(5):  # enough samples
        source.read(FRAME, 102.95 + i * 0.01)
    source.read(FRAME, 103.0)
    assert not source.calibrating
    assert source.calibration_remaining(103.0) == 0.0


def test_after_calibration_angles_are_relative_to_the_mean_calibration_pose() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    # alternate 8 and 12 degrees of pitch: the mean (10) becomes zero. Frames 0.2 s apart,
    # so calibration ends on the 16th frame (an even number of samples).
    for i in range(16):
        backend.face(pitch=8 if i % 2 == 0 else 12, yaw=-4)
        source.read(FRAME, 200.0 + i * 0.2)
    assert not source.calibrating
    backend.face(pitch=30, yaw=11)
    r = source.read(FRAME, 204.0)
    assert r.pitch_deg == pytest.approx(20.0)
    assert r.yaw_deg == pytest.approx(15.0)


def test_calibration_waits_for_a_face_before_it_can_finish() -> None:
    backend = FakeBackend()  # no face at all
    source = PerceptionSource(backend, SETTINGS)
    for i in range(50):
        source.read(FRAME, 300.0 + i * 0.2)  # 10 seconds with nobody there
    assert source.calibrating
    backend.face(pitch=5)
    for i in range(10):
        source.read(FRAME, 310.0 + i * 0.1)
    assert not source.calibrating


def test_recalibrating_restarts_it_and_adopts_the_new_pose() -> None:
    backend = FakeBackend()
    source = calibrated_source(backend)
    source.recalibrate()
    backend.face(pitch=-6, yaw=20)
    r = source.read(FRAME, 110.0)
    assert source.calibrating
    assert (r.pitch_deg, r.yaw_deg) == (0.0, 0.0)
    for i in range(1, 35):
        source.read(FRAME, 110.0 + i * 0.1)
    assert not source.calibrating
    backend.face(pitch=4, yaw=20)
    r = source.read(FRAME, 120.0)
    assert r.pitch_deg == pytest.approx(10.0)
    assert r.yaw_deg == pytest.approx(0.0)


def test_eyes_closed_and_shoulders_come_through_from_the_raw_observation() -> None:
    backend = FakeBackend()
    source = calibrated_source(backend)
    backend.face(pitch=10, yaw=-4, blink=0.9, shoulders=(0.9, 0.9))
    r = source.read(FRAME, 110.0)
    assert r.eyes_closed and r.shoulders_present
    backend.face(pitch=10, yaw=-4, blink=0.1, shoulders=(0.9, 0.2))
    r = source.read(FRAME, 110.1)
    assert not r.eyes_closed and not r.shoulders_present


def test_no_face_keeps_the_shoulders_so_distracted_can_be_told_from_not_visible() -> None:
    backend = FakeBackend()
    source = calibrated_source(backend)
    backend.scene([], [pose_obs(shoulders=(0.9, 0.9))])  # face turned away, body still there
    r = source.read(FRAME, 112.0)  # long enough for the face to count as lost
    assert (r.face_present, r.shoulders_present) == (False, True)
    backend.scene([], [])
    r = source.read(FRAME, 112.1)
    assert (r.face_present, r.shoulders_present) == (False, False)


def test_the_invert_flags_flip_the_sign_of_the_relative_angles() -> None:
    backend = FakeBackend()
    flipped = PerceptionSettings(**{**SETTINGS.__dict__, "invert_pitch": True, "invert_yaw": True})
    source = calibrated_source(backend, flipped)
    backend.face(pitch=30, yaw=11)  # +20 pitch, +15 yaw from the calibration pose
    r = source.read(FRAME, 110.0)
    assert r.pitch_deg == pytest.approx(-20.0)
    assert r.yaw_deg == pytest.approx(-15.0)


def test_settings_are_read_from_the_perception_section_of_the_config() -> None:
    from shared.config import load_config

    settings = PerceptionSettings.from_config(load_config().raw)
    assert settings.calibration_seconds == 3.0
    assert settings.recalibrate_key == "c"
    assert settings.blink_threshold == 0.5
    assert settings.shoulder_visibility_threshold == 0.5
    assert settings.invert_pitch is False and settings.invert_yaw is False
    assert settings.face_model.endswith("face_landmarker.task")
    assert settings.calibration_min_size_ratio == 2.0
    assert settings.calibration_min_in_frame == 0.98
    assert settings.calibration_min_face_width == 0.12
    assert settings.calibration_max_wobble_deg == 2.0
    assert settings.steady_window_s == 1.0
