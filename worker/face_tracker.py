"""Following faces from frame to frame. Pure: boxes in, track ids out; no MediaPipe, no camera.

MediaPipe gives faces in no particular order and without identity, so this module
keeps one track per face, matched from one frame to the next by box overlap (IoU,
with the nearest centre as the tiebreak). One track can be *locked*: that is the
scored person. Locking is the only way a track becomes the scored one, and the lock
never moves by itself to another face.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    """An axis-aligned box in normalised image coordinates (0 to 1, y down)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def iou(self, other: "Box") -> float:
        w = min(self.x1, other.x1) - max(self.x0, other.x0)
        h = min(self.y1, other.y1) - max(self.y0, other.y0)
        if w <= 0 or h <= 0:
            return 0.0
        inter = w * h
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def centre_distance(self, other: "Box") -> float:
        (ax, ay), (bx, by) = self.centre, other.centre
        return math.hypot(ax - bx, ay - by)


MIN_IOU = 0.1  # boxes overlapping less than this are not the same face


@dataclass
class _Track:
    id: int
    box: Box  # last known box
    last_seen: float


class FaceTracker:
    """One track per face, a stable id each, and at most one locked track.

    `update` is called once per frame with that frame's face boxes. Tracks that are not
    the locked one are forgotten after `lost_after_s` without a match. The locked track
    is kept (with its last known box) until `unlock`, so the same person can be picked up
    again when they come back, however long they were gone, and no one else can take over.
    """

    def __init__(self, lost_after_s: float) -> None:
        self._lost_after_s = lost_after_s
        self._tracks: list[_Track] = []
        self._next_id = 1
        self._locked_id: int | None = None
        self._frame_ids: list[int] = []
        self._frame_boxes: list[Box] = []
        self._timestamp = 0.0

    @property
    def locked_id(self) -> int | None:
        return self._locked_id

    @property
    def locked_index(self) -> int | None:
        """Index (in the last `update`'s boxes) of the locked face, or None if it is not in view."""
        if self._locked_id is None or self._locked_id not in self._frame_ids:
            return None
        return self._frame_ids.index(self._locked_id)

    @property
    def locked_box(self) -> Box | None:
        """Where the locked face was last seen (None when nothing is locked)."""
        track = self._locked_track()
        return track.box if track is not None else None

    def locked_missing_s(self, timestamp: float) -> float:
        """Seconds since the locked face was last matched (0 when it was matched in the last update)."""
        track = self._locked_track()
        if track is None or self.locked_index is not None:
            return 0.0
        return max(0.0, timestamp - track.last_seen)

    def locked_lost(self, timestamp: float) -> bool:
        """The locked face has been missing for more than `lost_after_s`."""
        return self._locked_id is not None and self.locked_missing_s(timestamp) > self._lost_after_s

    def update(self, boxes: Sequence[Box], timestamp: float) -> list[int]:
        """Match this frame's boxes to the tracks; returns one track id per box, in order."""
        pairs = sorted(
            (-track.box.iou(box), track.box.centre_distance(box), ti, bi)
            for ti, track in enumerate(self._tracks)
            for bi, box in enumerate(boxes)
            if track.box.iou(box) >= MIN_IOU
        )
        ids: list[int | None] = [None] * len(boxes)
        used_tracks: set[int] = set()
        for _, _, ti, bi in pairs:
            if ti in used_tracks or ids[bi] is not None:
                continue
            used_tracks.add(ti)
            track = self._tracks[ti]
            track.box, track.last_seen = boxes[bi], timestamp
            ids[bi] = track.id
        for bi, box in enumerate(boxes):
            if ids[bi] is None:
                self._tracks.append(_Track(self._next_id, box, timestamp))
                ids[bi] = self._next_id
                self._next_id += 1
        self._tracks = [
            t
            for t in self._tracks
            if t.id == self._locked_id or timestamp - t.last_seen <= self._lost_after_s
        ]
        self._frame_ids = [i for i in ids if i is not None]
        self._frame_boxes = list(boxes)
        self._timestamp = timestamp
        return list(self._frame_ids)

    def lock_largest(self) -> int | None:
        """Lock the largest face of the last `update` (the scored person). None if no face was seen."""
        if not self._frame_boxes:
            return None
        largest = max(range(len(self._frame_boxes)), key=lambda i: self._frame_boxes[i].area)
        self._locked_id = self._frame_ids[largest]
        return self._locked_id

    def has_clearly_larger_face(self, factor: float) -> bool:
        """A face in the last `update` has more than `factor` times the area of the locked face."""
        locked = self.locked_box
        if locked is None or not self._frame_boxes:
            return False
        return max(b.area for b in self._frame_boxes) > factor * locked.area

    def unlock(self) -> None:
        self._locked_id = None

    def _locked_track(self) -> _Track | None:
        for t in self._tracks:
            if t.id == self._locked_id:
                return t
        return None
