"""Turning raw MediaPipe output into Readings. Pure: no camera, no models.

MediaPipe itself is behind the `Backend` protocol (worker/mediapipe_source.py),
so everything here runs on plain numbers.
"""

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from worker.face_tracker import Box, FaceTracker
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


# MediaPipe pose landmark indices (33-point body model).
POSE_NOSE = 0
POSE_HEAD = range(0, 11)  # nose, eyes, ears, mouth: the landmarks that say which head a pose belongs to
POSE_LEFT_SHOULDER = 11
POSE_RIGHT_SHOULDER = 12


@dataclass(frozen=True)
class FaceObservation:
    """One face the model saw. Landmarks are normalised image coordinates (0 to 1, y down)."""

    landmarks: NDArray[np.float64]  # shape (n, 2): x, y per landmark (478 with irises)
    head_matrix: Any | None  # 4x4 facial transformation matrix; None = the model gave none
    blendshapes: dict[str, float]  # blendshape name -> score

    @property
    def box(self) -> Box:
        """The extent of the landmark points."""
        xs, ys = self.landmarks[:, 0], self.landmarks[:, 1]
        return Box(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))


@dataclass(frozen=True)
class PoseObservation:
    """One body the model saw."""

    landmarks: NDArray[np.float64]  # shape (33, 3): x, y (normalised, y down) and visibility per landmark

    def visibility(self, index: int) -> float:
        return float(self.landmarks[index, 2])

    def point(self, index: int) -> tuple[float, float]:
        return float(self.landmarks[index, 0]), float(self.landmarks[index, 1])


@dataclass(frozen=True)
class RawObservation:
    """What the models saw in one frame, before any thresholds, tracking or calibration.

    The order of faces and of poses carries no meaning, and the two lists are not linked:
    matching a pose to a face is the source's job.
    """

    faces: tuple[FaceObservation, ...]
    poses: tuple[PoseObservation, ...]


class Backend(Protocol):
    """The thin adapter around the MediaPipe landmarkers."""

    def detect(self, frame: Any, timestamp: float) -> RawObservation: ...


def _positive_int(value: Any, name: str) -> int:
    n = int(value)
    if n < 1:
        raise ValueError(f"{name} must be at least 1")
    return n


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
    max_faces: int = 5  # most faces and bodies the models look for in a frame
    max_poses: int = 5
    face_lost_after_s: float = 1.0  # the scored face missing longer than this counts as no face
    # The calibration check (only while calibrating): the countdown runs only while all of these hold.
    calibration_min_size_ratio: float = 2.0  # the scored face's area is at least this many times any other face's
    calibration_min_in_frame: float = 0.98  # share of the face's landmark points inside the picture
    calibration_min_face_width: float = 0.12  # face box width as a share of the picture width
    calibration_max_wobble_deg: float = 2.0  # steadiness (see steady_window_s) must be within +/- this
    steady_window_s: float = 1.0  # the "Steady" readout looks at the raw head angles over this long

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "PerceptionSettings":
        """Read the top-level `perception:` section of config/attentio.yaml."""
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
            max_faces=_positive_int(s["max_faces"], "perception.max_faces"),
            max_poses=_positive_int(s["max_poses"], "perception.max_poses"),
            face_lost_after_s=float(s["face_lost_after_s"]),
            calibration_min_size_ratio=float(s["calibration_min_size_ratio"]),
            calibration_min_in_frame=float(s["calibration_min_in_frame"]),
            calibration_min_face_width=float(s["calibration_min_face_width"]),
            calibration_max_wobble_deg=float(s["calibration_max_wobble_deg"]),
            steady_window_s=float(s["steady_window_s"]),
        )


@dataclass(frozen=True)
class FaceDiagnostic:
    """One face in view this frame, for the preview only. Never scored, stored or sent."""

    track_id: int
    box: Box
    landmarks: NDArray[np.float64]
    tracked: bool  # True for the scored person, False for everyone else ("ignored")
    pitch_deg: float | None  # raw head angles, relative to the camera, + = down / + = image right
    yaw_deg: float | None  # (what the head really does; not calibrated, not inverted). None without a matrix


@dataclass(frozen=True)
class FramingReadout:
    """How well the scored person is framed, from the landmark points alone (MediaPipe gives no confidence)."""

    in_frame_pct: float  # share of the face's landmark points inside the picture, 0 to 100
    size_pct: float  # the face box width as a share of the picture width, 0 to 100
    steady_deg: float  # spread of the raw head pitch and yaw over the last second (see `_steadiness`)


@dataclass(frozen=True)
class Diagnostics:
    """What the preview needs beyond the Reading. Lives for one frame; nothing here is stored."""

    faces: tuple[FaceDiagnostic, ...] = ()
    shoulders: tuple[tuple[float, float], tuple[float, float]] | None = None  # locked pose's (left, right) points
    framing: FramingReadout | None = None  # the "In frame / Size / Steady" line; None with no locked face in view
    calibration_reason: str | None = None  # why the calibration countdown is paused; None shows "Calibrating"
    calibration_detail: str | None = None  # the white line under it: what is wrong and what is needed

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def tracked_count(self) -> int:
        return sum(1 for f in self.faces if f.tracked)

    @property
    def tracked(self) -> FaceDiagnostic | None:
        return next((f for f in self.faces if f.tracked), None)


MIN_CALIBRATION_SAMPLES = 5  # face frames needed before a calibration may finish

# Why the calibration countdown can pause, most important first.
REASON_ANOTHER_FACE = "Another face is about as close as you"
REASON_OFF_CENTRE = "Centre your face"
REASON_TOO_FAR = "Move closer"
REASON_MOVING = "Hold still"


def _steadiness(samples: deque[tuple[float, float, float]]) -> float:
    """The larger of the standard deviations (population) of raw pitch and yaw, in degrees.

    One standard deviation rather than max minus min, so that a single noisy frame does not
    decide it. 0 with fewer than two samples.
    """
    if len(samples) < 2:
        return 0.0
    angles = np.array([(p, y) for _, p, y in samples])
    return float(angles.std(axis=0).max())


def _framing(face: FaceObservation, steady_deg: float) -> FramingReadout:
    xs, ys = face.landmarks[:, 0], face.landmarks[:, 1]
    inside = (xs >= 0.0) & (xs <= 1.0) & (ys >= 0.0) & (ys <= 1.0)
    box = face.box
    return FramingReadout(
        in_frame_pct=float(inside.mean()) * 100.0,
        size_pct=(box.x1 - box.x0) * 100.0,
        steady_deg=steady_deg,
    )
SWITCH_AREA_FACTOR = 1.5  # during calibration a face this much larger than the locked one takes the lock


@dataclass
class _Calibration:
    started_at: float | None = None  # set by the first frame after (re)starting
    pitches: list[float] = field(default_factory=list)
    yaws: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class _FaceReading:
    """The last angles read from the scored face, held through short dropouts."""

    pitch_deg: float  # relative to calibration
    yaw_deg: float
    eyes_closed: bool


class PerceptionSource:
    """A ReadingSource that turns a Backend's raw observations into Readings.

    Who is scored: the backend may see several faces. A `FaceTracker` follows each one
    frame to frame, and at calibration the source locks onto the largest face. Only that
    face is scored. If it is missing for more than `face_lost_after_s` the readings
    become face-absent, and the source never moves on to another face by itself: it locks
    again on `recalibrate()`, or when a face shows up where the locked one was last seen.
    The other faces are not scored, stored or sent; they only appear in the diagnostics.
    Shoulders come only from the body whose head sits inside the locked face's box (its
    last known box when the face is missing), so someone behind does not count.

    Calibration: for `calibration_seconds` from the first frame (and again after
    `recalibrate()`), the person is assumed to be looking at the teacher. Every
    frame with the locked face adds its raw pitch and yaw to the sample; when the time is
    up (and at least a few face frames were seen) the means become the zero. While
    calibrating, readings carry pitch 0 and yaw 0, so the person counts as looking
    at the teacher, and the state rules receive them as usual, marked `calibrating=True`
    so that the front-region learner does not learn from the invented zero angles (the
    reading that ends calibration already has the zero applied and is not marked). If nobody is in view,
    calibration simply keeps waiting for a face. If the face it locked onto is lost for
    longer than `face_lost_after_s` before calibration has finished, the lock and the
    samples so far are dropped and it locks onto the largest face again. The same happens
    if, during calibration, a face more than 1.5 times the area of the locked one appears
    (the person nearest the camera is usually the one who was not detected in the first frame).
    After calibration the lock is never moved by size.

    Quality: with a locked face in view every frame's diagnostics carry a `FramingReadout`
    (in frame, size, steady; all from the landmark points). During calibration only, the
    countdown also needs the scored face to be at least `calibration_min_size_ratio` times the
    area of every other face, in frame to `calibration_min_in_frame`, at least
    `calibration_min_face_width` of the picture wide and steady within
    `calibration_max_wobble_deg`. When one fails the countdown and the samples collected so far
    are thrown away (so the zero is made only from good frames), and the diagnostics say why
    (the first failing one, in that order). After calibration none of this is looked at.
    """

    def __init__(self, backend: Backend, settings: PerceptionSettings) -> None:
        self._backend = backend
        self._settings = settings
        self._calibration: _Calibration | None = _Calibration()
        self._offset = (0.0, 0.0)  # raw (pitch, yaw) that counts as zero
        self._faces = FaceTracker(settings.face_lost_after_s)
        self._held: _FaceReading | None = None  # last reading of the locked face
        self._diagnostics = Diagnostics()
        self._angle_history: deque[tuple[float, float, float]] = deque()  # (time, raw pitch, raw yaw), locked face
        self._history_id: int | None = None  # the track the history belongs to

    @property
    def calibrating(self) -> bool:
        return self._calibration is not None

    @property
    def diagnostics(self) -> Diagnostics:
        """The diagnostics of the most recent `read`."""
        return self._diagnostics

    def recalibrate(self) -> None:
        """Start calibrating again from the next frame, and lock onto the largest face then."""
        self._calibration = _Calibration()
        self._faces.unlock()
        self._held = None
        self._angle_history.clear()

    def calibration_remaining(self, timestamp: float) -> float:
        """Seconds of calibration left at `timestamp` (0 when not calibrating)."""
        c = self._calibration
        if c is None:
            return 0.0
        if c.started_at is None:
            return self._settings.calibration_seconds
        return max(0.0, self._settings.calibration_seconds - (timestamp - c.started_at))

    def read(self, frame: Any | None, timestamp: float) -> Reading:
        return self.read_with_diagnostics(frame, timestamp)[0]

    def read_with_diagnostics(self, frame: Any | None, timestamp: float) -> tuple[Reading, Diagnostics]:
        """The scored person's Reading, plus what the preview needs to draw."""
        if frame is None:
            self._diagnostics = Diagnostics()
            return (
                Reading(
                    timestamp=timestamp,
                    face_present=False,
                    shoulders_present=False,
                    calibrating=self._calibration is not None,
                ),
                self._diagnostics,
            )
        obs = self._backend.detect(frame, timestamp)
        st = self._settings

        ids = self._faces.update([f.box for f in obs.faces], timestamp)
        if self._calibration is not None:
            if self._faces.locked_lost(timestamp):  # the person we locked onto left: start over
                self._faces.unlock()
                self._calibration = _Calibration()
                self._held = None
            elif self._faces.has_clearly_larger_face(SWITCH_AREA_FACTOR):
                # the locked face was a far-away or partial one and the real person has now been found
                self._faces.unlock()
                self._calibration = _Calibration()
                self._held = None
            if self._faces.locked_id is None:
                self._faces.lock_largest()
        locked_index = self._faces.locked_index

        locked_face = obs.faces[locked_index] if locked_index is not None else None
        angles = self._angles(locked_face)
        shoulders, shoulder_points = self._shoulders(obs.poses)
        faces: list[FaceDiagnostic] = []
        for i, face in enumerate(obs.faces):
            raw = self._raw_angles(face)
            faces.append(
                FaceDiagnostic(
                    track_id=ids[i],
                    box=face.box,
                    landmarks=face.landmarks,
                    tracked=i == locked_index,
                    pitch_deg=raw[0] if raw else None,
                    yaw_deg=raw[1] if raw else None,
                )
            )
        framing = self._framing(locked_face, self._faces.locked_id, timestamp)
        problem = (
            self._calibration_problem(obs.faces, locked_index, framing)
            if self._calibration is not None and framing is not None and locked_index is not None
            else None
        )
        self._diagnostics = Diagnostics(
            faces=tuple(faces),
            shoulders=shoulder_points,
            framing=framing,
            calibration_reason=problem[0] if problem else None,
            calibration_detail=problem[1] if problem else None,
        )

        self._advance_calibration(timestamp, angles, paused=problem is not None)
        # The reading that ends calibration already has the zero applied, so it is not a calibration reading.
        calibrating = self._calibration is not None
        if angles is None:
            held = self._held
            if held is not None and not self._faces.locked_lost(timestamp):
                # a short dropout: keep the last reading of this person rather than flicker
                return (
                    Reading(
                        timestamp=timestamp,
                        face_present=True,
                        shoulders_present=shoulders,
                        pitch_deg=held.pitch_deg,
                        yaw_deg=held.yaw_deg,
                        eyes_closed=held.eyes_closed,
                        calibrating=calibrating,
                    ),
                    self._diagnostics,
                )
            return (
                Reading(
                    timestamp=timestamp, face_present=False, shoulders_present=shoulders, calibrating=calibrating
                ),
                self._diagnostics,
            )
        assert locked_face is not None
        if self._calibration is not None:
            rel_pitch = rel_yaw = 0.0
        else:
            rel_pitch, rel_yaw = angles[0] - self._offset[0], angles[1] - self._offset[1]
        eyes_closed = eyes_closed_from_blendshapes(locked_face.blendshapes, st.blink_threshold)
        self._held = _FaceReading(rel_pitch, rel_yaw, eyes_closed)
        return (
            Reading(
                timestamp=timestamp,
                face_present=True,
                shoulders_present=shoulders,
                pitch_deg=rel_pitch,
                yaw_deg=rel_yaw,
                eyes_closed=eyes_closed,
                calibrating=calibrating,
            ),
            self._diagnostics,
        )

    @staticmethod
    def _raw_angles(face: FaceObservation) -> tuple[float, float] | None:
        return None if face.head_matrix is None else head_angles_from_matrix(face.head_matrix)

    def _angles(self, face: FaceObservation | None) -> tuple[float, float] | None:
        """The locked face's (pitch, yaw) with the invert flags applied; None without a usable face."""
        raw = self._raw_angles(face) if face is not None else None
        if raw is None:
            return None
        pitch, yaw = raw
        return (-pitch if self._settings.invert_pitch else pitch, -yaw if self._settings.invert_yaw else yaw)

    def _shoulders(
        self, poses: tuple[PoseObservation, ...]
    ) -> tuple[bool, tuple[tuple[float, float], tuple[float, float]] | None]:
        """Whether both shoulders of the locked person are visible, and their points if so.

        The locked person's body is the pose whose head landmarks lie inside the locked face's
        box (last known box if the face is out of view); with several, the nose nearest the
        box centre. No locked face, no pose in the box: no shoulders.
        """
        box = self._faces.locked_box
        if box is None:
            return False, None
        cx, cy = box.centre
        best: PoseObservation | None = None
        best_key: tuple[int, float] | None = None
        for pose in poses:
            if not any(box.contains(*pose.point(i)) for i in POSE_HEAD):
                continue
            nx, ny = pose.point(POSE_NOSE)
            key = (0 if box.contains(nx, ny) else 1, (nx - cx) ** 2 + (ny - cy) ** 2)
            if best_key is None or key < best_key:
                best, best_key = pose, key
        if best is None:
            return False, None
        left = best.visibility(POSE_LEFT_SHOULDER)
        right = best.visibility(POSE_RIGHT_SHOULDER)
        present = shoulders_from_visibility((left, right), self._settings.shoulder_visibility_threshold)
        points = (best.point(POSE_LEFT_SHOULDER), best.point(POSE_RIGHT_SHOULDER)) if present else None
        return present, points

    def _framing(self, face: FaceObservation | None, track_id: int | None, timestamp: float) -> FramingReadout | None:
        """The locked face's readout (None when it is not in view), updating the steadiness history."""
        if face is None:
            return None
        if self._history_id != track_id:  # a different person: their wobble is not this one's
            self._angle_history.clear()
            self._history_id = track_id
        raw = self._raw_angles(face)
        if raw is not None:
            self._angle_history.append((timestamp, raw[0], raw[1]))
        horizon = self._settings.steady_window_s + 1e-9
        while self._angle_history and timestamp - self._angle_history[0][0] > horizon:
            self._angle_history.popleft()
        return _framing(face, _steadiness(self._angle_history))

    def _calibration_problem(
        self, faces: tuple[FaceObservation, ...], locked_index: int, framing: FramingReadout
    ) -> tuple[str, str] | None:
        """(reason, detail) for the first failing calibration condition, or None when all hold."""
        st = self._settings
        area = faces[locked_index].box.area
        biggest_other = max((f.box.area for i, f in enumerate(faces) if i != locked_index), default=0.0)
        if biggest_other > 0 and area < st.calibration_min_size_ratio * biggest_other:
            return (
                REASON_ANOTHER_FACE,
                f"Calibration paused - your face is only {area / biggest_other:.1f}x the area of the nearest"
                f" other face, needs {st.calibration_min_size_ratio:.1f}x",
            )
        if framing.in_frame_pct < st.calibration_min_in_frame * 100.0 - 1e-9:
            return (
                REASON_OFF_CENTRE,
                f"Calibration paused - {framing.in_frame_pct:.0f}% of your face is in the picture,"
                f" needs {st.calibration_min_in_frame * 100:.0f}%",
            )
        if framing.size_pct < st.calibration_min_face_width * 100.0 - 1e-9:
            return (
                REASON_TOO_FAR,
                f"Calibration paused - face is {framing.size_pct:.0f}% of the picture width,"
                f" needs {st.calibration_min_face_width * 100:.0f}%",
            )
        if framing.steady_deg > st.calibration_max_wobble_deg + 1e-9:
            return (
                REASON_MOVING,
                f"Calibration paused - head moving +/-{framing.steady_deg:.1f} deg,"
                f" needs under +/-{st.calibration_max_wobble_deg:.1f} deg for {st.calibration_seconds:.0f} s",
            )
        return None

    def _advance_calibration(self, timestamp: float, angles: tuple[float, float] | None, paused: bool) -> None:
        c = self._calibration
        if c is None:
            return
        if paused:  # the check failed: the countdown and the samples start again once it clears
            c.started_at = None
            c.pitches.clear()
            c.yaws.clear()
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
