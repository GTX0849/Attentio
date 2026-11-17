"""The MediaPipe side: FaceLandmarker and PoseLandmarker in VIDEO mode.

Kept thin on purpose. It only runs the models and hands back a RawObservation;
all thresholds, calibration and angle maths live in worker/perception.py.
"""

from pathlib import Path
from typing import Any

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

from shared.config import PROJECT_ROOT
from worker.perception import PerceptionSettings, PerceptionSource, RawObservation

LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12


def resolve_model(path: str) -> Path:
    """A model path from the config: relative paths are relative to the project root."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


class MediaPipeBackend:
    def __init__(self, face_model: Path, pose_model: Path) -> None:
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
                num_faces=1,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
        )
        self._pose = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(pose_model)),
                running_mode=mode,
                num_poses=1,
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

        matrix = None
        blendshapes: dict[str, float] = {}
        if face.face_landmarks and face.facial_transformation_matrixes:
            matrix = face.facial_transformation_matrixes[0]
            if face.face_blendshapes:
                blendshapes = {c.category_name: float(c.score) for c in face.face_blendshapes[0]}
        shoulders: tuple[float, float] | None = None
        if pose.pose_landmarks:
            lm = pose.pose_landmarks[0]
            shoulders = (float(lm[LEFT_SHOULDER].visibility), float(lm[RIGHT_SHOULDER].visibility))
        return RawObservation(head_matrix=matrix, blendshapes=blendshapes, shoulder_visibility=shoulders)

    def close(self) -> None:
        self._face.close()
        self._pose.close()


class MediaPipeReadingSource(PerceptionSource):
    """The default ReadingSource: real head pose, eyes, shoulders and calibration."""

    def __init__(self, settings: PerceptionSettings) -> None:
        self._mp_backend = MediaPipeBackend(resolve_model(settings.face_model), resolve_model(settings.pose_model))
        super().__init__(self._mp_backend, settings)

    def close(self) -> None:
        self._mp_backend.close()
