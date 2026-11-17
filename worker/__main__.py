"""Usage: python -m worker --student STU001 --camera CAM1"""

import argparse
import logging
from pathlib import Path

from shared.config import load_config
from worker.runner import DEFAULT_SPOOL, run


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m worker", description="ClassPulse webcam worker")
    parser.add_argument("--student", required=True, help="student register number, e.g. STU001")
    parser.add_argument("--camera", required=True, help="camera id, e.g. CAM1")
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV webcam index (default 0)")
    parser.add_argument("--no-preview", action="store_true", help="do not show the preview window")
    parser.add_argument("--spool", type=Path, default=DEFAULT_SPOOL, help="unsent-record spool file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run(
        student=args.student,
        camera=args.camera,
        config=load_config(),
        spool_path=args.spool,
        preview=not args.no_preview,
        camera_index=args.camera_index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
