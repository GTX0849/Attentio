"""The MediaPipe side: FaceLandmarker and PoseLandmarker in VIDEO mode.

Kept thin on purpose. It only runs the models and hands back a RawObservation;
all thresholds, calibration and angle maths live in worker/perception.py.
"""

from pathlib import Path
from typing import Any

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

from shared.config import PROJECT_ROOT
from worker.perception import (
    FaceObservation,
    PerceptionSettings,
    PerceptionSource,
    PoseObservation,
    RawObservation,
)


def resolve_model(path: str) -> Path:
    """A model path from the config: relative paths are relative to the project root."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _score(value: float | None) -> float:
    """A landmark's visibility; MediaPipe leaves it None when the model gives none."""
    return 0.0 if value is None else float(value)


class MediaPipeBackend:
    """Runs both landmarkers for up to `max_faces` faces and `max_poses` bodies per frame.

    Both numbers are maximums: the models return only who they find, in no particular order.
    """

    def __init__(self, face_model: Path, pose_model: Path, max_faces: int = 1, max_poses: int = 1) -> None:
        for model in (face_model, pose_model):
            if not model.exists():
                raise FileNotFoundError(
                    f"Model file not found: {model}. Fetch it with: python -m worker.models download"
                )
        mode = vision.RunningMode.VIDEO
        self._face = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(face_model)),
                running_mode=mode,
                num_faces=max_faces,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
        )
        self._pose = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(pose_model)),
                running_mode=mode,
                num_poses=max_poses,
            )
        )
        self._last_ms = -1

    def detect(self, frame: Any, timestamp: float) -> RawObservation:
        """`frame` is a BGR image straight from OpenCV (not mirrored)."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ms = max(int(timestamp * 1000), self._last_ms + 1)  # VIDEO mode needs strictly increasing times
        self._last_ms = ms

        face = self._face.detect_for_video(image, ms)
        pose = self._pose.detect_for_video(image, ms)

        faces: list[FaceObservation] = []
        for i, landmarks in enumerate(face.face_landmarks):
            if not landmarks:
                continue
            matrix = face.facial_transformation_matrixes[i] if i < len(face.facial_transformation_matrixes) else None
            blendshapes: dict[str, float] = {}
            if i < len(face.face_blendshapes):
                blendshapes = {c.category_name: float(c.score) for c in face.face_blendshapes[i]}
            points = np.array([(lm.x, lm.y) for lm in landmarks], dtype=np.float64)
            faces.append(FaceObservation(landmarks=points, head_matrix=matrix, blendshapes=blendshapes))
        poses = tuple(
            PoseObservation(
                landmarks=np.array([(lm.x, lm.y, _score(lm.visibility)) for lm in landmarks], dtype=np.float64)
            )
            for landmarks in pose.pose_landmarks
            if landmarks
        )
        return RawObservation(faces=tuple(faces), poses=poses)

    def close(self) -> None:
        self._face.close()
        self._pose.close()


class MediaPipeReadingSource(PerceptionSource):
    """The default ReadingSource: real head pose, eyes, shoulders and calibration."""

    def __init__(self, settings: PerceptionSettings) -> None:
        self._mp_backend = MediaPipeBackend(
            resolve_model(settings.face_model),
            resolve_model(settings.pose_model),
            max_faces=settings.max_faces,
            max_poses=settings.max_poses,
        )
        super().__init__(self._mp_backend, settings)

    def close(self) -> None:
        self._mp_backend.close()
