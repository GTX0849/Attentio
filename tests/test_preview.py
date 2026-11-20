"""Preview helpers that need no window: colours and text."""

import numpy as np

from shared.schema import AttentionState
from worker.face_tracker import Box
from worker.perception import Diagnostics, FaceDiagnostic
from worker.preview import draw_preview, state_bgr
from worker.readings import Reading


def test_state_colours_are_the_dashboard_colours_in_bgr() -> None:
    assert state_bgr(AttentionState.ATTENTIVE) == (0xD0, 0x5F, 0x1B)  # #1B5FD0
    assert state_bgr(AttentionState.NEUTRAL) == (0xD6, 0xB3, 0x9D)  # #9DB3D6
    assert state_bgr(AttentionState.DISTRACTED) == (0x2A, 0x62, 0xD4)  # #D4622A
    assert state_bgr(AttentionState.NOT_VISIBLE) == (0xD2, 0xCD, 0xC9)  # #C9CDD2


def test_drawing_returns_a_new_mirrored_image_and_leaves_the_input_alone() -> None:
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[:, :40] = (0, 255, 0)  # green strip on the left edge
    reading = Reading(timestamp=1.0, face_present=True, shoulders_present=True, pitch_deg=3.0)
    out = draw_preview(frame, reading, AttentionState.ATTENTIVE, 8.0, None)
    assert out.shape == frame.shape
    assert frame[:, :40].tolist() == np.full((360, 40, 3), (0, 255, 0)).tolist()  # input untouched
    assert tuple(out[200, 620]) == (0, 255, 0)  # the strip is now on the right (clear of the banner and hints bar)


# -- the multi-face overlay ----------------------------------------------------------

CALIBRATING_BGR = (0x4D, 0xD3, 0xFF)  # #FFD34D
IGNORED_BGR = (0xB1, 0xAA, 0xA3)  # #A3AAB1


def face_diag(track_id: int, x0: float, y0: float, x1: float, y1: float, tracked: bool) -> FaceDiagnostic:
    """A made-up 478-point face filling the box, nose in the middle."""
    rng = np.random.default_rng(track_id)
    pts = np.column_stack([rng.uniform(x0, x1, 478), rng.uniform(y0, y1, 478)])
    pts[0] = (x0, y0)
    pts[1] = ((x0 + x1) / 2, (y0 + y1) / 2)  # nose tip
    pts[2] = (x1, y1)
    return FaceDiagnostic(
        track_id=track_id,
        box=Box(float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())),
        landmarks=pts,
        tracked=tracked,
        pitch_deg=-4.0,
        yaw_deg=2.5,
    )


def three_faces() -> Diagnostics:
    return Diagnostics(
        faces=(
            face_diag(1, 0.15, 0.25, 0.25, 0.45, tracked=False),
            face_diag(2, 0.40, 0.25, 0.60, 0.65, tracked=True),
            face_diag(3, 0.78, 0.24, 0.86, 0.40, tracked=False),
        ),
        shoulders=((0.35, 0.78), (0.65, 0.78)),
    )


def count_pixels(image: np.ndarray, bgr: tuple[int, int, int]) -> int:
    return int((image == np.array(bgr, np.uint8)).all(axis=2).sum())


def test_the_overlay_draws_tracked_and_ignored_faces_while_calibrating() -> None:
    frame = np.zeros((720, 1280, 3), np.uint8)
    reading = Reading(timestamp=1.0, face_present=True, shoulders_present=True)
    out = draw_preview(frame, reading, None, 8.0, 2.1, diagnostics=three_faces())
    assert out.shape == frame.shape
    assert tuple(out[5, 600]) == CALIBRATING_BGR  # the banner is yellow while calibrating
    assert count_pixels(out, CALIBRATING_BGR) > 5000  # banner, tracked box, contours
    assert count_pixels(out, IGNORED_BGR) > 100  # the two ignored boxes and tags
    assert not frame.any()  # the input is untouched


def test_the_overlay_is_off_after_calibration_until_asked_for_but_the_banner_and_counter_stay() -> None:
    frame = np.zeros((720, 1280, 3), np.uint8)
    reading = Reading(timestamp=1.0, face_present=True, shoulders_present=True)
    plain = draw_preview(frame, reading, AttentionState.ATTENTIVE, 8.0, None, diagnostics=three_faces())
    assert tuple(plain[5, 600]) == state_bgr(AttentionState.ATTENTIVE)
    assert count_pixels(plain, IGNORED_BGR) == 0
    assert plain[:48, 900:].any(axis=2).sum() > 0  # the counter text is on the banner's right side
    shown = draw_preview(frame, reading, AttentionState.ATTENTIVE, 8.0, None, diagnostics=three_faces(), overlay=True)
    assert count_pixels(shown, IGNORED_BGR) > 100
    assert count_pixels(shown, state_bgr(AttentionState.ATTENTIVE)) > count_pixels(plain, state_bgr(AttentionState.ATTENTIVE))


def test_the_overlay_copes_with_no_tracked_face_a_face_at_the_edge_and_a_small_frame() -> None:
    reading = Reading(timestamp=1.0, face_present=False, shoulders_present=False)
    edge = Diagnostics(faces=(face_diag(5, 0.0, 0.0, 0.1, 0.1, tracked=False),))
    for shape in [(720, 1280, 3), (480, 640, 3), (120, 160, 3)]:
        frame = np.zeros(shape, np.uint8)
        for diagnostics in (Diagnostics(), edge, three_faces()):
            for state, calibration_left in [(AttentionState.DISTRACTED, None), (None, 3.0)]:
                out = draw_preview(
                    frame, reading, state, 0.0, calibration_left, diagnostics=diagnostics, overlay=True, reference=(-3.1, 1.8), region=(30.0, 35.0)
                )
                assert out.shape == frame.shape


def test_a_face_with_fewer_landmarks_than_the_full_mesh_still_draws() -> None:
    face = face_diag(7, 0.4, 0.3, 0.6, 0.7, tracked=True)
    short = FaceDiagnostic(7, face.box, face.landmarks[:10], True, None, None)
    reading = Reading(timestamp=1.0, face_present=True, shoulders_present=False)
    out = draw_preview(np.zeros((360, 640, 3), np.uint8), reading, AttentionState.NEUTRAL, 8.0, None, diagnostics=Diagnostics(faces=(short,)), overlay=True)
    assert out.shape == (360, 640, 3)


def bright(region: np.ndarray) -> int:
    return int((region.min(axis=2) > 200).sum())  # white text pixels


def test_without_diagnostics_nothing_about_faces_is_drawn_and_the_landmarks_hint_is_left_out() -> None:
    frame = np.zeros((720, 1280, 3), np.uint8)
    reading = Reading(timestamp=1.0, face_present=True, shoulders_present=True)
    out = draw_preview(frame, reading, AttentionState.ATTENTIVE, 8.0, None, overlay=True, recalibrate_key=None, landmarks_key="m")
    with_hint = draw_preview(frame, reading, AttentionState.ATTENTIVE, 8.0, None, diagnostics=Diagnostics(), recalibrate_key=None)
    # the hints bar is shorter without the "m landmarks" hint
    assert bright(out[684:]) < bright(with_hint[684:])
