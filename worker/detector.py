"""Face detection with OpenCV's bundled Haar cascade (replaced by CLA-10)."""

from typing import Any

import cv2


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
