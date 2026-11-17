"""Preview helpers that need no window: colours and text."""

import numpy as np

from shared.schema import AttentionState
from worker.preview import draw_preview, state_bgr
from worker.readings import Reading


def test_state_colours_are_the_dashboard_colours_in_bgr() -> None:
    assert state_bgr(AttentionState.ATTENTIVE) == (0xD0, 0x5F, 0x1B)  # #1B5FD0
    assert state_bgr(AttentionState.NEUTRAL) == (0xD6, 0xB3, 0x9D)  # #9DB3D6
    assert state_bgr(AttentionState.DISTRACTED) == (0x2A, 0x62, 0xD4)  # #D4622A
    assert state_bgr(AttentionState.NOT_VISIBLE) == (0xD2, 0xCD, 0xC9)  # #C9CDD2


def test_drawing_returns_a_new_mirrored_image_and_leaves_the_input_alone() -> None:
    frame = np.zeros((120, 160, 3), np.uint8)
    frame[:, :40] = (0, 255, 0)  # green strip on the left edge
    reading = Reading(timestamp=1.0, face_present=True, shoulders_present=True, pitch_deg=3.0)
    out = draw_preview(frame, reading, AttentionState.ATTENTIVE, 8.0, None)
    assert out.shape == frame.shape
    assert frame[:, :40].tolist() == np.full((120, 40, 3), (0, 255, 0)).tolist()  # input untouched
    assert tuple(out[100, 150]) == (0, 255, 0)  # the strip is now on the right (below the text banner)
