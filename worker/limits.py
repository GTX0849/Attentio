"""The tuning analysis: labelled head angles in, a camera's attention limits out. Pure; no files.

Everything here works on `rel_pitch` and `rel_yaw`, the angles relative to the learned front
reference, because those are what the state rules compare with the limits. Positive pitch means
looking down. The file reading and writing is in worker/autotune.py and worker/tuned.py.

How each limit is worked out
* Up limit: upward deviation is `max(0, -rel_pitch)` for every row labelled "watching the teacher"
  (a row looking level or down deviates by 0). The limit is the `percentile`-th percentile (nearest
  rank, 95 by default) of those deviations plus `margin_deg`, kept between `min_up_deg` and
  `max_up_deg`. It is written as `pitch_attentive_min_deg = -limit`.
* Side limit: the same with `|rel_yaw|`, written as `yaw_max_deg`.
* Note-taking / phone boundary: the `rel_pitch` above which a row is called "phone in lap" instead
  of "taking notes". Candidates are the midpoints between neighbouring recorded values that lie
  between the two groups' medians; the one with the best balanced accuracy (the mean of the share of
  notes rows at or below it and the share of phone rows above it) wins, the middle one when several
  tie. It is written as `pitch_neutral_max_deg`, kept between `min_neutral_max_deg` and
  `max_neutral_max_deg`. When even the best boundary scores under `min_separation` the groups overlap
  too much to trust, and the current value is kept.

`pitch_attentive_max_deg` and the time rules are never tuned.
"""

import math
import statistics
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

WATCHING, NOTES, PHONE = 1, 2, 3  # the tune labels the analysis uses (see worker/tuning.py)


@dataclass(frozen=True)
class TuneRow:
    """One scoring tick of a tune file: when, what the person said they were doing, and the angles."""

    t: float
    label: int
    rel_pitch: float
    rel_yaw: float


@dataclass(frozen=True)
class CurrentLimits:
    """The limits in force before tuning (the `states:` section, or an earlier tuned file)."""

    pitch_attentive_min_deg: float
    yaw_max_deg: float
    pitch_neutral_max_deg: float


@dataclass(frozen=True)
class TuningConfig:
    min_watching_s: float
    min_notes_s: float
    min_phone_s: float
    max_gap_s: float
    percentile: float
    margin_deg: float
    min_up_deg: float
    max_up_deg: float
    min_side_deg: float
    max_side_deg: float
    min_neutral_max_deg: float
    max_neutral_max_deg: float
    min_separation: float
    history_keep: int

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> "TuningConfig":
        """Read the top-level `tuning:` section of config/attentio.yaml."""
        section = raw["tuning"]
        values: dict[str, Any] = {name: float(section[name]) for name in cls.__dataclass_fields__}
        values["history_keep"] = int(section["history_keep"])
        return cls(**values)


@dataclass(frozen=True)
class TunedLimits:
    """The result of the analysis."""

    pitch_attentive_min_deg: float  # -(up limit)
    yaw_max_deg: float  # the side limit
    pitch_neutral_max_deg: float  # the note/phone boundary (the current one when `boundary_kept`)
    up_percentile_deg: float | None  # the percentile of upward deviation, before the margin (None: no watching rows)
    side_percentile_deg: float | None  # the percentile of |rel_yaw|, before the margin
    separation_accuracy: float  # balanced accuracy of the note/phone boundary, 0.5 to 1.0
    boundary_kept: bool  # True when the groups overlapped too much and the current boundary was kept


def compute_limits(rows: Sequence[TuneRow], current: CurrentLimits, cfg: TuningConfig) -> TunedLimits:
    """The tuned limits from labelled rows (all runs together). Rows labelled 0 and 4 and 5 are ignored here."""
    watching = [r for r in rows if r.label == WATCHING]
    up: float | None = None  # no watching rows: nothing to learn the up and side limits from, keep them
    side: float | None = None
    new_up, new_side = -current.pitch_attentive_min_deg, current.yaw_max_deg
    if watching:
        up = percentile([max(0.0, -r.rel_pitch) for r in watching], cfg.percentile)
        side = percentile([abs(r.rel_yaw) for r in watching], cfg.percentile)
        new_up = _clamp(up + cfg.margin_deg, cfg.min_up_deg, cfg.max_up_deg)
        new_side = _clamp(side + cfg.margin_deg, cfg.min_side_deg, cfg.max_side_deg)
    boundary = _best_boundary(
        [r.rel_pitch for r in rows if r.label == NOTES], [r.rel_pitch for r in rows if r.label == PHONE]
    )
    accuracy = 0.5 if boundary is None else boundary[1]
    kept = boundary is None or accuracy < cfg.min_separation
    chosen = current.pitch_neutral_max_deg
    if boundary is not None and not kept:
        chosen = _clamp(boundary[0], cfg.min_neutral_max_deg, cfg.max_neutral_max_deg)
    return TunedLimits(
        pitch_attentive_min_deg=-new_up,
        yaw_max_deg=new_side,
        pitch_neutral_max_deg=chosen,
        up_percentile_deg=up,
        side_percentile_deg=side,
        separation_accuracy=accuracy,
        boundary_kept=kept,
    )


def _best_boundary(notes: Sequence[float], phone: Sequence[float]) -> tuple[float, float] | None:
    """(boundary, balanced accuracy) for the notes/phone split, or None when there is nothing to split.

    A row is "phone" when its pitch is above the boundary. None when either group is empty or the
    phone median is not further down than the notes median.
    """
    if not notes or not phone:
        return None
    low, high = statistics.median(notes), statistics.median(phone)
    if high <= low:
        return None
    notes_sorted, phone_sorted = sorted(notes), sorted(phone)
    values = sorted(set(notes_sorted) | set(phone_sorted))
    candidates = sorted({low, high} | {mid for a, b in zip(values, values[1:]) if low <= (mid := (a + b) / 2) <= high})

    def accuracy(boundary: float) -> float:
        notes_right = bisect_right(notes_sorted, boundary) / len(notes_sorted)
        phone_right = (len(phone_sorted) - bisect_right(phone_sorted, boundary)) / len(phone_sorted)
        return (notes_right + phone_right) / 2

    scores = [accuracy(c) for c in candidates]
    best = max(scores)
    tied = [c for c, score in zip(candidates, scores) if score >= best - 1e-12]
    return tied[len(tied) // 2], best


DATA_LABELS = {WATCHING: "watching the teacher", NOTES: "taking notes", PHONE: "phone in lap"}


def label_seconds(runs: Sequence[Sequence[TuneRow]], max_gap_s: float) -> dict[int, float]:
    """Seconds of recorded time per label (1, 2 and 3), from the row timestamps, run by run.

    Each row lasts until the next row of its own run. A gap longer than `max_gap_s` (the face was
    lost, or the worker was stopped) counts for nothing, and the last row of a run lasts for nothing.
    """
    seconds = {label: 0.0 for label in DATA_LABELS}
    for run in runs:
        for row, following in zip(run, run[1:]):
            step = following.t - row.t
            if row.label in seconds and 0.0 < step <= max_gap_s:
                seconds[row.label] += step
    return seconds


def missing_data(seconds: dict[int, float], cfg: TuningConfig) -> list[str]:
    """What is still needed before tuning can change anything, e.g. "needs 22 s more of taking notes". Empty when enough."""
    needed = {WATCHING: cfg.min_watching_s, NOTES: cfg.min_notes_s, PHONE: cfg.min_phone_s}
    missing: list[str] = []
    for label, name in DATA_LABELS.items():
        short = needed[label] - seconds.get(label, 0.0)
        if short > 1e-9:
            missing.append(f"needs {math.ceil(short - 1e-9)} s more of {name}")
    return missing


def percentile(values: Sequence[float], percent: float) -> float:
    """Nearest-rank percentile: the smallest value with at least `percent` % of the values at or below it."""
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))

