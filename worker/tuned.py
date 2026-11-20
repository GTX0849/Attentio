"""A camera's tuned limits: the small YAML file auto-tuning writes and the worker reads at start-up.

Layout (the folder is committed and shared between machines on purpose, so it is NOT git-ignored):

    config/tuned/<safe camera id>.yaml           the live file; delete it to go back to the defaults
    config/tuned/<safe camera id>.html           the chart made with it (worker/autotune.py)
    config/tuned/history/<safe camera id>/<stamp>.yaml
                                                 the last few versions it replaced; copy one back to undo a tune

Only labelled tuning (`python -m worker.autotune`, or the end of a `--tune` run) writes these files.
Lecture learning (worker/front_region.py) never does: it only ever lives in memory.

The file holds `states:` (the three tuned values, same names and meaning as in config/classpulse.yaml)
and `metadata:` (what the values were made from, for people to read). Only `states:` is used when loading.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from shared.config import PROJECT_ROOT

log = logging.getLogger("worker.tuned")

TUNED_DIR = PROJECT_ROOT / "config" / "tuned"
FILE_VERSION = 1
TUNED_KEYS = ("pitch_attentive_min_deg", "yaw_max_deg", "pitch_neutral_max_deg")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_path_part(camera_id: str) -> str:
    """`camera_id` as one safe file or folder name: odd characters become `_`, no leading dots, never empty."""
    cleaned = _UNSAFE.sub("_", camera_id).lstrip(".")
    return cleaned or "camera"


def tuned_path(camera_id: str, directory: Path = TUNED_DIR) -> Path:
    return directory / f"{safe_path_part(camera_id)}.yaml"


def chart_path(camera_id: str, directory: Path = TUNED_DIR) -> Path:
    return directory / f"{safe_path_part(camera_id)}.html"


@dataclass(frozen=True)
class TunedFile:
    """A camera's tuned file, read back."""

    path: Path
    camera_id: str
    created: datetime
    values: dict[str, float]  # the three tuned limits, by their `states:` names
    metadata: dict[str, Any]


def load_tuned(camera_id: str, directory: Path = TUNED_DIR) -> TunedFile | None:
    """The camera's tuned file, or None when there is none (or it cannot be read: one warning, defaults are used)."""
    path = tuned_path(camera_id, directory)
    if not path.is_file():
        return None
    try:
        return _read(path, camera_id)
    except (OSError, ValueError, yaml.YAMLError) as e:
        log.warning("Ignoring the tuned limits in %s (%s); using the default limits", path, e)
        return None


def _read(path: Path, camera_id: str) -> TunedFile:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("states"), dict):
        raise ValueError("no `states:` section")
    states = data["states"]
    values: dict[str, float] = {}
    for key in TUNED_KEYS:
        value = states.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"`{key}` is not a number")
        values[key] = float(value)
    metadata = data.get("metadata")
    return TunedFile(
        path=path,
        camera_id=str(data.get("camera_id", camera_id)),
        created=_parse_time(data.get("created")),
        values=values,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _parse_time(raw: object) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    raise ValueError("no `created` time")


def write_tuned(
    camera_id: str,
    values: dict[str, float],
    metadata: dict[str, Any],
    *,
    now: datetime,
    directory: Path = TUNED_DIR,
    keep: int = 5,
) -> Path:
    """Write the camera's tuned file. The version it replaces goes to `history/<camera>/`, newest `keep` kept."""
    path = tuned_path(camera_id, directory)
    directory.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        _archive(path, camera_id, directory, now, keep)
    document = {
        "version": FILE_VERSION,
        "camera_id": camera_id,
        "created": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "states": {key: values[key] for key in TUNED_KEYS},
        "metadata": metadata,
    }
    header = (
        f"# ClassPulse tuned limits for camera {' '.join(camera_id.split())}, made by python -m worker.autotune.\n"
        "# Delete this file to go back to the defaults in config/classpulse.yaml. Earlier versions are in history/.\n"
    )
    path.write_text(header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _archive(path: Path, camera_id: str, directory: Path, now: datetime, keep: int) -> None:
    """Move the live file into the history folder, named after when it was made, then trim the folder."""
    history = directory / "history" / safe_path_part(camera_id)
    history.mkdir(parents=True, exist_ok=True)
    try:
        made = _read(path, camera_id).created
    except (OSError, ValueError, yaml.YAMLError):
        made = now  # unreadable: name it after the replacement time
    stamp = made.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    n = 0
    while True:  # two versions made in the same second must not share a file
        target = history / (f"{stamp}.yaml" if n == 0 else f"{stamp}-{n}.yaml")
        if not target.exists():
            break
        n += 1
    path.replace(target)
    versions = sorted(history.glob("*.yaml"), key=_history_order)
    for old in versions[: max(0, len(versions) - keep)]:
        old.unlink()


def _history_order(path: Path) -> tuple[str, int]:
    stamp, _, suffix = path.stem.partition("-")
    return stamp, int(suffix) if suffix.isdigit() else 0
