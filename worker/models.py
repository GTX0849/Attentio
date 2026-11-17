"""Download the two official MediaPipe models into the git-ignored models/ folder.

Usage: python -m worker.models download

Only these two files are ever fetched, from Google's public MediaPipe bucket.
Files that already exist are left alone.
"""

import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from shared.config import PROJECT_ROOT

MODELS_DIR = PROJECT_ROOT / "models"
_BUCKET = "https://storage.googleapis.com/mediapipe-models"

# file name -> official URL (float16, latest)
MODELS: dict[str, str] = {
    "face_landmarker.task": f"{_BUCKET}/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    "pose_landmarker_lite.task": f"{_BUCKET}/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
}


def _fetch(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, open(dest, "wb") as out:  # noqa: S310
        while chunk := response.read(1 << 20):
            out.write(chunk)


def download_missing(
    directory: Path = MODELS_DIR,
    fetch: Callable[[str, Path], None] = _fetch,
) -> list[str]:
    """Fetch each model that is not in `directory` yet; returns the names downloaded."""
    directory.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    for name, url in MODELS.items():
        target = directory / name
        if target.exists():
            continue
        partial = target.with_name(target.name + ".part")
        fetch(url, partial)
        partial.replace(target)  # a half-finished download never looks like a model
        downloaded.append(name)
    return downloaded


def main(argv: list[str]) -> int:
    if argv != ["download"]:
        print("Usage: python -m worker.models download")
        return 2
    downloaded = download_missing()
    for name in MODELS:
        status = "downloaded" if name in downloaded else "already present"
        print(f"{MODELS_DIR / name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
