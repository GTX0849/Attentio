"""The tune recorder: what it writes, when it skips, and the summary."""

from pathlib import Path

import numpy as np

from shared.schema import AttentionState
from worker.concurrency import Status
from worker.face_tracker import Box
from worker.perception import Diagnostics, FaceDiagnostic, FramingReadout
from worker.readings import Reading
from worker.tuning import COLUMNS, TuneRecorder, format_summary

START = 1_800_000_000.0  # 2027-01-15 08:00:00 UTC


def diagnostics(size_pct: float = 22.5) -> Diagnostics:
    box = Box(0.4, 0.3, 0.6, 0.6)
    face = FaceDiagnostic(1, box, np.zeros((478, 2)), True, 1.0, 2.0)
    return Diagnostics(faces=(face,), framing=FramingReadout(100.0, size_pct, 0.5))


def status(
    pitch: float = 4.0,
    yaw: float = -6.0,
    *,
    eyes_closed: bool = False,
    state: AttentionState | None = AttentionState.ATTENTIVE,
    calibrating: bool = False,
    face: bool = True,
    diag: Diagnostics | None = None,
    reference: tuple[float, float] | None = (1.5, -2.0),
) -> Status:
    reading = Reading(
        timestamp=START,
        face_present=face,
        shoulders_present=True,
        pitch_deg=pitch if face else 0.0,
        yaw_deg=yaw if face else 0.0,
        eyes_closed=eyes_closed,
    )
    return Status(
        reading,
        state,
        8.0,
        3.0 if calibrating else None,
        diagnostics=diag if diag is not None else diagnostics(),
        reference=reference,
        region=(15.0, 25.0),
    )


def lines_of(path: Path | None) -> list[str]:
    assert path is not None
    return path.read_text(encoding="utf-8").splitlines()


def test_the_file_has_a_comment_line_the_header_and_one_row_per_tick(tmp_path: Path) -> None:
    rec = TuneRecorder("CAM1", tmp_path, started_at=START)
    assert rec.record(status(), 1, START + 0.1) is True
    assert rec.record(status(5.0, -7.0, eyes_closed=True, state=AttentionState.DISTRACTED), 5, START + 0.2) is True
    summary = rec.close()

    assert summary.path == tmp_path / "CAM1" / "20270115T080000Z.csv"
    assert lines_of(summary.path) == [
        "# classpulse tune v1 camera=CAM1",
        "t,label,pitch,yaw,ref_pitch,ref_yaw,rel_pitch,rel_yaw,eyes_closed,face_size_pct,state",
        "1800000000.100,1,4.000,-6.000,1.500,-2.000,2.500,-4.000,0,22.500,attentive",
        "1800000000.200,5,5.000,-7.000,1.500,-2.000,3.500,-5.000,1,22.500,distracted",
    ]
    assert ",".join(COLUMNS) == lines_of(summary.path)[1]


def test_ticks_during_calibration_or_without_a_locked_face_are_skipped(tmp_path: Path) -> None:
    rec = TuneRecorder("CAM1", tmp_path, started_at=START)
    assert rec.record(status(calibrating=True), 1, START) is False
    assert rec.record(status(face=False), 1, START + 0.1) is False
    assert rec.record(status(diag=Diagnostics()), 1, START + 0.2) is False  # face held through a dropout: not in view
    assert rec.record(status(reference=None), 1, START + 0.3) is False  # no learner: no reference
    assert rec.record(status(), 2, START + 0.4) is True
    summary = rec.close()
    assert len(lines_of(summary.path)) == 3  # comment, header, the one good row
    assert summary.rows_per_label == {0: 0, 1: 0, 2: 1, 3: 0, 4: 0, 5: 0}


def test_a_state_not_known_yet_is_written_as_starting(tmp_path: Path) -> None:
    rec = TuneRecorder("CAM1", tmp_path, started_at=START)
    rec.record(status(state=None), 0, START)
    summary = rec.close()
    assert lines_of(summary.path)[2].endswith(",starting")


def test_the_summary_counts_rows_per_label(tmp_path: Path) -> None:
    rec = TuneRecorder("CAM1", tmp_path, started_at=START)
    for i, label in enumerate([1, 1, 1, 3, 3, 0]):
        rec.record(status(), label, START + i * 0.1)
    summary = rec.close()
    assert summary.camera_id == "CAM1"
    assert summary.rows_per_label == {0: 1, 1: 3, 2: 0, 3: 2, 4: 0, 5: 0}
    text = format_summary(summary)
    assert str(summary.path) in text
    assert "1 - Watching the teacher: 3" in text
    assert "3 - Phone in lap: 2" in text
    assert "6 rows" in text


def test_off_means_no_file_no_folder_and_no_rows(tmp_path: Path) -> None:
    rec = TuneRecorder("CAM1", tmp_path / "tuning", enabled=False, started_at=START)
    assert rec.record(status(), 1, START) is False
    summary = rec.close()
    assert summary.path is None
    assert sum(summary.rows_per_label.values()) == 0
    assert not (tmp_path / "tuning").exists()


def test_the_camera_id_is_made_safe_for_a_path(tmp_path: Path) -> None:
    rec = TuneRecorder("../Room 4/cam:1", tmp_path, started_at=START)
    summary = rec.close()
    assert summary.path is not None
    assert summary.path.parent.parent == tmp_path  # stays inside the tuning folder
    assert summary.path.parent.name == "_Room_4_cam_1"
    assert "# classpulse tune v1 camera=../Room 4/cam:1" == lines_of(summary.path)[0]


def test_rows_reach_the_disk_about_every_second_and_on_close(tmp_path: Path) -> None:
    rec = TuneRecorder("CAM1", tmp_path, started_at=START)
    path = tmp_path / "CAM1" / "20270115T080000Z.csv"
    rec.record(status(), 1, START + 0.1)
    assert len(lines_of(path)) == 2  # the header is on disk from the start; the row is still buffered
    rec.record(status(), 1, START + 1.2)  # more than a second since the file was opened
    assert len(lines_of(path)) == 4
    rec.record(status(), 1, START + 1.3)
    rec.close()
    assert len(lines_of(path)) == 5


def test_two_runs_in_the_same_second_get_separate_files(tmp_path: Path) -> None:
    a = TuneRecorder("CAM1", tmp_path, started_at=START).close()
    b = TuneRecorder("CAM1", tmp_path, started_at=START).close()
    assert a.path != b.path
    assert a.path is not None and a.path.exists() and b.path is not None and b.path.exists()


def test_after_close_further_ticks_are_ignored(tmp_path: Path) -> None:
    rec = TuneRecorder("CAM1", tmp_path, started_at=START)
    summary = rec.close()
    assert rec.record(status(), 1, START) is False
    assert len(lines_of(summary.path)) == 2
