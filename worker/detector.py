"""Face detection with OpenCV's bundled Haar cascade (replaced by CLA-10)."""

from typing import Any

import cv2

from worker.readings import Reading


class FaceDetector:
    def __init__(self) -> None:
        path = cv2.data.haarcascades  # type: ignore[attr-defined]
        path += "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(path)
        if self._cascade.empty():
            raise RuntimeError(f"Could not load the face cascade from {path}")

    def has_face(self, frame: Any) -> bool:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(
            gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60)
        )
        return len(faces) > 0


class HaarReadingSource:
    """The ReadingSource for the walking skeleton: a face or no face, nothing else.

    No shoulders, head angles or eye state exist yet, so they are fixed (shoulders
    absent, pitch and yaw 0, eyes open). CLA-10 replaces this class.
    """

    def __init__(self, detector: FaceDetector | None = None) -> None:
        self._detector = detector or FaceDetector()

    def read(self, frame: Any | None, timestamp: float) -> Reading:
        face = frame is not None and self._detector.has_face(frame)
        return Reading(timestamp=timestamp, face_present=face, shoulders_present=False)
