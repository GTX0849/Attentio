"""Tune mode: labelled recording during test sessions (`python -m worker ... --tune`).

FOR TEST SESSIONS ONLY. The person testing presses a number key to say what they are doing, and
the worker saves the matching head angles so the front-region limits can be tuned later from real
evidence. Do not use it in real lectures with students who have not agreed to it.

Labels (number keys in the preview window; 0 is the default at the start):

    0 nothing, 1 watching the teacher, 2 taking notes, 3 phone in lap,
    4 talking to a neighbour, 5 eyes closed

File format, version 1 (read by the tuning analysis, CLA-21)
------------------------------------------------------------
Each run writes its own file: `.spool/tuning/<camera_id>/<UTC timestamp>.csv`, for example
`.spool/tuning/CAM1/20261120T093015Z.csv` (`.spool/` is git-ignored; `camera_id` is made safe for a
path: anything other than letters, digits, `.`, `_` and `-` becomes `_`). UTF-8, `\n` line ends:

    line 1   `# classpulse tune v1 camera=<camera id>`   (comment; skip lines starting with `#`)
    line 2   the header: t,label,pitch,yaw,ref_pitch,ref_yaw,rel_pitch,rel_yaw,eyes_closed,face_size_pct,state
    line 3+  one row per scoring tick

Columns, in order (degrees where an angle):

    t              epoch seconds (3 decimals) of the scoring tick
    label          0 to 5, the label in force at that tick
    pitch, yaw     the head angles relative to calibration (positive pitch = looking down,
                   positive yaw = turned to the image right), as the state rules see them
    ref_pitch,
    ref_yaw        the learned front-region reference (centre) at that moment, same frame as pitch/yaw
    rel_pitch,
    rel_yaw        pitch - ref_pitch and yaw - ref_yaw: how far from the learned front
    eyes_closed    0 or 1
    face_size_pct  the scored face's width as a share of the picture width, 0 to 100
    state          the attention state at that tick: attentive, neutral, distracted, not_visible,
                   or `starting` before the first state is known

Rows: about 10 a second (`worker.target_fps`), and only while a face is locked and in view and
calibration has finished. Ticks during calibration, with no face, or while the locked face is out
of view (so there is no face size) are skipped, so a file can have gaps. Nothing else is saved: no
names, no student id, no images, nothing about other faces. Records sent to the backend are not
affected. Nothing is written without `--tune`.

Writing is buffered and flushed about once a second and on close; it is done from the scoring
thread (a few small appends a second), so it never blocks the preview. A disk error turns
recording off with one log line instead of stopping the worker.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from shared.config import PROJECT_ROOT
from worker.concurrency import LabelHolder, Status

log = logging.getLogger("worker.tuning")

DEFAULT_TUNING_DIR = PROJECT_ROOT / ".spool" / "tuning"
FORMAT_VERSION = 1
COLUMNS = (
    "t",
    "label",
    "pitch",
    "yaw",
    "ref_pitch",
    "ref_yaw",
    "rel_pitch",
    "rel_yaw",
    "eyes_closed",
    "face_size_pct",
    "state",
)
FLUSH_EVERY_S = 1.0

NO_LABEL = 0
LABEL_NAMES = {
    0: "No label",
    1: "Watching the teacher",
    2: "Taking notes",
    3: "Phone in lap",
    4: "Talking to a neighbour",
    5: "Eyes closed",
}
KEY_HINTS = "1 teacher - 2 notes - 3 phone - 4 neighbour - 5 eyes closed - 0 none"


def label_for_key(key: int) -> int | None:
    """The label a pressed key stands for (the digits 0 to 5), or None for any other key."""
    for label in LABEL_NAMES:
        if key == ord(str(label)):
            return label
    return None


def handle_tune_key(key: int, labels: LabelHolder | None) -> bool:
    """Apply a key press. True if it was a label key. With no holder (tune mode off) nothing happens."""
    if labels is None:
        return False
    label = label_for_key(key)
    if label is None:
        return False
    labels.set(label)
    return True


def label_text(label: int) -> str:
    """What the preview's tune chip says, e.g. "1 - Watching the teacher"."""
    return f"{label} - {LABEL_NAMES.get(label, 'Unknown')}"


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_path_part(camera_id: str) -> str:
    """`camera_id` as one safe folder name: odd characters become `_`, no leading dots, never empty."""
    cleaned = _UNSAFE.sub("_", camera_id).lstrip(".")
    return cleaned or "camera"


@dataclass(frozen=True)
class TuneSummary:
    """What a finished tune run saved."""

    path: Path | None  # None when tune mode was off
    camera_id: str
    rows_per_label: dict[int, int] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(self.rows_per_label.values())


def format_summary(summary: TuneSummary) -> str:
    """The text printed when a tune run ends: the file and the number of rows per label."""
    lines = [f"Tune file: {summary.path}", f"{summary.total_rows} rows saved:"]
    lines += [f"  {label_text(label)}: {count}" for label, count in summary.rows_per_label.items()]
    return "\n".join(lines)


def on_tune_finished(path: Path, camera_id: str) -> None:
    """The hook the runner calls once the tune file is closed (after the summary is printed).

    CLA-21 puts the tuning analysis here (read `path`, see the format above). For now there is
    nothing to do beyond the summary the runner has already printed.
    """


class TuneRecorder:
    """Writes the tune file. `record` is called from the scoring thread once per tick; it is cheap
    (one buffered line) and flushes about once a second, so it never holds up the preview.

    With `enabled=False` nothing is created or written. `started_at` (epoch seconds, default now)
    names the file.
    """

    def __init__(
        self,
        camera_id: str,
        directory: Path = DEFAULT_TUNING_DIR,
        *,
        enabled: bool = True,
        started_at: float | None = None,
    ) -> None:
        self._camera_id = camera_id
        self._lock = threading.Lock()
        self._counts = {label: 0 for label in LABEL_NAMES}
        self._file: IO[str] | None = None
        self._path: Path | None = None
        self._last_flush = 0.0
        if enabled:
            stamp = datetime.fromtimestamp(time.time() if started_at is None else started_at, UTC)
            self._open(directory / safe_path_part(camera_id), stamp.strftime("%Y%m%dT%H%M%SZ"))
            self._last_flush = stamp.timestamp()

    @property
    def path(self) -> Path | None:
        return self._path

    def _open(self, folder: Path, stamp: str) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        n = 0
        while True:  # two runs in the same second must not share a file
            path = folder / (f"{stamp}.csv" if n == 0 else f"{stamp}-{n}.csv")
            try:
                self._file = path.open("x", encoding="utf-8", newline="\n")
                break
            except FileExistsError:
                n += 1
        self._path = path
        header = " ".join(self._camera_id.split())  # one line, whatever the id contains
        self._file.write(f"# classpulse tune v{FORMAT_VERSION} camera={header}\n")
        self._file.write(",".join(COLUMNS) + "\n")
        self._file.flush()

    def record(self, status: Status, label: int, now: float) -> bool:
        """Save this tick if a face is locked and in view and calibration is over. True if a row was written."""
        reading, diagnostics, reference = status.reading, status.diagnostics, status.reference
        if (
            status.calibration_left_s is not None
            or not reading.face_present
            or diagnostics is None
            or diagnostics.framing is None
            or reference is None
        ):
            return False
        state = "starting" if status.state is None else status.state.value
        row = (
            f"{now:.3f},{label},{reading.pitch_deg:.3f},{reading.yaw_deg:.3f},"
            f"{reference[0]:.3f},{reference[1]:.3f},"
            f"{reading.pitch_deg - reference[0]:.3f},{reading.yaw_deg - reference[1]:.3f},"
            f"{int(reading.eyes_closed)},{diagnostics.framing.size_pct:.3f},{state}\n"
        )
        with self._lock:
            if self._file is None:
                return False
            try:
                self._file.write(row)
                if now - self._last_flush >= FLUSH_EVERY_S:
                    self._file.flush()
                    self._last_flush = now
            except OSError:
                log.exception("Could not write the tune file; tune recording is off")
                self._abandon()
                return False
            self._counts[label] = self._counts.get(label, 0) + 1
            return True

    def close(self) -> TuneSummary:
        """Flush and close the file (safe to call twice), and say what was saved."""
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    log.exception("Could not close the tune file cleanly")
                self._file = None
            return TuneSummary(self._path, self._camera_id, dict(self._counts))

    def _abandon(self) -> None:
        try:
            if self._file is not None:
                self._file.close()
        except OSError:
            pass
        self._file = None
