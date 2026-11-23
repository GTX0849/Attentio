"""Attention and presence figures, as plain functions over summed seconds.

Every figure for a period (a lecture, a subject, a week, the whole semester) is
worked out from the SUMMED seconds of that period, never by averaging the
percentages of its windows. The pieces:

- `Seconds` holds seconds per state; `total_seconds` adds a list of them.
- `attention(attentive, distracted)` is attentive / (attentive + distracted).
- `presence(...)` is the in-view share of the seconds recorded.
- `attention_figure(seconds, min_presence)` applies the visibility gate: when
  presence over the period is below the threshold the value is None and
  `reason` says why ("insufficient data").
- `lecture_presence(seconds, scheduled)` and `is_present` decide attendance
  against the lecture's scheduled length, so time with no record counts as
  not present.

The threshold is `scores.min_presence_ratio` in config/attentio.yaml.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from shared.config import load_config

# Why an attention figure has no value.
NO_RECORDS = "no_records"  # nothing was recorded in the period
LOW_PRESENCE = "low_presence"  # in view for less than the threshold: insufficient data
NO_SCORED_TIME = "no_scored_time"  # in view, but neither attentive nor distracted for any time


@dataclass(frozen=True)
class Seconds:
    """Seconds spent in each state over some period."""

    attentive: float = 0.0
    neutral: float = 0.0
    distracted: float = 0.0
    not_visible: float = 0.0

    def __add__(self, other: "Seconds") -> "Seconds":
        return Seconds(
            self.attentive + other.attentive,
            self.neutral + other.neutral,
            self.distracted + other.distracted,
            self.not_visible + other.not_visible,
        )

    @property
    def visible(self) -> float:
        """Seconds the student was in view."""
        return self.attentive + self.neutral + self.distracted

    @property
    def total(self) -> float:
        return self.visible + self.not_visible


@dataclass(frozen=True)
class AttentionFigure:
    """Attention over a period. `value` is None, with a `reason`, when it cannot be shown."""

    value: float | None
    reason: str | None
    presence: float | None
    seconds: Seconds


def min_presence_ratio() -> float:
    """The presence threshold from the `scores:` section of the shared config."""
    return float(load_config().raw["scores"]["min_presence_ratio"])


def total_seconds(parts: Iterable[Seconds]) -> Seconds:
    total = Seconds()
    for part in parts:
        total = total + part
    return total


def attention(attentive: float, distracted: float) -> float | None:
    """Attentive share of attentive plus distracted seconds; None if there are none."""
    denominator = attentive + distracted
    return attentive / denominator if denominator > 0 else None


def presence(attentive: float, neutral: float, distracted: float, not_visible: float) -> float | None:
    """Share of the recorded seconds the student was in view; None when nothing was recorded."""
    total = attentive + neutral + distracted + not_visible
    return (attentive + neutral + distracted) / total if total > 0 else None


def period_presence(seconds: Seconds) -> float | None:
    return presence(seconds.attentive, seconds.neutral, seconds.distracted, seconds.not_visible)


def attention_figure(seconds: Seconds, min_presence: float) -> AttentionFigure:
    """Attention over a period, with the visibility gate applied."""
    shown = period_presence(seconds)
    if shown is None:
        return AttentionFigure(None, NO_RECORDS, None, seconds)
    if shown < min_presence:
        return AttentionFigure(None, LOW_PRESENCE, shown, seconds)
    value = attention(seconds.attentive, seconds.distracted)
    if value is None:
        return AttentionFigure(None, NO_SCORED_TIME, shown, seconds)
    return AttentionFigure(value, None, shown, seconds)


def scheduled_seconds(starts_at: datetime, ends_at: datetime | None, now: datetime) -> float:
    """Length of a lecture: start to end, or start to `now` while a session is open."""
    return max(0.0, ((ends_at if ends_at is not None else now) - starts_at).total_seconds())


def elapsed_seconds(starts_at: datetime, ends_at: datetime | None, now: datetime) -> float:
    """How much of the lecture has gone by at `now`."""
    stop = now if ends_at is None else min(now, ends_at)
    return max(0.0, (stop - starts_at).total_seconds())


def lecture_presence(seconds: Seconds, scheduled: float) -> float | None:
    """In-view share of the lecture's whole length, at most 1; None for a zero-length lecture."""
    if scheduled <= 0:
        return None
    return min(1.0, seconds.visible / scheduled)


def is_present(lecture_presence_ratio: float | None, min_presence: float) -> bool:
    """Attended: in view for at least the threshold share of the whole lecture."""
    return lecture_presence_ratio is not None and lecture_presence_ratio >= min_presence
