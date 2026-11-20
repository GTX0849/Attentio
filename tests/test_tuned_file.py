"""The per-camera tuned file: written with its metadata, versions kept, and loaded for the right camera only."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from shared.config import load_config
from worker.states import StateClassifier, StateThresholds, limits_message
from worker.tuned import load_tuned, tuned_path, write_tuned

RAW = load_config().raw
NOON = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


def values(up: float = -28.0, side: float = 31.0, boundary: float = 41.0) -> dict[str, float]:
    return {"pitch_attentive_min_deg": up, "yaw_max_deg": side, "pitch_neutral_max_deg": boundary}


META = {"runs": 3, "minutes_per_label": {"watching_the_teacher": 2.5, "taking_notes": 1.0, "phone_in_lap": 0.75}}


def test_a_tuned_file_is_written_with_the_values_and_the_metadata_and_read_back(tmp_path: Path) -> None:
    path = write_tuned("CAM1", values(), META, now=NOON, directory=tmp_path, keep=5)
    assert path == tmp_path / "CAM1.yaml" == tuned_path("CAM1", tmp_path)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["states"] == values()
    assert saved["metadata"]["runs"] == 3
    assert saved["metadata"]["minutes_per_label"]["taking_notes"] == 1.0
    assert saved["camera_id"] == "CAM1"
    tuned = load_tuned("CAM1", tmp_path)
    assert tuned is not None
    assert tuned.path == path
    assert tuned.created == NOON
    assert tuned.values == values()
    assert tuned.metadata["runs"] == 3


def test_an_odd_camera_id_gets_a_safe_file_name(tmp_path: Path) -> None:
    path = write_tuned("room 2/cam:1", values(), META, now=NOON, directory=tmp_path, keep=5)
    assert path.parent == tmp_path
    assert path.name == "room_2_cam_1.yaml"
    assert load_tuned("room 2/cam:1", tmp_path) is not None


def test_no_file_means_no_tuned_limits(tmp_path: Path) -> None:
    assert load_tuned("CAM1", tmp_path) is None
    assert load_tuned("CAM1", tmp_path / "does-not-exist") is None


def test_a_camera_only_sees_its_own_file(tmp_path: Path) -> None:
    write_tuned("CAM1", values(side=22.0), META, now=NOON, directory=tmp_path, keep=5)
    assert load_tuned("CAM2", tmp_path) is None
    other = write_tuned("CAM2", values(side=44.0), META, now=NOON, directory=tmp_path, keep=5)
    first, second = load_tuned("CAM1", tmp_path), load_tuned("CAM2", tmp_path)
    assert first is not None and second is not None
    assert (first.values["yaw_max_deg"], second.values["yaw_max_deg"]) == (22.0, 44.0)
    assert other.name == "CAM2.yaml"


def test_a_broken_file_is_ignored_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "CAM1.yaml").write_text("states: [not, a, mapping\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert load_tuned("CAM1", tmp_path) is None
    assert "CAM1.yaml" in caplog.text
    (tmp_path / "CAM1.yaml").write_text("states:\n  yaw_max_deg: wide\n", encoding="utf-8")
    assert load_tuned("CAM1", tmp_path) is None


def test_each_new_tune_keeps_the_version_it_replaces_and_only_the_last_five(tmp_path: Path) -> None:
    for n in range(8):  # versions 0 to 7, an hour apart
        write_tuned("CAM1", values(side=20.0 + n), META, now=NOON + timedelta(hours=n), directory=tmp_path, keep=5)
    history = sorted((tmp_path / "history" / "CAM1").glob("*.yaml"))
    assert len(history) == 5
    kept_sides = sorted(yaml.safe_load(p.read_text(encoding="utf-8"))["states"]["yaw_max_deg"] for p in history)
    assert kept_sides == [22.0, 23.0, 24.0, 25.0, 26.0]  # versions 2 to 6; the newest (7) is the live file
    live = load_tuned("CAM1", tmp_path)
    assert live is not None and live.values["yaw_max_deg"] == 27.0


def test_history_is_per_camera_and_the_first_write_has_nothing_to_keep(tmp_path: Path) -> None:
    write_tuned("CAM1", values(), META, now=NOON, directory=tmp_path, keep=5)
    assert not (tmp_path / "history").exists() or not list((tmp_path / "history").rglob("*.yaml"))
    write_tuned("CAM1", values(side=40.0), META, now=NOON + timedelta(hours=1), directory=tmp_path, keep=5)
    write_tuned("CAM2", values(), META, now=NOON, directory=tmp_path, keep=5)
    assert len(list((tmp_path / "history" / "CAM1").glob("*.yaml"))) == 1
    assert not (tmp_path / "history" / "CAM2").exists()


def test_two_tunes_in_the_same_second_do_not_overwrite_each_others_history(tmp_path: Path) -> None:
    for n in range(4):
        write_tuned("CAM1", values(side=20.0 + n), META, now=NOON, directory=tmp_path, keep=5)
    assert len(list((tmp_path / "history" / "CAM1").glob("*.yaml"))) == 3


def test_a_tuned_camera_overrides_the_default_limits_for_that_camera_only(tmp_path: Path) -> None:
    write_tuned("CAM1", values(up=-26.5, side=29.0, boundary=44.0), META, now=NOON, directory=tmp_path, keep=5)
    defaults = StateThresholds.from_config(RAW)
    tuned = StateThresholds.from_config(RAW, camera_id="CAM1", tuned_dir=tmp_path)
    assert (tuned.pitch_attentive_min_deg, tuned.yaw_max_deg, tuned.pitch_neutral_max_deg) == (-26.5, 29.0, 44.0)
    assert tuned.pitch_attentive_max_deg == defaults.pitch_attentive_max_deg == 15.0  # never tuned
    assert tuned.eyes_closed_grace_s == defaults.eyes_closed_grace_s
    assert tuned.look_down_allowance_s == defaults.look_down_allowance_s
    assert tuned.allowance_refill_s == defaults.allowance_refill_s
    assert tuned.learning == defaults.learning
    assert tuned.tuned is not None and tuned.tuned.path.name == "CAM1.yaml"
    assert defaults.tuned is None


def test_other_cameras_and_a_missing_file_keep_the_defaults(tmp_path: Path) -> None:
    write_tuned("CAM1", values(side=29.0), META, now=NOON, directory=tmp_path, keep=5)
    defaults = StateThresholds.from_config(RAW)
    assert StateThresholds.from_config(RAW, camera_id="CAM2", tuned_dir=tmp_path) == defaults
    assert StateThresholds.from_config(RAW, camera_id="CAM1", tuned_dir=tmp_path / "gone") == defaults
    tuned_path("CAM1", tmp_path).unlink()  # deleting the file reverts to the defaults
    assert StateThresholds.from_config(RAW, camera_id="CAM1", tuned_dir=tmp_path) == defaults


def test_the_front_regions_default_extents_come_from_the_tuned_up_and_side_limits(tmp_path: Path) -> None:
    write_tuned("CAM1", values(up=-26.5, side=29.0), META, now=NOON, directory=tmp_path, keep=5)
    tuned = StateClassifier(StateThresholds.from_config(RAW, camera_id="CAM1", tuned_dir=tmp_path))
    assert tuned.extents() == (26.5, 29.0)
    assert StateClassifier(StateThresholds.from_config(RAW)).extents() == (30.0, 35.0)


def test_the_start_up_message_says_the_limits_are_tuned_with_the_file_the_date_and_the_values(tmp_path: Path) -> None:
    write_tuned("CAM1", values(up=-26.5, side=29.0, boundary=44.0), META, now=NOON, directory=tmp_path, keep=5)
    message = limits_message(StateThresholds.from_config(RAW, camera_id="CAM1", tuned_dir=tmp_path), "CAM1")
    assert message.startswith("Using tuned limits for CAM1")
    assert str(tmp_path / "CAM1.yaml") in message
    assert "2026-11-20" in message
    assert "up 26.5" in message and "side 29.0" in message and "notes/phone boundary 44.0" in message


def test_the_start_up_message_says_the_limits_are_the_defaults_with_the_values(tmp_path: Path) -> None:
    message = limits_message(StateThresholds.from_config(RAW, camera_id="CAM9", tuned_dir=tmp_path), "CAM9")
    assert message.startswith("Using the default limits for CAM9")
    assert "no tuned file" in message
    assert "up 30.0" in message and "side 35.0" in message and "notes/phone boundary 38.0" in message
