"""The preview window's drawing. Frames are shown, never written to disk."""

from typing import Any

import cv2

from shared.schema import AttentionState
from worker.readings import Reading

# The dashboard's state colours (see the dashboard theme), as RGB hex.
_STATE_HEX = {
    AttentionState.ATTENTIVE: "1B5FD0",
    AttentionState.NEUTRAL: "9DB3D6",
    AttentionState.DISTRACTED: "D4622A",
    AttentionState.NOT_VISIBLE: "C9CDD2",
}
_LIGHT_BACKGROUNDS = {AttentionState.NEUTRAL, AttentionState.NOT_VISIBLE}  # these need dark text
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_STATE_BG_UNKNOWN = (90, 90, 90)  # before the first reading
_WHITE = (255, 255, 255)
_DARK = (40, 30, 20)


def state_bgr(state: AttentionState) -> tuple[int, int, int]:
    """The state's colour in OpenCV's BGR order."""
    h = _STATE_HEX[state]
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def _label(state: AttentionState | None) -> str:
    return "Starting" if state is None else state.value.replace("_", " ").capitalize()


def draw_preview(
    frame: Any,
    reading: Reading,
    state: AttentionState | None,
    allowance_left_s: float,
    calibration_left_s: float | None,
) -> Any:
    """A mirrored copy of `frame` with the state banner and readouts drawn on it.

    `calibration_left_s` is None when not calibrating. Text is drawn after the
    mirroring, so it reads normally.
    """
    out = cv2.flip(frame, 1)
    width = out.shape[1]
    colour = state_bgr(state) if state is not None else _STATE_BG_UNKNOWN
    text_colour = _DARK if state in _LIGHT_BACKGROUNDS else _WHITE
    cv2.rectangle(out, (0, 0), (width, 44), colour, -1)
    cv2.putText(out, _label(state), (12, 31), _FONT, 0.9, text_colour, 2, cv2.LINE_AA)

    if reading.face_present:
        lines = [
            f"Pitch {reading.pitch_deg:+.1f} deg (+ = down)   Yaw {reading.yaw_deg:+.1f} deg",
            "Eyes: closed" if reading.eyes_closed else "Eyes: open",
        ]
    else:
        lines = ["No face", "Shoulders: " + ("in view" if reading.shoulders_present else "not in view")]
    lines.append(f"Look-down allowance left: {allowance_left_s:.1f} s")
    for i, line in enumerate(lines):
        y = 70 + i * 26
        cv2.putText(out, line, (12, y), _FONT, 0.6, (0, 0, 0), 4, cv2.LINE_AA)  # outline for legibility
        cv2.putText(out, line, (12, y), _FONT, 0.6, _WHITE, 1, cv2.LINE_AA)

    if calibration_left_s is not None:
        msg = f"Calibrating... look at the screen ({calibration_left_s:.1f} s)"
        (w, h), _ = cv2.getTextSize(msg, _FONT, 0.8, 2)
        x, y = max(8, (width - w) // 2), out.shape[0] // 2
        cv2.rectangle(out, (x - 10, y - h - 10), (x + w + 10, y + 12), (0, 0, 0), -1)
        cv2.putText(out, msg, (x, y), _FONT, 0.8, (0, 215, 255), 2, cv2.LINE_AA)
    return out

