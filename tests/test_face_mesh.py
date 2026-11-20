"""The overlay's landmark data is a copy of MediaPipe's; check it has not drifted."""

from typing import Any

from mediapipe.tasks.python.vision import face_landmarker

from worker import face_mesh

CONNECTIONS = face_landmarker.FaceLandmarksConnections


def edges(connections: list[Any]) -> tuple[tuple[int, int], ...]:
    return tuple((c.start, c.end) for c in connections)


def test_the_contour_edges_match_the_installed_mediapipe() -> None:
    assert face_mesh.FACE_OVAL == edges(CONNECTIONS.FACE_LANDMARKS_FACE_OVAL)
    assert face_mesh.LEFT_EYEBROW == edges(CONNECTIONS.FACE_LANDMARKS_LEFT_EYEBROW)
    assert face_mesh.RIGHT_EYEBROW == edges(CONNECTIONS.FACE_LANDMARKS_RIGHT_EYEBROW)
    assert face_mesh.LEFT_EYE == edges(CONNECTIONS.FACE_LANDMARKS_LEFT_EYE)
    assert face_mesh.RIGHT_EYE == edges(CONNECTIONS.FACE_LANDMARKS_RIGHT_EYE)
    assert face_mesh.LIPS == edges(CONNECTIONS.FACE_LANDMARKS_LIPS)


def test_key_points_sit_on_the_features_they_name() -> None:
    def points(connections: list[Any]) -> set[int]:
        return {i for c in connections for i in (c.start, c.end)}

    assert face_mesh.LEFT_EYE_OUTER in points(CONNECTIONS.FACE_LANDMARKS_LEFT_EYE)
    assert face_mesh.RIGHT_EYE_OUTER in points(CONNECTIONS.FACE_LANDMARKS_RIGHT_EYE)
    assert {face_mesh.LEFT_MOUTH_CORNER, face_mesh.RIGHT_MOUTH_CORNER} <= points(CONNECTIONS.FACE_LANDMARKS_LIPS)
    assert face_mesh.CHIN in points(CONNECTIONS.FACE_LANDMARKS_FACE_OVAL)
    # an iris centre is the point just before its four ring points
    assert face_mesh.LEFT_IRIS_CENTRE + 1 in points(CONNECTIONS.FACE_LANDMARKS_LEFT_IRIS)
    assert face_mesh.RIGHT_IRIS_CENTRE + 1 in points(CONNECTIONS.FACE_LANDMARKS_RIGHT_IRIS)
