"""Face-mesh landmark indices and contour edges for the preview overlay. Pure data.

MediaPipe's FaceLandmarker returns 478 landmarks per face: 468 mesh points plus 5 per iris.
The contour edges below are copied from `FaceLandmarksConnections` in
mediapipe/tasks/python/vision/face_landmarker.py (so the preview needs no MediaPipe import);
tests/test_preview.py checks they still match the installed MediaPipe.

Left and right are the subject's own: "left eye" is the subject's left, which is on the
right of the unmirrored camera image.
"""

Edges = tuple[tuple[int, int], ...]

# Key points, drawn as white dots with a coloured ring.
NOSE_TIP = 1
CHIN = 152
LEFT_EYE_OUTER = 263  # subject's left eye, outer corner (right in the unmirrored image)
RIGHT_EYE_OUTER = 33  # subject's right eye, outer corner
LEFT_MOUTH_CORNER = 291
RIGHT_MOUTH_CORNER = 61
KEY_POINTS = (NOSE_TIP, CHIN, LEFT_EYE_OUTER, RIGHT_EYE_OUTER, LEFT_MOUTH_CORNER, RIGHT_MOUTH_CORNER)

# Iris centres (the last of each iris' five points), drawn as small filled dots.
LEFT_IRIS_CENTRE = 473
RIGHT_IRIS_CENTRE = 468
IRIS_CENTRES = (LEFT_IRIS_CENTRE, RIGHT_IRIS_CENTRE)

FACE_OVAL: tuple[tuple[int, int], ...] = (
    (10, 338), (338, 297), (297, 332), (332, 284), (284, 251), (251, 389), (389, 356), (356, 454),
    (454, 323), (323, 361), (361, 288), (288, 397), (397, 365), (365, 379), (379, 378), (378, 400),
    (400, 377), (377, 152), (152, 148), (148, 176), (176, 149), (149, 150), (150, 136), (136, 172),
    (172, 58), (58, 132), (132, 93), (93, 234), (234, 127), (127, 162), (162, 21), (21, 54),
    (54, 103), (103, 67), (67, 109), (109, 10),
)

LEFT_EYEBROW: tuple[tuple[int, int], ...] = (
    (276, 283), (283, 282), (282, 295), (295, 285), (300, 293), (293, 334), (334, 296), (296, 336),
)

RIGHT_EYEBROW: tuple[tuple[int, int], ...] = (
    (46, 53), (53, 52), (52, 65), (65, 55), (70, 63), (63, 105), (105, 66), (66, 107),
)

LEFT_EYE: tuple[tuple[int, int], ...] = (
    (263, 249), (249, 390), (390, 373), (373, 374), (374, 380), (380, 381), (381, 382), (382, 362),
    (263, 466), (466, 388), (388, 387), (387, 386), (386, 385), (385, 384), (384, 398), (398, 362),
)

RIGHT_EYE: tuple[tuple[int, int], ...] = (
    (33, 7), (7, 163), (163, 144), (144, 145), (145, 153), (153, 154), (154, 155), (155, 133),
    (33, 246), (246, 161), (161, 160), (160, 159), (159, 158), (158, 157), (157, 173), (173, 133),
)

LIPS: tuple[tuple[int, int], ...] = (
    (61, 146), (146, 91), (91, 181), (181, 84), (84, 17), (17, 314), (314, 405), (405, 321),
    (321, 375), (375, 291), (61, 185), (185, 40), (40, 39), (39, 37), (37, 0), (0, 267),
    (267, 269), (269, 270), (270, 409), (409, 291), (78, 95), (95, 88), (88, 178), (178, 87),
    (87, 14), (14, 317), (317, 402), (402, 318), (318, 324), (324, 308), (78, 191), (191, 80),
    (80, 81), (81, 82), (82, 13), (13, 312), (312, 311), (311, 310), (310, 415), (415, 308),
)

# The contours drawn on the tracked face (irises are dots, not contours).
CONTOURS: tuple[Edges, ...] = (FACE_OVAL, LEFT_EYEBROW, RIGHT_EYEBROW, LEFT_EYE, RIGHT_EYE, LIPS)
