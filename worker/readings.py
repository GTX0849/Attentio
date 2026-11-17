"""The reading: what the vision side tells the state rules about one instant.

This is the seam between perception and decisions. The state rules
(worker/states.py) only ever see Readings, so CLA-10 can swap the Haar face
detector for MediaPipe by providing a different ReadingSource.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Reading:
    """One observation, held until the next one (sample and hold)."""

    timestamp: float  # seconds; only differences matter to the rules, but the
    # worker passes epoch seconds so windows line up with the wall clock
    face_present: bool
    shoulders_present: bool
    pitch_deg: float = 0.0  # relative to calibration, positive = looking down; only meaningful with a face
    yaw_deg: float = 0.0  # relative to calibration; only meaningful with a face
    eyes_closed: bool = False


class ReadingSource(Protocol):
    def read(self, frame: Any | None, timestamp: float) -> Reading:
        """Turn a camera frame (None when the camera gave none) into a Reading."""
        ...
