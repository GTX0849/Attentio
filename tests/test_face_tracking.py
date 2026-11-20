"""Who gets scored when the camera sees several people.

Everything goes through PerceptionSource with a fake backend that returns made-up faces
and bodies over time; no camera and no model files.
"""

import pytest

from tests.test_perception import FRAME, SETTINGS, FakeBackend, face_obs, pose_obs
from worker.perception import Diagnostics, FaceObservation, PerceptionSource, PoseObservation

TEACHER_SIDE = (0.25, 0.5)  # a face to the left of the scored one
FRONT = (0.6, 0.5)  # the scored person (the largest face), centre-right


def scene(
    backend: FakeBackend,
    faces: list[FaceObservation],
    poses: list[PoseObservation] | None = None,
) -> None:
    backend.scene(faces, poses)


def run(
    source: PerceptionSource,
    backend: FakeBackend,
    start: float,
    seconds: float,
    faces: list[FaceObservation],
    poses: list[PoseObservation] | None = None,
    step: float = 0.1,
) -> None:
    """Feed the same scene for `seconds`, one frame every `step`."""
    scene(backend, faces, poses)
    for i in range(round(seconds / step)):
        source.read(FRAME, start + i * step)


def calibrate_on_two(backend: FakeBackend) -> PerceptionSource:
    """Calibrated at t=100..103.5 with a big face at pitch 10 / yaw -4 and a small one beside it."""
    source = PerceptionSource(backend, SETTINGS)
    run(
        source,
        backend,
        100.0,
        3.5,
        [face_obs(*TEACHER_SIDE, size=0.1, pitch=0), face_obs(*FRONT, size=0.25, pitch=10, yaw=-4)],
        [pose_obs(nose=FRONT)],
    )
    assert not source.calibrating
    return source


def test_the_largest_face_at_calibration_is_the_one_scored() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    # the big face is now 20 degrees down from where it calibrated; the small one is at 45
    scene(
        backend,
        [face_obs(*TEACHER_SIDE, size=0.1, pitch=45), face_obs(*FRONT, size=0.25, pitch=30, yaw=-4)],
        [pose_obs(nose=FRONT)],
    )
    r, diag = source.read_with_diagnostics(FRAME, 104.0)
    assert r.face_present
    assert r.pitch_deg == pytest.approx(20.0)
    assert diag.tracked is not None and diag.tracked.box.centre == pytest.approx(FRONT)


def test_the_scored_face_is_followed_as_it_moves_and_stays_scored_when_others_become_larger() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    first_id = source.diagnostics.tracked.track_id if source.diagnostics.tracked else None
    assert first_id is not None
    x = FRONT[0]
    t = 104.0
    for step in range(12):  # drifts left, towards the other face, shrinking while the other one grows
        x -= 0.02
        size = 0.25 - 0.005 * step
        scene(
            backend,
            [face_obs(*TEACHER_SIDE, size=0.1 + 0.02 * step), face_obs(x, FRONT[1], size=size, pitch=14, yaw=-4)],
            [pose_obs(nose=(x, FRONT[1]))],
        )
        r, diag = source.read_with_diagnostics(FRAME, t)
        t += 0.1
        assert r.pitch_deg == pytest.approx(4.0)  # still the same person: 14 - 10
        assert diag.tracked is not None and diag.tracked.track_id == first_id
    assert diag.tracked_count == 1 and diag.face_count == 2


def test_a_short_dropout_keeps_the_last_reading_of_the_scored_face() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    scene(backend, [face_obs(*FRONT, size=0.25, pitch=30, yaw=-4)], [pose_obs(nose=FRONT)])
    source.read(FRAME, 104.0)
    scene(backend, [], [pose_obs(nose=FRONT)])
    r = source.read(FRAME, 104.5)  # half a second without a face
    assert r.face_present and r.pitch_deg == pytest.approx(20.0)


def test_after_the_scored_face_is_missing_for_over_a_second_readings_are_face_absent() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    other = face_obs(*TEACHER_SIDE, size=0.1)
    scene(backend, [other, face_obs(*FRONT, size=0.25, pitch=30, yaw=-4)], [pose_obs(nose=FRONT)])
    source.read(FRAME, 104.0)
    scene(backend, [other], [pose_obs(nose=FRONT)])  # the scored face vanishes, its body stays
    assert source.read(FRAME, 105.0).face_present  # exactly 1 s: still within the limit
    r = source.read(FRAME, 105.2)
    assert not r.face_present
    assert r.shoulders_present  # the body is still where the face was
    assert source.diagnostics.tracked_count == 0
    assert source.diagnostics.face_count == 1


def test_it_never_switches_to_another_face_while_the_scored_one_is_gone() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    # only the other (smaller, elsewhere) face is in view for a long time, looking straight at the camera
    run(source, backend, 105.0, 10.0, [face_obs(*TEACHER_SIDE, size=0.1, pitch=10, yaw=-4)])
    r = source.read(FRAME, 115.0)
    assert not r.face_present
    # and a brand-new face appearing somewhere else does not take over either
    scene(backend, [face_obs(0.9, 0.2, size=0.3)])
    assert not source.read(FRAME, 115.1).face_present
    assert source.diagnostics.tracked_count == 0


def test_the_scored_face_is_picked_up_again_when_a_face_reappears_where_it_was() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    run(source, backend, 104.0, 5.0, [face_obs(*TEACHER_SIDE, size=0.1)], [pose_obs(nose=FRONT)])
    assert not source.read(FRAME, 109.0).face_present
    scene(backend, [face_obs(*TEACHER_SIDE, size=0.1), face_obs(FRONT[0] + 0.03, FRONT[1], size=0.25, pitch=22, yaw=-4)])
    r = source.read(FRAME, 109.1)
    assert r.face_present
    assert r.pitch_deg == pytest.approx(12.0)  # 22 - 10


def test_recalibrating_locks_onto_the_largest_face_in_view_then() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    # the front person leans back and the small face's owner steps up: now the left one is largest
    source.recalibrate()
    run(
        source,
        backend,
        110.0,
        3.5,
        [face_obs(*TEACHER_SIDE, size=0.3, pitch=-6), face_obs(*FRONT, size=0.2, pitch=10)],
        [pose_obs(nose=TEACHER_SIDE)],
    )
    assert not source.calibrating
    scene(
        backend,
        [face_obs(*TEACHER_SIDE, size=0.3, pitch=4), face_obs(*FRONT, size=0.2, pitch=60)],
        [pose_obs(nose=TEACHER_SIDE)],
    )
    r = source.read(FRAME, 114.0)
    assert r.pitch_deg == pytest.approx(10.0)  # 4 - (-6): the new person's calibration, not the old one's


def test_a_lost_lock_before_calibration_ends_goes_to_the_largest_face_and_restarts_calibration() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    # someone walks through first and leaves; the student sits down later and is the only one left
    run(source, backend, 200.0, 2.0, [face_obs(0.3, 0.5, size=0.3, pitch=-20)])
    assert source.calibrating
    run(source, backend, 202.0, 2.0, [face_obs(0.7, 0.5, size=0.2, pitch=8)])
    assert source.calibrating  # restarted when it moved on
    run(source, backend, 204.0, 4.0, [face_obs(0.7, 0.5, size=0.2, pitch=8)])
    assert not source.calibrating
    scene(backend, [face_obs(0.7, 0.5, size=0.2, pitch=18)])
    assert source.read(FRAME, 208.0).pitch_deg == pytest.approx(10.0)  # relative to the student, not the walker


def test_shoulders_come_only_from_the_body_inside_the_scored_face() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    scored = face_obs(*FRONT, size=0.25, pitch=10, yaw=-4)
    behind = pose_obs(nose=(0.1, 0.3), shoulders=(0.95, 0.95))  # somebody else, shoulders in clear view
    scene(backend, [scored], [behind, pose_obs(nose=FRONT, shoulders=(0.9, 0.2))])
    assert not source.read(FRAME, 104.0).shoulders_present  # own body: one shoulder out of view
    scene(backend, [scored], [behind])
    assert not source.read(FRAME, 104.1).shoulders_present  # only the other person's shoulders are visible
    scene(backend, [scored], [behind, pose_obs(nose=FRONT, shoulders=(0.9, 0.8))])
    assert source.read(FRAME, 104.2).shoulders_present


def test_diagnostics_list_the_tracked_face_and_the_ignored_ones_with_the_shoulder_points() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    scene(
        backend,
        [face_obs(*TEACHER_SIDE, size=0.1), face_obs(*FRONT, size=0.25, pitch=10, yaw=-4), face_obs(0.9, 0.3, size=0.08)],
        [pose_obs(nose=FRONT)],
    )
    r, diag = source.read_with_diagnostics(FRAME, 104.0)
    assert isinstance(diag, Diagnostics)
    assert (diag.face_count, diag.tracked_count) == (3, 1)
    assert [f.tracked for f in diag.faces] == [False, True, False]
    assert len({f.track_id for f in diag.faces}) == 3
    assert diag.shoulders == ((FRONT[0] - 0.15, FRONT[1] + 0.2), (FRONT[0] + 0.15, FRONT[1] + 0.2))
    assert diag.tracked is not None and diag.tracked.pitch_deg == pytest.approx(10.0)
    assert source.diagnostics is diag
    assert r.shoulders_present


def test_a_missing_frame_leaves_empty_diagnostics() -> None:
    backend = FakeBackend()
    source = calibrate_on_two(backend)
    _, diag = source.read_with_diagnostics(None, 104.0)
    assert (diag.face_count, diag.tracked_count, diag.shoulders) == (0, 0, None)


def test_during_calibration_a_clearly_larger_face_that_shows_up_takes_the_lock_and_restarts_calibration() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    # at first only a small, far-away face is detected (or the near one has not been found yet)
    run(source, backend, 300.0, 1.0, [face_obs(*TEACHER_SIDE, size=0.1, pitch=-30)])
    assert source.calibrating
    both = [face_obs(*TEACHER_SIDE, size=0.1, pitch=-30), face_obs(*FRONT, size=0.25, pitch=10)]
    run(source, backend, 301.0, 2.5, both)
    assert source.calibrating  # the timer restarted at 301 when the lock moved to the bigger face
    run(source, backend, 303.5, 1.0, both)
    assert not source.calibrating
    scene(backend, both[:1] + [face_obs(*FRONT, size=0.25, pitch=25)])
    r, diag = source.read_with_diagnostics(FRAME, 306.0)
    assert r.pitch_deg == pytest.approx(15.0)  # calibrated on the big face (10), not the small one (-30)
    assert diag.tracked is not None and diag.tracked.box.centre == pytest.approx(FRONT)


def test_a_slightly_larger_face_during_calibration_does_not_steal_the_lock() -> None:
    backend = FakeBackend()
    source = PerceptionSource(backend, SETTINGS)
    run(source, backend, 400.0, 1.0, [face_obs(*FRONT, size=0.25, pitch=10), face_obs(*TEACHER_SIDE, size=0.2, pitch=-30)])
    run(source, backend, 401.0, 3.0, [face_obs(*FRONT, size=0.25, pitch=10), face_obs(*TEACHER_SIDE, size=0.26, pitch=-30)])
    assert not source.calibrating  # no restart: the first lock held
    scene(backend, [face_obs(*FRONT, size=0.25, pitch=25), face_obs(*TEACHER_SIDE, size=0.26, pitch=-30)])
    assert source.read(FRAME, 404.1).pitch_deg == pytest.approx(15.0)
