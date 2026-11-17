"""Readings in, finished 30-second window records out."""

from shared.schema import AttentionRecord, AttentionState
from worker.readings import Reading
from worker.states import StateClassifier, StateThresholds
from worker.windows import WindowAggregator


class AttentionTracker:
    def __init__(
        self, camera_id: str, student_id: str, window_seconds: int, thresholds: StateThresholds
    ) -> None:
        self._classifier = StateClassifier(thresholds)
        self._aggregator = WindowAggregator(camera_id, student_id, window_seconds)

    def observe(self, reading: Reading) -> list[AttentionRecord]:
        """Feed one reading; returns the records of any windows it completes."""
        return self._feed(self._classifier.observe(reading))

    def flush(self, timestamp: float) -> list[AttentionRecord]:
        """End the stream at `timestamp`, returning the completed and the partial window."""
        records = self._feed(self._classifier.close(timestamp))
        return records + self._aggregator.flush(timestamp)

    def current_state(self, at: float) -> AttentionState | None:
        """The state in force at `at`, for the preview (read-only)."""
        return self._classifier.current_state(at)

    def look_down_remaining_s(self, at: float) -> float:
        """Look-down allowance left at `at`, for the preview (read-only)."""
        return self._classifier.look_down_remaining_s(at)

    def _feed(self, transitions: list[tuple[float, AttentionState]]) -> list[AttentionRecord]:
        records: list[AttentionRecord] = []
        for t, state in transitions:
            records += self._aggregator.observe(t, state)
        return records
