"""Aggregates timed state observations into fixed, wall-clock-aligned windows.

Each observation is held (sample and hold) until the next one, so the time
between two observations is credited to the earlier observation's state. An
interval that crosses a window boundary is split exactly at the boundary.
"""

import math
from datetime import UTC, datetime

from shared.schema import AttentionRecord, AttentionState


class WindowAggregator:
    def __init__(self, camera_id: str, student_id: str, window_seconds: int) -> None:
        self._camera_id = camera_id
        self._student_id = student_id
        self._window = window_seconds
        self._held: tuple[float, AttentionState] | None = None  # (time, state)
        self._totals: dict[AttentionState, float] = {}
        self._window_start: float | None = None  # epoch seconds of current window

    def observe(self, t: float, state: AttentionState) -> list[AttentionRecord]:
        """Record that `state` was seen at time `t` (epoch seconds).

        Returns the records of any windows completed by this observation.
        """
        finished = self._advance_to(t)
        self._held = (t, state)
        return finished

    def flush(self, t: float) -> list[AttentionRecord]:
        """Close the partial window at time `t` (used on shutdown)."""
        finished = self._advance_to(t)
        if self._window_start is not None and self._totals:
            finished.append(self._make_record())
        self._held = None
        self._totals = {}
        self._window_start = None
        return finished

    def _advance_to(self, t: float) -> list[AttentionRecord]:
        """Credit the held state up to `t`, emitting windows that end before it."""
        finished: list[AttentionRecord] = []
        if self._held is None:
            return finished
        start, state = self._held
        while start < t:
            window_start = math.floor(start / self._window) * self._window
            self._window_start = window_start
            window_end = window_start + self._window
            end = min(t, window_end)
            self._totals[state] = self._totals.get(state, 0.0) + (end - start)
            start = end
            if end == window_end:  # window complete
                finished.append(self._make_record())
                self._totals = {}
                self._window_start = None
        self._held = (t, state)
        return finished

    def _make_record(self) -> AttentionRecord:
        assert self._window_start is not None
        return AttentionRecord(
            camera_id=self._camera_id,
            student_id=self._student_id,
            window_start=datetime.fromtimestamp(self._window_start, tz=UTC),
            attentive=self._totals.get(AttentionState.ATTENTIVE, 0.0),
            neutral=self._totals.get(AttentionState.NEUTRAL, 0.0),
            distracted=self._totals.get(AttentionState.DISTRACTED, 0.0),
            not_visible=self._totals.get(AttentionState.NOT_VISIBLE, 0.0),
        )
