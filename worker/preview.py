"""The preview window's drawing. Frames are shown, never written to disk.

The layout follows the "Worker Preview" artboards in the design file (1280x720 frame, every
size scaled with the frame width): a state banner across the top with the face counter on
its right, a tag and box on every face, landmark contours and key points on the tracked
face, a readout panel bottom-left, a key hints bar along the bottom and, while
calibrating, a message panel under the banner.
"""

import math
from typing import Any

import cv2
import numpy as np

from shared.schema import AttentionState
from worker import face_mesh
from worker.perception import Diagnostics, FaceDiagnostic
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
_MONO = cv2.FONT_HERSHEY_COMPLEX_SMALL  # the closest OpenCV has to the design's monospace
_STATE_BG_UNKNOWN = (90, 90, 90)  # before the first reading
_WHITE = (255, 255, 255)
_DARK = (40, 30, 20)
_OUTLINE = (20, 15, 10)  # the 1 px darker edge under thin coloured lines
_CALIBRATING = (0x4D, 0xD3, 0xFF)  # #FFD34D in BGR: the track colour while calibrating
_IGNORED = (0xB1, 0xAA, 0xA3)  # #A3AAB1 in BGR
_PANEL = (0x19, 0x14, 0x0F)  # #0F1419 in BGR
_PANEL_ALPHA = 0.72

_BANNER_H = 48  # design sizes, for a 1280 px wide frame
_HINTS_H = 36
_MARGIN = 16
_MIN_SCALE = 0.6  # so a small webcam frame still gets readable text
_ARROW_LENGTH = 90


def state_bgr(state: AttentionState) -> tuple[int, int, int]:
    """The state's colour in OpenCV's BGR order."""
    h = _STATE_HEX[state]
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def _label(state: AttentionState | None) -> str:
    return "Starting" if state is None else state.value.replace("_", " ").capitalize()


def _px(design_px: float, scale: float) -> int:
    return max(1, round(design_px * scale))


def _shade(out: Any, x0: int, y0: int, x1: int, y1: int, alpha: float = _PANEL_ALPHA) -> None:
    """Darken the region with the panel colour at `alpha` opacity (clipped to the image)."""
    h, w = out.shape[:2]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return
    region = out[y0:y1, x0:x1]
    tint = np.empty_like(region)
    tint[:] = _PANEL
    cv2.addWeighted(region, 1 - alpha, tint, alpha, 0, dst=region)


def _text_size(text: str, font: int, scale: float, thickness: int) -> tuple[int, int]:
    (w, h), _ = cv2.getTextSize(text, font, scale, thickness)
    return w, h


def draw_preview(
    frame: Any,
    reading: Reading,
    state: AttentionState | None,
    allowance_left_s: float,
    calibration_left_s: float | None,
    *,
    diagnostics: Diagnostics | None = None,
    overlay: bool = False,
    reference: tuple[float, float] | None = None,
    region: tuple[float, float] | None = None,
    recalibrate_key: str | None = "c",
    landmarks_key: str | None = "m",
) -> Any:
    """A mirrored copy of `frame` with the banner, readouts and (optionally) the landmark overlay.

    `calibration_left_s` is None when not calibrating. `diagnostics` (faces, shoulders) is
    None for sources that only know "a face or not"; the counter and overlay need it.
    `overlay` turns the landmark overlay on; it is always on while calibrating. `reference`
    is the learned (pitch, yaw) centre of the front region and `region` its (up, side)
    extents in degrees; both None until a learner provides them. A key set to None is left
    out of the key hints. Everything is drawn after the mirroring, so text reads normally.
    """
    out = cv2.flip(frame, 1)
    height, width = out.shape[:2]
    scale = max(_MIN_SCALE, width / 1280)
    calibrating = calibration_left_s is not None
    if calibrating:
        colour = _CALIBRATING
        light = True
    else:
        colour = state_bgr(state) if state is not None else _STATE_BG_UNKNOWN
        light = state in _LIGHT_BACKGROUNDS
    text_colour = _DARK if light else _WHITE

    if diagnostics is not None and (overlay or calibrating):
        _draw_overlay(out, diagnostics, colour, text_colour, scale)

    _draw_banner(out, "Calibrating" if calibrating else _label(state), diagnostics, colour, text_colour, scale)
    _draw_readout(out, reading, allowance_left_s, diagnostics, reference, region, scale)
    _draw_key_hints(out, recalibrate_key, landmarks_key if diagnostics is not None else None, scale)
    if calibration_left_s is not None:
        headline = diagnostics.calibration_reason if diagnostics and diagnostics.calibration_reason else "Calibrating"
        _draw_calibration_message(out, headline, calibration_left_s, scale)
    return out


# -- banner, readout, hints, calibration message -----------------------------------


def _draw_banner(
    out: Any,
    label: str,
    diagnostics: Diagnostics | None,
    colour: tuple[int, int, int],
    text_colour: tuple[int, int, int],
    scale: float,
) -> None:
    height, width = out.shape[:2]
    banner = _px(_BANNER_H, scale)
    cv2.rectangle(out, (0, 0), (width, banner), colour, -1)
    font_scale, thickness = 0.95 * scale, max(1, round(2 * scale))
    _, th = _text_size(label, _FONT, font_scale, thickness)
    baseline = (banner + th) // 2
    cv2.putText(out, label, (_px(_MARGIN, scale), baseline), _FONT, font_scale, text_colour, thickness, cv2.LINE_AA)
    if diagnostics is not None:
        counter = f"Faces {diagnostics.face_count} - tracking {diagnostics.tracked_count}"
        mono_scale = 0.95 * scale
        tw, mh = _text_size(counter, _MONO, mono_scale, 1)
        cv2.putText(
            out,
            counter,
            (width - _px(_MARGIN, scale) - tw, (banner + mh) // 2),
            _MONO,
            mono_scale,
            text_colour,
            max(1, round(scale)),
            cv2.LINE_AA,
        )


def _readout_lines(
    reading: Reading,
    allowance_left_s: float,
    diagnostics: Diagnostics | None,
    reference: tuple[float, float] | None,
    region: tuple[float, float] | None,
) -> list[str]:
    lines: list[str] = []
    framing = diagnostics.framing if diagnostics is not None else None
    if framing is not None:  # CLA-19 fills this in
        lines.append(
            f"In frame {framing.in_frame_pct:.0f}% - Size {framing.size_pct:.0f}% - Steady +/-{framing.steady_deg:.1f} deg"
        )
    if reading.face_present:
        eyes = "Eyes closed" if reading.eyes_closed else "Eyes open"
        lines.append(f"Pitch {reading.pitch_deg:+.1f} deg - Yaw {reading.yaw_deg:+.1f} deg - {eyes}")
    else:
        lines += ["No face", "Shoulders: " + ("in view" if reading.shoulders_present else "not in view")]
    if reference is not None and region is not None:
        lines.append(
            f"Reference {reference[0]:+.1f} / {reference[1]:+.1f} - Region up {region[0]:.0f} / side {region[1]:.0f}"
        )
    lines.append(f"Look-down left {allowance_left_s:.1f} s")
    return lines


def _draw_readout(
    out: Any,
    reading: Reading,
    allowance_left_s: float,
    diagnostics: Diagnostics | None,
    reference: tuple[float, float] | None,
    region: tuple[float, float] | None,
    scale: float,
) -> None:
    height, width = out.shape[:2]
    lines = _readout_lines(reading, allowance_left_s, diagnostics, reference, region)
    font_scale = 0.8 * scale
    pad, line_h = _px(14, scale), _px(29, scale)
    text_w = max(_text_size(line, _MONO, font_scale, 1)[0] for line in lines)
    box_w, box_h = text_w + 2 * pad, len(lines) * line_h + pad
    x0 = _px(_MARGIN, scale)
    y1 = height - _px(_HINTS_H, scale) - _px(_MARGIN, scale)
    y0 = y1 - box_h
    _shade(out, x0, y0, x0 + box_w, y1)
    for i, line in enumerate(lines):
        _, th = _text_size(line, _MONO, font_scale, 1)
        y = y0 + pad // 2 + i * line_h + (line_h + th) // 2
        cv2.putText(out, line, (x0 + pad, y), _MONO, font_scale, _WHITE, max(1, round(scale)), cv2.LINE_AA)


def _draw_key_hints(out: Any, recalibrate_key: str | None, landmarks_key: str | None, scale: float) -> None:
    height, width = out.shape[:2]
    top = height - _px(_HINTS_H, scale)
    _shade(out, 0, top, width, height)
    parts = ["q quit"]
    if recalibrate_key:
        parts.append(f"{recalibrate_key} recalibrate")
    if landmarks_key:
        parts.append(f"{landmarks_key} landmarks")
    text = "   ".join(parts)
    font_scale = 0.8 * scale
    tw, th = _text_size(text, _MONO, font_scale, 1)
    cv2.putText(
        out,
        text,
        (width - _px(_MARGIN, scale) - tw, top + (_px(_HINTS_H, scale) + th) // 2),
        _MONO,
        font_scale,
        _WHITE,
        max(1, round(scale)),
        cv2.LINE_AA,
    )


def _draw_calibration_message(out: Any, headline: str, calibration_left_s: float, scale: float) -> None:
    height, width = out.shape[:2]
    detail = f"Look at the teacher and hold still - {calibration_left_s:.1f} s left"
    head_scale, detail_scale = 0.85 * scale, 0.6 * scale
    head_w, head_h = _text_size(headline, _FONT, head_scale, max(1, round(2 * scale)))
    detail_w, detail_h = _text_size(detail, _FONT, detail_scale, 1)
    pad = _px(30, scale)
    box_w = max(_px(600, scale), max(head_w, detail_w) + 2 * pad)
    box_h = _px(75, scale)
    x0 = (width - box_w) // 2
    y0 = _px(_BANNER_H, scale) + _px(12, scale)
    _shade(out, x0, y0, x0 + box_w, y0 + box_h, alpha=0.9)
    cv2.putText(
        out,
        headline,
        ((width - head_w) // 2, y0 + _px(34, scale) + head_h // 2 - _px(6, scale)),
        _FONT,
        head_scale,
        _CALIBRATING,
        max(1, round(2 * scale)),
        cv2.LINE_AA,
    )
    cv2.putText(
        out,
        detail,
        ((width - detail_w) // 2, y0 + _px(58, scale) + detail_h // 2),
        _FONT,
        detail_scale,
        _WHITE,
        1,
        cv2.LINE_AA,
    )


# -- the landmark overlay ---------------------------------------------------------


def _draw_overlay(
    out: Any,
    diagnostics: Diagnostics,
    colour: tuple[int, int, int],
    text_colour: tuple[int, int, int],
    scale: float,
) -> None:
    """Ignored faces first, then the tracked face with its contours, key points, arrow and shoulders."""
    height, width = out.shape[:2]
    line = _px(2, scale)
    for face in diagnostics.faces:
        if not face.tracked:
            _draw_box(out, face, _IGNORED, _DARK, "Ignored", line, scale)
    tracked = diagnostics.tracked
    if tracked is not None:
        _draw_box(out, tracked, colour, text_colour, "Tracked", line, scale)
        # the frame was mirrored, so x runs the other way
        xs = np.round((1.0 - tracked.landmarks[:, 0]) * width).astype(int)
        ys = np.round(tracked.landmarks[:, 1] * height).astype(int)
        _draw_contours(out, xs, ys, colour, line)
        _draw_irises(out, xs, ys, colour, scale)
    if diagnostics.shoulders is not None:
        _draw_shoulders(out, diagnostics.shoulders, colour, line, scale)
    if tracked is not None:
        _draw_arrow(out, xs, ys, tracked, colour, line, scale)
        _draw_key_points(out, xs, ys, colour, scale)


def _draw_box(
    out: Any,
    face: FaceDiagnostic,
    colour: tuple[int, int, int],
    text_colour: tuple[int, int, int],
    label: str,
    line: int,
    scale: float,
) -> None:
    height, width = out.shape[:2]
    b = face.box
    x0, x1 = round((1.0 - b.x1) * width), round((1.0 - b.x0) * width)  # mirrored
    y0, y1 = round(b.y0 * height), round(b.y1 * height)
    cv2.rectangle(out, (x0, y0), (x1, y1), _OUTLINE, line + 2)
    cv2.rectangle(out, (x0, y0), (x1, y1), colour, line)
    font_scale = 0.55 * scale
    tw, th = _text_size(label, _FONT, font_scale, max(1, round(scale)))
    pad = _px(10, scale)
    tag_h = th + 2 * _px(7, scale)
    tag_bottom = max(y0, tag_h)  # keep the tag on screen when the face is at the very top
    cv2.rectangle(out, (x0 - line // 2, tag_bottom - tag_h), (x0 + tw + 2 * pad, tag_bottom), colour, -1)
    cv2.putText(
        out,
        label,
        (x0 + pad, tag_bottom - _px(7, scale)),
        _FONT,
        font_scale,
        text_colour,
        max(1, round(scale)),
        cv2.LINE_AA,
    )


def _draw_contours(out: Any, xs: Any, ys: Any, colour: tuple[int, int, int], line: int) -> None:
    n = len(xs)
    segments = [
        ((int(xs[a]), int(ys[a])), (int(xs[b]), int(ys[b])))
        for contour in face_mesh.CONTOURS
        for a, b in contour
        if a < n and b < n
    ]
    for p, q in segments:  # the darker outline first, so the colour stays readable on any video
        cv2.line(out, p, q, _OUTLINE, line + 2, cv2.LINE_AA)
    for p, q in segments:
        cv2.line(out, p, q, colour, line, cv2.LINE_AA)


def _draw_irises(out: Any, xs: Any, ys: Any, colour: tuple[int, int, int], scale: float) -> None:
    r = _px(3, scale)
    for i in face_mesh.IRIS_CENTRES:
        if i < len(xs):
            cv2.circle(out, (int(xs[i]), int(ys[i])), r + 1, _OUTLINE, -1, cv2.LINE_AA)
            cv2.circle(out, (int(xs[i]), int(ys[i])), r, colour, -1, cv2.LINE_AA)


def _draw_key_points(out: Any, xs: Any, ys: Any, colour: tuple[int, int, int], scale: float) -> None:
    r = _px(6, scale)
    ring = _px(2, scale)
    for i in face_mesh.KEY_POINTS:
        if i < len(xs):
            c = (int(xs[i]), int(ys[i]))
            cv2.circle(out, c, r + 1, _OUTLINE, -1, cv2.LINE_AA)
            cv2.circle(out, c, r, colour, -1, cv2.LINE_AA)  # the ring
            cv2.circle(out, c, max(1, r - ring), _WHITE, -1, cv2.LINE_AA)


def _draw_arrow(
    out: Any,
    xs: Any,
    ys: Any,
    face: FaceDiagnostic,
    colour: tuple[int, int, int],
    line: int,
    scale: float,
) -> None:
    """From the nose tip, where the head points: right in the picture for a head turned right.

    The direction is the raw head yaw and pitch (looking down points down the picture). It is
    90 px long for a head turned 8 degrees or more and shorter near straight ahead, where the
    direction would otherwise jump around with every bit of noise.
    """
    if face.pitch_deg is None or face.yaw_deg is None or face_mesh.NOSE_TIP >= len(xs):
        return
    dx, dy = -face.yaw_deg, face.pitch_deg  # mirrored picture: a head turned to image right looks left
    magnitude = math.hypot(dx, dy)
    ux, uy = (dx / magnitude, dy / magnitude) if magnitude > 0.5 else (0.0, -1.0)
    length = _ARROW_LENGTH * scale * (0.35 + 0.65 * min(1.0, magnitude / 8.0))
    start = (int(xs[face_mesh.NOSE_TIP]), int(ys[face_mesh.NOSE_TIP]))
    end = (round(start[0] + ux * length), round(start[1] + uy * length))
    cv2.arrowedLine(out, start, end, _OUTLINE, line + 2, cv2.LINE_AA, tipLength=0.28)
    cv2.arrowedLine(out, start, end, colour, line, cv2.LINE_AA, tipLength=0.28)


def _draw_shoulders(
    out: Any,
    shoulders: tuple[tuple[float, float], tuple[float, float]],
    colour: tuple[int, int, int],
    line: int,
    scale: float,
) -> None:
    height, width = out.shape[:2]
    points = [(round((1.0 - x) * width), round(y * height)) for x, y in shoulders]
    r = _px(7, scale)
    cv2.line(out, points[0], points[1], _OUTLINE, line + 3, cv2.LINE_AA)
    cv2.line(out, points[0], points[1], colour, line + 1, cv2.LINE_AA)
    for p in points:
        cv2.circle(out, p, r + 1, _OUTLINE, -1, cv2.LINE_AA)
        cv2.circle(out, p, r, colour, -1, cv2.LINE_AA)
