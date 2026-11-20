"""Per-camera auto-tuning: read a camera's labelled tune files, work out its limits, write them down.

    python -m worker.autotune CAM1              # tune camera CAM1 and write config/tuned/CAM1.yaml (+ chart)
    python -m worker.autotune CAM1 --dry-run    # show the suggested limits, write nothing

It also runs by itself at the end of a `python -m worker ... --tune` run (see `on_tune_finished`).

What it reads: ALL the tune CSV files of the camera in `.spool/tuning/<camera>/` (format in
worker/tuning.py), not just the latest run. To leave a run out, delete or move its file. A file
that cannot be read is skipped with a warning. Rows labelled 0 are ignored. Each file is one
"run" (one `--tune` session); the number of people is not recorded, so runs are reported, not people.

What it does: with enough data (the minimums are in `tuning:` of config/classpulse.yaml) it works
out the limits (worker/limits.py), writes `config/tuned/<camera>.yaml` (worker/tuned.py), keeps the
last few earlier versions, draws `config/tuned/<camera>.html` and prints a summary. With too little
data it changes nothing and says what is missing.

ONLY labelled tuning ever writes these files. The limits the worker learns during a lecture
(worker/front_region.py) stay in memory and never reach `config/tuned/`.
"""

import argparse
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import altair as alt
import yaml

from shared.config import CONFIG_PATH
from worker.limits import (
    DATA_LABELS,
    CurrentLimits,
    TunedLimits,
    TuneRow,
    TuningConfig,
    compute_limits,
    label_seconds,
    missing_data,
)
from worker.states import StateThresholds
from worker.tuned import TUNED_DIR, chart_path, safe_path_part, write_tuned
from worker.tuning import COLUMNS, DEFAULT_TUNING_DIR, FORMAT_VERSION, LABEL_NAMES

log = logging.getLogger("worker.autotune")

MAX_CHART_POINTS = 6000  # the chart shows at most this many rows (evenly thinned), so the page stays small
_MAGIC = f"# classpulse tune v{FORMAT_VERSION} camera="
_COLUMN = {name: i for i, name in enumerate(COLUMNS)}


class TuneFileError(ValueError):
    """A tune file that cannot be used."""


@dataclass(frozen=True)
class Run:
    """One tune file: one `--tune` session."""

    path: Path
    rows: list[TuneRow]


@dataclass(frozen=True)
class AutotuneResult:
    camera_id: str
    runs: int  # runs that had rows labelled 1, 2 or 3
    skipped: list[str]  # why each unreadable file was skipped
    seconds: dict[int, float]  # recorded seconds per label 1, 2, 3
    missing: list[str]  # what more data is needed; empty when there is enough
    previous: CurrentLimits
    limits: TunedLimits | None  # None when data is missing
    written: Path | None  # the tuned file, None for a dry run or too little data
    chart: Path | None
    text: str  # the summary to print


def read_tune_file(path: Path) -> list[TuneRow]:
    """The rows of one tune file. Raises TuneFileError when it is not a usable v1 tune file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise TuneFileError(f"cannot read it ({e})") from e
    if not lines or not lines[0].startswith("# classpulse tune"):
        raise TuneFileError("it does not start with a '# classpulse tune' line")
    if not lines[0].startswith(_MAGIC):
        raise TuneFileError(f"unknown format ({lines[0][:40]!r}); this tool reads v{FORMAT_VERSION}")
    body = [line for line in lines[1:] if line.strip() and not line.startswith("#")]
    if not body or body[0].strip() != ",".join(COLUMNS):
        raise TuneFileError("the header line is missing or not the v1 columns")
    rows: list[TuneRow] = []
    for number, line in enumerate(body[1:], start=1):
        fields = line.split(",")
        try:
            if len(fields) != len(COLUMNS):
                raise ValueError(f"{len(fields)} fields, expected {len(COLUMNS)}")
            row = TuneRow(
                t=float(fields[_COLUMN["t"]]),
                label=int(fields[_COLUMN["label"]]),
                rel_pitch=float(fields[_COLUMN["rel_pitch"]]),
                rel_yaw=float(fields[_COLUMN["rel_yaw"]]),
            )
            if not all(math.isfinite(x) for x in (row.t, row.rel_pitch, row.rel_yaw)) or row.label not in LABEL_NAMES:
                raise ValueError("a value is not a usable number or label")
        except ValueError as e:
            raise TuneFileError(f"data row {number} is not valid ({e})") from e
        rows.append(row)
    return rows


def _camera_in(path: Path) -> str:
    first = path.read_text(encoding="utf-8").split("\n", 1)[0]
    return first[len(_MAGIC) :].strip()


def read_camera_runs(camera_id: str, tuning_dir: Path = DEFAULT_TUNING_DIR) -> tuple[list[Run], list[str]]:
    """Every readable tune file of the camera, oldest first, and a line for each file skipped (also logged)."""
    folder = tuning_dir / safe_path_part(camera_id)
    runs: list[Run] = []
    skipped: list[str] = []
    for path in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
        try:
            rows = read_tune_file(path)
            if _camera_in(path) != " ".join(camera_id.split()):
                raise TuneFileError(f"it belongs to camera {_camera_in(path)!r}")
        except TuneFileError as e:
            message = f"skipped {path.name}: {e}"
            log.warning("Tuning: %s", message)
            skipped.append(message)
            continue
        runs.append(Run(path, rows))
    return runs, skipped


def auto_tune(
    camera_id: str,
    *,
    tuning_dir: Path = DEFAULT_TUNING_DIR,
    tuned_dir: Path = TUNED_DIR,
    raw_config: dict[str, Any] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> AutotuneResult:
    """Tune one camera from all its tune files. Writes the tuned file and chart unless `dry_run` or data is missing."""
    raw = raw_config if raw_config is not None else load_raw_config()
    cfg = TuningConfig.from_config(raw)
    moment = now or datetime.now(UTC)
    runs, skipped = read_camera_runs(camera_id, tuning_dir)
    seconds = label_seconds([run.rows for run in runs], cfg.max_gap_s)
    used = [run for run in runs if any(r.label in DATA_LABELS for r in run.rows)]
    thresholds = StateThresholds.from_config(raw, camera_id=camera_id, tuned_dir=tuned_dir)
    previous = CurrentLimits(
        thresholds.pitch_attentive_min_deg, thresholds.yaw_max_deg, thresholds.pitch_neutral_max_deg
    )
    missing = missing_data(seconds, cfg)
    header = _header(camera_id, len(used), skipped, seconds)
    if missing:
        text = "\n".join(
            [
                *header,
                "Not enough labelled data yet, so nothing was changed:",
                *[f"  {m}" for m in missing],
                "Record more with `python -m worker --student ... --camera ... --tune`, then run this again.",
            ]
        )
        return AutotuneResult(camera_id, len(used), skipped, seconds, missing, previous, None, None, None, text)

    all_rows = [row for run in runs for row in run.rows]
    limits = compute_limits(all_rows, previous, cfg)
    written: Path | None = None
    chart: Path | None = None
    if not dry_run:
        written = write_tuned(
            camera_id,
            {
                "pitch_attentive_min_deg": round(limits.pitch_attentive_min_deg, 1),
                "yaw_max_deg": round(limits.yaw_max_deg, 1),
                "pitch_neutral_max_deg": round(limits.pitch_neutral_max_deg, 1),
            },
            _metadata(used, skipped, seconds, limits, previous, cfg),
            now=moment,
            directory=tuned_dir,
            keep=cfg.history_keep,
        )
        chart = chart_path(camera_id, tuned_dir)
        build_chart(all_rows, _rounded(limits)).save(chart)
    text = "\n".join([*header, *_table(previous, limits, cfg), *_footer(limits, cfg, written, chart, dry_run)])
    return AutotuneResult(camera_id, len(used), skipped, seconds, [], previous, limits, written, chart, text)


def _rounded(limits: TunedLimits) -> TunedLimits:
    return replace(
        limits,
        pitch_attentive_min_deg=round(limits.pitch_attentive_min_deg, 1),
        yaw_max_deg=round(limits.yaw_max_deg, 1),
        pitch_neutral_max_deg=round(limits.pitch_neutral_max_deg, 1),
    )


def _key(label: int) -> str:
    return DATA_LABELS[label].replace(" ", "_")


def _metadata(
    used: Sequence[Run],
    skipped: Sequence[str],
    seconds: dict[int, float],
    limits: TunedLimits,
    previous: CurrentLimits,
    cfg: TuningConfig,
) -> dict[str, Any]:
    return {
        "runs": len(used),
        "runs_note": "a run is one --tune session; the number of people is not recorded",
        "files": [run.path.name for run in used],
        "skipped_files": len(skipped),
        "minutes_per_label": {_key(label): round(seconds[label] / 60, 2) for label in DATA_LABELS},
        "percentile": cfg.percentile,
        "margin_deg": cfg.margin_deg,
        "up_percentile_deg": None if limits.up_percentile_deg is None else round(limits.up_percentile_deg, 2),
        "side_percentile_deg": None if limits.side_percentile_deg is None else round(limits.side_percentile_deg, 2),
        "separation_accuracy": round(limits.separation_accuracy, 3),
        "notes_phone_boundary_kept": limits.boundary_kept,
        "previous": {
            "pitch_attentive_min_deg": previous.pitch_attentive_min_deg,
            "yaw_max_deg": previous.yaw_max_deg,
            "pitch_neutral_max_deg": previous.pitch_neutral_max_deg,
        },
    }


def _header(camera_id: str, runs: int, skipped: Sequence[str], seconds: dict[int, float]) -> list[str]:
    minutes = ", ".join(f"{DATA_LABELS[label]} {seconds[label] / 60:.1f} min" for label in DATA_LABELS)
    lines = [
        f"Auto-tune for camera {camera_id}",
        f"Used {runs} {'run' if runs == 1 else 'runs'} (a run is one --tune session; the number of people is not recorded, so this counts runs, not people)",
        f"Recorded time used: {minutes}",
    ]
    lines += [f"Warning: {message}" for message in skipped]
    return lines


def _table(previous: CurrentLimits, limits: TunedLimits, cfg: TuningConfig) -> list[str]:
    def row(name: str, before: float, after: float) -> str:
        return f"{name:<28}{before:>9.1f}{after:>9.1f}"

    return [
        f"{'':<28}{'previous':>9}{'new':>9}",
        row("Up limit (deg)", -previous.pitch_attentive_min_deg, -limits.pitch_attentive_min_deg),
        row("Side limit (deg)", previous.yaw_max_deg, limits.yaw_max_deg),
        row("Notes/phone boundary (deg)", previous.pitch_neutral_max_deg, limits.pitch_neutral_max_deg),
    ]


def _footer(limits: TunedLimits, cfg: TuningConfig, written: Path | None, chart: Path | None, dry_run: bool) -> list[str]:
    lines = [
        f"Up and side limits: the {cfg.percentile:g}th percentile of the watching-the-teacher rows"
        f" ({limits.up_percentile_deg:.1f} up, {limits.side_percentile_deg:.1f} to the side) plus {cfg.margin_deg:g} degrees."
        if limits.up_percentile_deg is not None and limits.side_percentile_deg is not None
        else "Up and side limits unchanged.",
    ]
    if limits.boundary_kept:
        lines.append(
            f"Notes vs phone: the two overlap too much to separate (best balanced accuracy {limits.separation_accuracy:.0%},"
            f" needs {cfg.min_separation:.0%}), so the current boundary was kept."
        )
    else:
        lines.append(f"Notes vs phone separation accuracy: {limits.separation_accuracy:.0%} (balanced accuracy at the boundary).")
    if dry_run:
        lines.append("Dry run: nothing was written.")
    else:
        lines.append(f"Written: {written}")
        lines.append(f"Chart: {chart}")
    return lines


def build_chart(rows: Sequence[TuneRow], limits: TunedLimits) -> alt.LayerChart:
    """rel_pitch against rel_yaw for every labelled row, with the chosen limits drawn as rule lines.

    Pitch runs upward on the page (the y axis is reversed), so looking up is up. At most
    `MAX_CHART_POINTS` rows are drawn, evenly thinned.
    """
    labelled = [r for r in rows if r.label != 0]
    stride = max(1, math.ceil(len(labelled) / MAX_CHART_POINTS))
    points = [
        {"rel_yaw": round(r.rel_yaw, 2), "rel_pitch": round(r.rel_pitch, 2), "label": LABEL_NAMES[r.label]}
        for r in labelled[::stride]
    ]
    up, side, boundary = -limits.pitch_attentive_min_deg, limits.yaw_max_deg, limits.pitch_neutral_max_deg
    label_names = [LABEL_NAMES[i] for i in range(1, 6)]
    scatter = (
        alt.Chart(alt.Data(values=points))
        .mark_circle(size=18, opacity=0.45)
        .encode(
            x=alt.X("rel_yaw:Q", title="Sideways turn from the reference (degrees, right is positive)"),
            y=alt.Y("rel_pitch:Q", title="Pitch from the reference (degrees, down is positive)", scale=alt.Scale(reverse=True)),
            color=alt.Color("label:N", title="Label", scale=alt.Scale(domain=label_names)),
            tooltip=["label:N", "rel_pitch:Q", "rel_yaw:Q"],
        )
    )
    horizontal = alt.Data(
        values=[
            {"rel_pitch": -up, "limit": f"Up limit {up:.1f}"},
            {"rel_pitch": boundary, "limit": f"Notes/phone boundary {boundary:.1f}"},
        ]
    )
    vertical = alt.Data(
        values=[
            {"rel_yaw": -side, "limit": f"Side limit {side:.1f}"},
            {"rel_yaw": side, "limit": f"Side limit {side:.1f}"},
        ]
    )
    horizontal_rules = (
        alt.Chart(horizontal)
        .mark_rule(strokeDash=[6, 4], strokeWidth=2, color="#222")
        .encode(y="rel_pitch:Q", tooltip="limit:N")
    )
    horizontal_text = (
        alt.Chart(horizontal)
        .mark_text(align="left", dx=4, dy=-4, color="#222")
        .encode(y="rel_pitch:Q", text="limit:N", x=alt.value(4))
    )
    vertical_rules = (
        alt.Chart(vertical)
        .mark_rule(strokeDash=[6, 4], strokeWidth=2, color="#222")
        .encode(x="rel_yaw:Q", tooltip="limit:N")
    )
    chart: alt.LayerChart = (scatter + horizontal_rules + horizontal_text + vertical_rules).properties(
        width=640, height=480, title="Tune sessions: head angles by label, with the limits chosen"
    )
    return chart


def load_raw_config() -> dict[str, Any]:
    """The parsed config/classpulse.yaml (read directly: this tool needs nothing from .env)."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return raw


def on_tune_finished(
    path: Path,
    camera_id: str,
    *,
    tuning_dir: Path | None = None,
    tuned_dir: Path = TUNED_DIR,
    raw_config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Called when a `--tune` run ends: tune the camera from all its files and print the summary.

    `path` is the file just written (`<tuning dir>/<camera>/<stamp>.csv`), so the tuning folder is
    two levels up unless given. A failure is logged and never stops the worker from shutting down.
    """
    try:
        folder = tuning_dir if tuning_dir is not None else path.parent.parent
        result = auto_tune(camera_id, tuning_dir=folder, tuned_dir=tuned_dir, raw_config=raw_config, now=now)
        print(result.text, flush=True)
    except Exception:
        log.exception("Auto-tuning failed for %s; nothing was changed", camera_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m worker.autotune", description="Tune a camera's attention limits from its labelled tune files"
    )
    parser.add_argument("camera_id", help="camera id, e.g. CAM1")
    parser.add_argument("--dry-run", action="store_true", help="show the suggested limits, write nothing")
    parser.add_argument("--tuning-dir", type=Path, default=DEFAULT_TUNING_DIR, help="where the tune CSV files are")
    parser.add_argument("--tuned-dir", type=Path, default=TUNED_DIR, help="where the tuned files go")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = auto_tune(
        args.camera_id, tuning_dir=args.tuning_dir, tuned_dir=args.tuned_dir, dry_run=args.dry_run
    )
    print(result.text)
    return 1 if result.missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
