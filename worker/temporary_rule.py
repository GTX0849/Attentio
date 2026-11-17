"""TEMPORARY state rule for the walking skeleton.

Face detected = Attentive, no face = Not visible.

CLA-8 replaces this with a pure state-rules component that takes timed
readings, and CLA-10 replaces the detector. Nothing else in the worker should
decide a state, so those tickets only need to change this module.
"""

from shared.schema import AttentionState


def classify(face_detected: bool) -> AttentionState:
    return AttentionState.ATTENTIVE if face_detected else AttentionState.NOT_VISIBLE
