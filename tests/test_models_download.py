"""Model download logic, with a fake fetch (no network) and a smoke test on the real models."""

from pathlib import Path

import numpy as np
import pytest

from worker.models import MODELS, download_missing


def test_only_missing_models_are_fetched(tmp_path: Path) -> None:
    (tmp_path / "face_landmarker.task").write_bytes(b"already here")
    fetched: list[str] = []

    def fake_fetch(url: str, dest: Path) -> None:
        fetched.append(url)
        dest.write_bytes(b"model")

    assert download_missing(tmp_path, fake_fetch) == ["pose_landmarker_lite.task"]
    assert [u.rsplit("/", 1)[-1] for u in fetched] == ["pose_landmarker_lite.task"]
    assert (tmp_path / "face_landmarker.task").read_bytes() == b"already here"
    assert (tmp_path / "pose_landmarker_lite.task").read_bytes() == b"model"
    assert not list(tmp_path.glob("*.part"))
    assert download_missing(tmp_path, fake_fetch) == []  # nothing left to fetch


def test_the_urls_are_the_official_mediapipe_bucket() -> None:
    assert all(u.startswith("https://storage.googleapis.com/mediapipe-models/") for u in MODELS.values())
    assert set(MODELS) == {"face_landmarker.task", "pose_landmarker_lite.task"}


def test_the_real_models_run_on_a_blank_frame() -> None:
    from shared.config import load_config
    from worker.mediapipe_source import MediaPipeReadingSource, resolve_model
    from worker.perception import PerceptionSettings

    settings = PerceptionSettings.from_config(load_config().raw)
    if not (resolve_model(settings.face_model).exists() and resolve_model(settings.pose_model).exists()):
        pytest.skip("models/ is missing; run: python -m worker.models download")
    source = MediaPipeReadingSource(settings)
    try:
        r = source.read(np.zeros((240, 320, 3), np.uint8), 1000.0)
    finally:
        source.close()
    assert (r.face_present, r.shoulders_present) == (False, False)
