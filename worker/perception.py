"""Turning raw MediaPipe output into Readings. Pure: no camera, no models.

MediaPipe itself is behind the `Backend` protocol (worker/mediapipe_source.py),
so everything here runs on plain numbers.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from worker.readings import Reading


def head_angles_from_matrix(matrix: Any) -> tuple[float, float]:
    """(pitch, yaw) in degrees from a FaceLandmarker facial transformation matrix.

    The matrix maps the canonical face into camera space (x right, y up, z towards
    the viewer). The face's forward axis is +z of the face, which is the third
    column of the rotation part. Raw angles are relative to the camera, not to
    calibration. Positive pitch = looking down; positive yaw = turned towards the
    right of the (unmirrored) image. Translation and scale are ignored.
    """
    m = np.asarray(matrix, dtype=float)
    fx, fy, fz = m[0, 2], m[1, 2], m[2, 2]
    pitch = math.degrees(math.atan2(-fy, math.hypot(fx, fz)))
    yaw = math.degrees(math.atan2(fx, fz))
    return pitch, yaw


def eyes_closed_from_blendshapes(scores: dict[str, float], threshold: float) -> bool:
    """Both eyes' blink strength at or above `threshold`. A missing score counts as open."""
    return (
        scores.get("eyeBlinkLeft", 0.0) >= threshold and scores.get("eyeBlinkRight", 0.0) >= threshold
    )


def shoulders_from_visibility(visibility: tuple[float, float] | None, threshold: float) -> bool:
    """Both shoulders' pose visibility at or above `threshold`; None (no pose found) = absent."""
    return visibility is not None and min(visibility) >= threshold


@dataclass(frozen=True)
class RawObservation:
    """What the models saw in one frame, before any thresholds or calibration."""

    head_matrix: Any | None  # 4x4 facial transformation matrix; None = no face found
    blendshapes: dict[str, float]  # blendshape name -> score (empty without a face)
    shoulder_visibility: tuple[float, float] | None  # (left, right) pose visibility; None = no pose found


class Backend(Protocol):
    """The thin adapter around the MediaPipe landmarkers."""

    def detect(self, frame: Any, timestamp: float) -> RawObservation: ...


@dataclass(frozen=True)
class PerceptionSettings:
    calibration_seconds: float
    recalibrate_key: str
    face_model: str
    pose_model: str
    blink_threshold: float
    shoulder_visibility_threshold: float
    invert_pitch: bool
    invert_yaw: bool

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "PerceptionSettings":
        """Read the top-level `perception:` section of config/classpulse.yaml."""
        s = raw["perception"]
        key = str(s["recalibrate_key"])
        if len(key) != 1:
            raise ValueError("perception.recalibrate_key must be a single character")
        return cls(
            calibration_seconds=float(s["calibration_seconds"]),
            recalibrate_key=key,
            face_model=str(s["face_model"]),
            pose_model=str(s["pose_model"]),
            blink_threshold=float(s["blink_threshold"]),
            shoulder_visibility_threshold=float(s["shoulder_visibility_threshold"]),
            invert_pitch=bool(s["invert_pitch"]),
            invert_yaw=bool(s["invert_yaw"]),
        )


MIN_CALIBRATION_SAMPLES = 5  # face frames needed before a calibration may finish


@dataclass
class _Calibration:
    started_at: float | None = None  # set by the first frame after (re)starting
    pitches: list[float] = field(default_factory=list)
    yaws: list[float] = field(default_factory=list)


class PerceptionSource:
    """A ReadingSource that turns a Backend's raw observations into Readings.

    Calibration: for `calibration_seconds` from the first frame (and again after
    `recalibrate()`), the person is assumed to be looking at the screen. Every
    frame with a face adds its raw pitch and yaw to the sample; when the time is
    up (and at least a few face frames were seen) the means become the zero. While
    calibrating, readings carry pitch 0 and yaw 0, so the person counts as looking
    at the screen, and the state rules receive them as usual. If nobody is in view,
    calibration simply keeps waiting for a face.
    """

    def __init__(self, backend: Backend, settings: PerceptionSettings) -> None:
        self._backend = backend
        self._settings = settings
        self._calibration: _Calibration | None = _Calibration()
        self._offset = (0.0, 0.0)  # raw (pitch, yaw) that counts as zero

    @property
    def calibrating(self) -> bool:
        return self._calibration is not None

    def recalibrate(self) -> None:
        """Start calibrating again from the next frame."""
        self._calibration = _Calibration()

    def calibration_remaining(self, timestamp: float) -> float:
        """Seconds of calibration left at `timestamp` (0 when not calibrating)."""
        c = self._calibration
        if c is None:
            return 0.0
        if c.started_at is None:
            return self._settings.calibration_seconds
        return max(0.0, self._settings.calibration_seconds - (timestamp - c.started_at))

    def read(self, frame: Any | None, timestamp: float) -> Reading:
        if frame is None:
            return Reading(timestamp=timestamp, face_present=False, shoulders_present=False)
        obs = self._backend.detect(frame, timestamp)
        st = self._settings
        shoulders = shoulders_from_visibility(obs.shoulder_visibility, st.shoulder_visibility_threshold)
        if obs.head_matrix is None:
            self._advance_calibration(timestamp, None)
            return Reading(timestamp=timestamp, face_present=False, shoulders_present=shoulders)
        pitch, yaw = head_angles_from_matrix(obs.head_matrix)
        if st.invert_pitch:
            pitch = -pitch
        if st.invert_yaw:
            yaw = -yaw
        self._advance_calibration(timestamp, (pitch, yaw))
        if self._calibration is not None:
            rel_pitch = rel_yaw = 0.0
        else:
            rel_pitch, rel_yaw = pitch - self._offset[0], yaw - self._offset[1]
        return Reading(
            timestamp=timestamp,
            face_present=True,
            shoulders_present=shoulders,
            pitch_deg=rel_pitch,
            yaw_deg=rel_yaw,
            eyes_closed=eyes_closed_from_blendshapes(obs.blendshapes, st.blink_threshold),
        )

    def _advance_calibration(self, timestamp: float, angles: tuple[float, float] | None) -> None:
        c = self._calibration
        if c is None:
            return
        if c.started_at is None:
            c.started_at = timestamp
        if angles is not None:
            c.pitches.append(angles[0])
            c.yaws.append(angles[1])
        done = timestamp - c.started_at >= self._settings.calibration_seconds
        if done and len(c.pitches) >= MIN_CALIBRATION_SAMPLES:
            self._offset = (sum(c.pitches) / len(c.pitches), sum(c.yaws) / len(c.yaws))
            self._calibration = None
