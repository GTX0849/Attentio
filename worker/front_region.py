"""The learned front region: a moving reference direction and a personal spread.

A student counts as Attentive while the head points toward the front of the room
in any natural way. Where "the front" is differs between students, and it moves
during a lecture (slouching, leaning, a teacher walking across the room). So the
state rules do not measure the head against the calibration pose alone: they
measure it against a reference direction that is learned all through the lecture.

What is learned (all per student, all from head pitch and yaw relative to calibration)
* Reference direction: the median of pitch and of yaw over the last `window_s`
  seconds, taken only from readings that were already inside the current front
  region (Attentive) and had a face. Capped at +/- `max_reference_pitch_deg` and
  +/- `max_reference_yaw_deg` from calibration, so the reference can never wander
  far from "looking at the screen".
* Personal spread: how much the head varies around the reference while Attentive.
  The measure is the `spread_percentile`-th percentile (nearest rank, 90 by
  default) of |angle - reference| over the same samples, kept separately for
  pitch (the up extent) and yaw (the sideways extent). A percentile is robust:
  one stray reading cannot widen it.
* Extents: extent = clamp(`spread_multiplier` x spread, minimum, maximum), with
  its own minimum and maximum for up and for sideways. The extents are how far
  up and how far to either side of the reference still counts as Attentive.

Until `min_attentive_s` seconds of Attentive data are in the window nothing is
learned: the reference stays at calibration (0, 0) and the extents are the
camera's limits from the config (`pitch_attentive_min_deg`, `yaw_max_deg`).

What is never learned: the downward limits (Attentive up to +15, Neutral up to
+38, the look-down allowance and its refill). They stay fixed, measured from the
reference.

Cost: at about 10 readings a second the window holds about 1200 samples. Each
reading is an O(1) append; the median and percentiles are recomputed at most
once per `update_interval_s`, so the work is bounded and deterministic (no
randomness, no wall clock: only the reading timestamps).

Production note: the profile lives only in memory and is discarded when the
session ends. A persistent store (a student's profile carried between
lectures) may be added behind `FrontRegionProfile` once consent and retention
have been designed; nothing here writes anything to disk.
"""

import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LearningConfig:
    window_s: float  # the reference and spread come from this many seconds of Attentive readings
    max_samples: int  # bound on the stored samples, whatever the reading rate
    max_gap_s: float  # a gap between two Attentive readings counts for at most this much data
    update_interval_s: float  # the median and spread are recomputed at most this often
    min_attentive_s: float  # seconds of Attentive data needed before anything is learned
    max_reference_pitch_deg: float  # the reference stays within +/- this of calibration (pitch)
    max_reference_yaw_deg: float  # ... and this (yaw)
    spread_percentile: float  # which percentile of |angle - reference| is the spread (0 to 100)
    spread_multiplier: float  # k in extent = clamp(k x spread, minimum, maximum)
    min_up_extent_deg: float
    max_up_extent_deg: float
    min_side_extent_deg: float
    max_side_extent_deg: float

    @classmethod
    def from_config(cls, section: dict[str, Any]) -> "LearningConfig":
        """Read the `states.learning:` section of config/classpulse.yaml."""
        values: dict[str, Any] = {name: float(section[name]) for name in cls.__dataclass_fields__}
        values["max_samples"] = int(section["max_samples"])
        return cls(**values)


class FrontRegionProfile(Protocol):
    """One student's learned front region. Swappable: see the module docstring."""

    def learn(self, timestamp: float, pitch_deg: float, yaw_deg: float) -> None:
        """Take a reading that was Attentive (face present). Timestamps never go back."""
        ...

    def reference(self) -> tuple[float, float]:
        """The reference direction (pitch, yaw) relative to calibration; (0, 0) until learned."""
        ...

    def extents(self) -> tuple[float, float] | None:
        """The learned (up, sideways) extents in degrees, or None until enough data exists."""
        ...

    def reset(self) -> None:
        """Forget everything (recalibration)."""
        ...


class InMemoryFrontRegionProfile:
    """The only implementation for now: lives in memory and is discarded at session end."""

    def __init__(self, config: LearningConfig) -> None:
        self._cfg = config
        # (timestamp, pitch, yaw, seconds of data this sample stands for)
        self._samples: deque[tuple[float, float, float, float]] = deque()
        self._reset_state()

    def _reset_state(self) -> None:
        self._samples.clear()
        self._last_t: float | None = None
        self._last_update: float | None = None
        self._reference = (0.0, 0.0)
        self._extents: tuple[float, float] | None = None

    def reset(self) -> None:
        self._reset_state()

    def reference(self) -> tuple[float, float]:
        return self._reference

    def extents(self) -> tuple[float, float] | None:
        return self._extents

    def learn(self, timestamp: float, pitch_deg: float, yaw_deg: float) -> None:
        cfg = self._cfg
        t = timestamp if self._last_t is None else max(timestamp, self._last_t)
        data_s = 0.0 if self._last_t is None else min(t - self._last_t, cfg.max_gap_s)
        self._last_t = t
        self._samples.append((t, pitch_deg, yaw_deg, data_s))
        while self._samples and (
            self._samples[0][0] < t - cfg.window_s or len(self._samples) > cfg.max_samples
        ):
            self._samples.popleft()
        if self._last_update is None or t - self._last_update >= cfg.update_interval_s:
            self._last_update = t
            self._update()

    def _update(self) -> None:
        cfg = self._cfg
        if sum(s[3] for s in self._samples) < cfg.min_attentive_s:
            return  # not enough data in the window: keep what we have (the defaults at the start)
        pitches = [s[1] for s in self._samples]
        yaws = [s[2] for s in self._samples]
        ref_pitch = _clamp(statistics.median(pitches), -cfg.max_reference_pitch_deg, cfg.max_reference_pitch_deg)
        ref_yaw = _clamp(statistics.median(yaws), -cfg.max_reference_yaw_deg, cfg.max_reference_yaw_deg)
        self._reference = (ref_pitch, ref_yaw)
        spread_pitch = _percentile([abs(p - ref_pitch) for p in pitches], cfg.spread_percentile)
        spread_yaw = _percentile([abs(y - ref_yaw) for y in yaws], cfg.spread_percentile)
        self._extents = (
            _clamp(cfg.spread_multiplier * spread_pitch, cfg.min_up_extent_deg, cfg.max_up_extent_deg),
            _clamp(cfg.spread_multiplier * spread_yaw, cfg.min_side_extent_deg, cfg.max_side_extent_deg),
        )


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


def _percentile(values: list[float], percent: float) -> float:
    """Nearest-rank percentile: the smallest value with at least `percent` % of the values at or below it."""
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]
