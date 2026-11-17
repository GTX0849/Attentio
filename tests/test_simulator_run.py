"""A small simulator run against the real backend app (through the test client), end to end.

Only public HTTP endpoints are used on both sides. "Now" is Wednesday 2026-03-18 12:00 UTC, so
one week of lectures ending today is Thu 12th to Wed 18th March (five weekdays).
"""

from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from simulator.__main__ import build_parser
from simulator.client import Backend, seed, wipe
from simulator.config import load_simulator_config
from tests.conftest import FakeClock
from tests.test_student_profile_api import send

NOW = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)


def run_small(client: TestClient, clock: FakeClock) -> tuple[int, int]:
    """Seed the small cohort; returns (lectures, records) as reported by the simulator."""
    clock.set(NOW)
    config = replace(load_simulator_config(small=True), records_per_request=1000)
    report = seed(Backend(client), config, now=NOW)
    return report.lectures, report.records


def test_a_small_run_creates_the_cohort_lectures_and_records_through_the_api(
    client: TestClient, clock: FakeClock
) -> None:
    lectures, records = run_small(client, clock)
    assert lectures == 40  # 5 weekdays x 2 sections x 4 slots

    summary = client.get("/api/simulation").json()
    assert summary["students"] == 12
    assert summary["teachers"] == 5
    assert summary["sections"] == ["SIM-A", "SIM-B"]
    assert summary["rooms"] == 2
    assert summary["lectures"] == 40
    assert summary["records"] == records
    assert 0.80 * 40 * 6 * 100 < records < 40 * 6 * 100  # some absences, mostly present
    assert summary["weeks"] == 1
    assert sum(summary["archetypes"].values()) == 12
    effects = {(e["kind"], e["key"]): e["value"] for e in summary["effects"]}
    assert effects[("teacher", "Dr. Anita Menon")] == 6.0
    assert effects[("slot", "14:00")] == -12.0
    assert effects[("section", "SIM-A")] == 5.0

    stored = client.get("/api/lectures", params={"limit": 500}).json()
    assert len(stored) == 40
    assert all(x["simulated"] for x in stored)
    assert {x["room_id"] for x in stored} == {"SIM-R201", "SIM-R202"}
    assert {x["teacher"] for x in stored} == set(load_simulator_config().teachers)

    students = {s["register_no"]: s for s in client.get("/api/students").json()}
    assert len(students) == 13  # twelve simulated and the live STU001
    assert students["SIM-B06"]["section"] == "SIM-B" and students["SIM-B06"]["simulated"]
    assert students["SIM-A01"]["archetype"] in {"engaged", "average", "struggling", "disengaged"}


def test_a_simulated_student_has_a_week_of_section_attendance_with_absences(
    client: TestClient, clock: FakeClock
) -> None:
    run_small(client, clock)
    profiles = [client.get(f"/api/students/SIM-A{n:02d}/profile").json() for n in range(1, 7)]
    for body in profiles:
        assert body["simulated"] is True
        assert len(body["attendance"]) == 20  # every lecture of section SIM-A in the week
        assert body["month"]["total"] == 20
    present = [sum(row["present"] for row in body["attendance"]) for body in profiles]
    assert max(present) >= 15
    # Somebody missed a lecture completely: a row with no records.
    absent = [
        row
        for body in profiles
        for row in body["attendance"]
        if row["figure"]["reason"] == "no_records"
    ]
    assert absent and all(row["present"] is False for row in absent)


def test_seeding_again_replaces_the_simulation_and_leaves_live_data_alone(
    client: TestClient, clock: FakeClock
) -> None:
    clock.set(NOW)
    send(client, "2026-03-18T05:00:00", 4, attentive=30)  # a live record for STU001 on CAM1
    _, first = run_small(client, clock)
    _, second = run_small(client, clock)
    assert first == second  # the same seed gives the same data
    assert client.get("/api/simulation").json()["records"] == second
    live = client.get("/api/students/STU001/profile").json()
    assert live["simulated"] is False
    assert live["overall"]["attentive"] == 120

    removed = wipe(Backend(client))
    assert removed["students"] == 12 and removed["records"] == second
    assert client.get("/api/simulation").json() is None
    assert client.get("/api/students/STU001/profile").json()["overall"]["attentive"] == 120


def test_the_command_line_has_seed_and_wipe_with_the_documented_options() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["seed", "--weeks", "2", "--seed", "5", "--small", "--backend-url", "http://127.0.0.1:8012"]
    )
    assert (args.command, args.weeks, args.seed, args.small) == ("seed", 2, 5, True)
    assert args.backend_url == "http://127.0.0.1:8012"
    assert parser.parse_args(["wipe"]).command == "wipe"
