"""The auto-tuning flow: pool a camera's tune files, check the data, write the tuned file and chart."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared.config import load_config
from worker.autotune import auto_tune, build_chart, main, on_tune_finished, read_camera_runs, read_tune_file
from worker.tuned import load_tuned, safe_path_part
from worker.tuning import COLUMNS

RAW = load_config().raw
NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
START = 1_800_000_000.0
HEADER = ",".join(COLUMNS)


def csv_row(t: float, label: int, rel_pitch: float, rel_yaw: float) -> str:
    # pitch/yaw/ref are filled in so the file looks like the real thing; only rel_* are analysed
    return f"{t:.3f},{label},{rel_pitch:.3f},{rel_yaw:.3f},0.000,0.000,{rel_pitch:.3f},{rel_yaw:.3f},0,20.000,attentive"


def run_rows(
    start: float, watching_s: float = 21.0, notes_s: float = 11.0, phone_s: float = 11.0, noise: bool = True
) -> list[str]:
    """One tune run at 10 rows a second. The angles are chosen so the answers can be worked out by hand:

    watching: rel_pitch 0, -1 ... -20 repeating and rel_yaw -10 ... +10 repeating
    notes: rel_pitch 20 to 25; phone: rel_pitch 45 to 50
    noise: wild label 0, 4 and 5 rows that must change nothing
    """
    out: list[str] = []
    t = start

    def add(label: int, seconds: float, pitch_of: Callable[[int], float], yaw_of: Callable[[int], float]) -> None:
        nonlocal t
        for i in range(round(seconds * 10)):
            out.append(csv_row(t, label, pitch_of(i), yaw_of(i)))
            t += 0.1

    if noise:
        add(0, 3.0, lambda i: -80.0, lambda i: 80.0)
        add(4, 3.0, lambda i: -80.0, lambda i: 80.0)
        add(5, 3.0, lambda i: 80.0, lambda i: 80.0)
    add(1, watching_s, lambda i: -float(i % 21), lambda i: float(i % 21 - 10))
    add(2, notes_s, lambda i: 20.0 + i % 6, lambda i: 0.0)
    add(3, phone_s, lambda i: 45.0 + i % 6, lambda i: 0.0)
    return out


def write_run(tuning_dir: Path, camera: str, stamp: str, rows: list[str], version: int = 1) -> Path:
    folder = tuning_dir / safe_path_part(camera)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}.csv"
    path.write_text("\n".join([f"# classpulse tune v{version} camera={camera}", HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def three_runs(tuning_dir: Path, camera: str = "CAM1") -> None:
    for k in range(3):
        write_run(tuning_dir, camera, f"2026112{k}T090000Z", run_rows(START + k * 1000))


# -- reading --------------------------------------------------------------------------------------


def test_a_tune_file_is_read_into_rows(tmp_path: Path) -> None:
    path = write_run(tmp_path, "CAM1", "20261120T090000Z", [csv_row(START, 2, 21.5, -3.25), csv_row(START + 0.1, 0, 1.0, 2.0)])
    rows = read_tune_file(path)
    assert [(r.t, r.label, r.rel_pitch, r.rel_yaw) for r in rows] == [(START, 2, 21.5, -3.25), (START + 0.1, 0, 1.0, 2.0)]


def test_all_of_a_cameras_files_are_pooled_and_malformed_ones_are_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_run(tmp_path, "CAM1", "20261120T090000Z", run_rows(START))
    write_run(tmp_path, "CAM1", "20261121T090000Z", run_rows(START + 1000))
    write_run(tmp_path, "CAM1", "20261122T090000Z", run_rows(START, 1), version=2)  # a format this code does not know
    write_run(tmp_path, "CAM1", "20261123T090000Z", ["1,2,three"])  # a broken row
    (tmp_path / "CAM1" / "20261124T090000Z.csv").write_text("not a tune file\n", encoding="utf-8")
    (tmp_path / "CAM1" / "20261125T090000Z.csv").write_bytes(b"\xff\xfe\x00 not text")
    write_run(tmp_path, "CAM2", "20261120T090000Z", run_rows(START))  # another camera's folder is not read
    with caplog.at_level(logging.WARNING):
        runs, skipped = read_camera_runs("CAM1", tmp_path)
    assert len(runs) == 2
    assert len(skipped) == 4
    for name in ("20261122T090000Z", "20261123T090000Z", "20261124T090000Z", "20261125T090000Z"):
        assert name in caplog.text
    assert "20261120T090000Z" not in caplog.text


def test_a_file_for_another_camera_in_the_folder_is_skipped(tmp_path: Path) -> None:
    path = write_run(tmp_path, "CAM1", "20261120T090000Z", run_rows(START))
    path.write_text(path.read_text(encoding="utf-8").replace("camera=CAM1", "camera=CAM7"), encoding="utf-8")
    runs, skipped = read_camera_runs("CAM1", tmp_path)
    assert runs == [] and len(skipped) == 1


def test_a_camera_with_no_files_has_no_runs(tmp_path: Path) -> None:
    assert read_camera_runs("CAM1", tmp_path) == ([], [])


# -- too little data ------------------------------------------------------------------------------


def test_too_little_data_changes_nothing_and_says_what_is_missing(tmp_path: Path) -> None:
    tuning, tuned = tmp_path / "tuning", tmp_path / "tuned"
    write_run(tuning, "CAM1", "20261120T090000Z", run_rows(START, watching_s=61.0, notes_s=8.1, phone_s=0.0))
    result = auto_tune("CAM1", tuning_dir=tuning, tuned_dir=tuned, raw_config=RAW, now=NOW)
    assert result.written is None and result.chart is None
    assert not tuned.exists()
    assert result.missing == ["needs 22 s more of taking notes", "needs 30 s more of phone in lap"]
    assert "needs 22 s more of taking notes" in result.text
    assert "nothing was changed" in result.text.lower()


def test_no_files_at_all_asks_for_all_three(tmp_path: Path) -> None:
    result = auto_tune("CAM1", tuning_dir=tmp_path / "tuning", tuned_dir=tmp_path / "tuned", raw_config=RAW, now=NOW)
    assert result.missing == [
        "needs 60 s more of watching the teacher",
        "needs 30 s more of taking notes",
        "needs 30 s more of phone in lap",
    ]
    assert result.runs == 0


def test_one_run_alone_can_be_too_short_while_several_together_are_enough(tmp_path: Path) -> None:
    tuning, tuned = tmp_path / "tuning", tmp_path / "tuned"
    write_run(tuning, "CAM1", "20261120T090000Z", run_rows(START))
    assert auto_tune("CAM1", tuning_dir=tuning, tuned_dir=tuned, raw_config=RAW, now=NOW, dry_run=True).missing
    three_runs(tuning)
    assert auto_tune("CAM1", tuning_dir=tuning, tuned_dir=tuned, raw_config=RAW, now=NOW, dry_run=True).missing == []


# -- enough data ----------------------------------------------------------------------------------


def test_enough_data_writes_the_tuned_file_and_the_chart(tmp_path: Path) -> None:
    tuning, tuned = tmp_path / "tuning", tmp_path / "tuned"
    three_runs(tuning)
    result = auto_tune("CAM1", tuning_dir=tuning, tuned_dir=tuned, raw_config=RAW, now=NOW)
    assert result.missing == []
    assert result.written == tuned / "CAM1.yaml"
    assert result.chart == tuned / "CAM1.html"
    assert result.chart.is_file()
    # watching: 95th percentile of the upward turn is 19, of the sideways turn 10; plus the 3 degree margin
    saved = load_tuned("CAM1", tuned)
    assert saved is not None
    assert saved.values == {"pitch_attentive_min_deg": -22.0, "yaw_max_deg": 13.0, "pitch_neutral_max_deg": 35.0}
    assert saved.created == NOW
    assert saved.metadata["runs"] == 3
    assert saved.metadata["separation_accuracy"] == 1.0
    assert saved.metadata["percentile"] == 95
    assert saved.metadata["up_percentile_deg"] == 19.0
    assert saved.metadata["side_percentile_deg"] == 10.0
    assert set(saved.metadata["minutes_per_label"]) == {"watching_the_teacher", "taking_notes", "phone_in_lap"}
    assert saved.metadata["minutes_per_label"]["watching_the_teacher"] == pytest.approx(63.0 / 60, abs=0.01)


def test_the_summary_shows_new_next_to_previous_with_runs_minutes_and_separation(tmp_path: Path) -> None:
    three_runs(tmp_path / "tuning")
    result = auto_tune("CAM1", tuning_dir=tmp_path / "tuning", tuned_dir=tmp_path / "tuned", raw_config=RAW, now=NOW)
    lines = {line.split("  ")[0].strip(): line for line in result.text.splitlines()}
    assert "30.0" in lines["Up limit (deg)"] and "22.0" in lines["Up limit (deg)"]
    assert "35.0" in lines["Side limit (deg)"] and "13.0" in lines["Side limit (deg)"]
    assert "38.0" in lines["Notes/phone boundary (deg)"] and "35.0" in lines["Notes/phone boundary (deg)"]
    assert "3 runs" in result.text
    assert "number of people is not recorded" in result.text
    assert "watching the teacher 1.1 min" in result.text
    assert "100%" in result.text  # the separation accuracy
    assert str(tmp_path / "tuned" / "CAM1.html") in result.text


def test_a_dry_run_shows_the_suggested_limits_and_writes_nothing(tmp_path: Path) -> None:
    three_runs(tmp_path / "tuning")
    result = auto_tune(
        "CAM1", tuning_dir=tmp_path / "tuning", tuned_dir=tmp_path / "tuned", raw_config=RAW, now=NOW, dry_run=True
    )
    assert result.written is None and result.chart is None
    assert not (tmp_path / "tuned").exists()
    assert result.limits is not None and result.limits.pitch_attentive_min_deg == -22.0
    assert "22.0" in result.text and "dry run" in result.text.lower()


def test_the_second_tune_shows_the_first_as_previous_and_keeps_it_in_history(tmp_path: Path) -> None:
    tuning, tuned = tmp_path / "tuning", tmp_path / "tuned"
    three_runs(tuning)
    auto_tune("CAM1", tuning_dir=tuning, tuned_dir=tuned, raw_config=RAW, now=NOW)
    # a fourth run that looks further sideways moves the side limit
    write_run(tuning, "CAM1", "20261130T090000Z", [csv_row(START + 5000 + i * 0.1, 1, 0.0, 30.0) for i in range(2000)])
    later = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
    second = auto_tune("CAM1", tuning_dir=tuning, tuned_dir=tuned, raw_config=RAW, now=later)
    assert second.previous.yaw_max_deg == 13.0
    assert second.limits is not None and second.limits.yaw_max_deg > 13.0
    assert len(list((tuned / "history" / "CAM1").glob("*.yaml"))) == 1


def test_overlapping_notes_and_phone_keep_the_current_boundary_and_the_summary_says_so(tmp_path: Path) -> None:
    tuning = tmp_path / "tuning"
    rows = [csv_row(START + i * 0.1, 1, -float(i % 21), 0.0) for i in range(700)]
    rows += [csv_row(START + 70 + i * 0.1, 2, 20.0 + i % 10, 0.0) for i in range(400)]
    rows += [csv_row(START + 110 + i * 0.1, 3, 20.0 + i % 10, 0.0) for i in range(400)]
    write_run(tuning, "CAM1", "20261120T090000Z", rows)
    result = auto_tune("CAM1", tuning_dir=tuning, tuned_dir=tmp_path / "tuned", raw_config=RAW, now=NOW)
    saved = load_tuned("CAM1", tmp_path / "tuned")
    assert saved is not None and saved.values["pitch_neutral_max_deg"] == 38.0
    assert saved.metadata["notes_phone_boundary_kept"] is True
    assert "overlap" in result.text and "kept" in result.text


# -- the end of a --tune run ----------------------------------------------------------------------


def test_on_tune_finished_with_too_little_data_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tuning, tuned = tmp_path / "tuning", tmp_path / "tuned"
    path = write_run(tuning, "CAM1", "20261120T090000Z", run_rows(START))
    on_tune_finished(path, "CAM1", tuned_dir=tuned, raw_config=RAW, now=NOW)
    assert not tuned.exists()
    assert "needs" in capsys.readouterr().out


def test_on_tune_finished_with_enough_data_writes_the_file_and_the_chart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tuning, tuned = tmp_path / "tuning", tmp_path / "tuned"
    three_runs(tuning)
    path = tuning / "CAM1" / "20261122T090000Z.csv"
    on_tune_finished(path, "CAM1", tuned_dir=tuned, raw_config=RAW, now=NOW)
    assert (tuned / "CAM1.yaml").is_file() and (tuned / "CAM1.html").is_file()
    out = capsys.readouterr().out
    assert "Up limit (deg)" in out and str(tuned / "CAM1.html") in out


def test_on_tune_finished_never_raises_into_the_worker(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        on_tune_finished(tmp_path / "t" / "CAM1" / "x.csv", "CAM1", tuned_dir=tmp_path / "tuned", raw_config={})
    assert "auto-tuning" in caplog.text.lower()


# -- the chart ------------------------------------------------------------------------------------


def test_the_chart_plots_relative_yaw_and_pitch_by_label_with_the_chosen_limits_as_rules(tmp_path: Path) -> None:
    three_runs(tmp_path / "tuning")
    result = auto_tune("CAM1", tuning_dir=tmp_path / "tuning", tuned_dir=tmp_path / "tuned", raw_config=RAW, now=NOW)
    assert result.chart is not None
    html = result.chart.read_text(encoding="utf-8")
    assert "vega" in html.lower()
    spec_text = json.dumps(
        build_chart([r for run in read_camera_runs("CAM1", tmp_path / "tuning")[0] for r in run.rows], result.limits).to_dict()
        if result.limits is not None
        else {}
    )
    assert "rel_yaw" in spec_text and "rel_pitch" in spec_text
    for value in ("-22.0", "13.0", "-13.0", "35.0"):  # up limit, both sides, notes/phone boundary
        assert value in spec_text
    assert "Watching the teacher" in spec_text and "Phone in lap" in spec_text
    assert "Watching the teacher" in html


# -- the command ----------------------------------------------------------------------------------


def test_the_command_runs_a_dry_run_by_camera_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    three_runs(tmp_path / "tuning")
    code = main(["CAM1", "--dry-run", "--tuning-dir", str(tmp_path / "tuning"), "--tuned-dir", str(tmp_path / "tuned")])
    assert code == 0
    assert "Up limit (deg)" in capsys.readouterr().out
    assert not (tmp_path / "tuned").exists()


def test_the_command_writes_without_dry_run_and_exits_1_when_there_is_too_little_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    three_runs(tmp_path / "tuning")
    args = ["--tuning-dir", str(tmp_path / "tuning"), "--tuned-dir", str(tmp_path / "tuned")]
    assert main(["CAM1", *args]) == 0
    assert (tmp_path / "tuned" / "CAM1.yaml").is_file()
    assert main(["CAM9", *args]) == 1
    assert "needs 60 s more of watching the teacher" in capsys.readouterr().out


def test_the_workers_end_of_run_hook_runs_the_auto_tune(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from worker import autotune
    from worker.tuning import on_tune_finished as workers_hook

    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(autotune, "on_tune_finished", lambda path, camera: calls.append((path, camera)))
    workers_hook(tmp_path / "CAM1" / "x.csv", "CAM1")
    assert calls == [(tmp_path / "CAM1" / "x.csv", "CAM1")]
