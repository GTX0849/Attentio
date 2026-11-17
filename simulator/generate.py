"""Pure generation: students, true effects, lecture schedule and attention records.

Nothing here touches the network or the database. Everything is seeded: the same
`SimulatorConfig` and `now` always give the same data. Each random draw comes from a
generator seeded by (seed, what it is for), so the data of one lecture or student does not
depend on the order of generation.

Mean attention in a window is

    student baseline + section effect + teacher effect + slot effect

(effects are configured in attention points, so 6 points is 0.06), plus a shared lecture
shock and the student's own shock for the lecture. Each 30-second window then splits its
seconds into not visible, neutral, attentive and distracted around that mean.
"""

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from simulator.config import SimulatorConfig
from simulator.timetable import AFTERNOON, SLOT_STARTS, SlotSpec, timetable_slots

# fmt: off
FIRST_NAMES = [
    "Aarav", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vihaan", "Ishita", "Arjun", "Tara",
    "Neil", "Saanvi", "Dev", "Riya", "Karan", "Nisha", "Aditya", "Pooja", "Yash", "Kavya",
    "Harsh", "Sneha", "Rahul", "Pallavi", "Manav", "Divya", "Siddharth", "Aisha", "Varun", "Lakshmi",
]
# fmt: on
LAST_NAMES = ["Sharma", "Nair", "Iyer", "Gupta", "Reddy", "Khan", "Patel", "Das", "Mehta", "Rao", "Singh"]

HIDDEN_WINDOW_SECONDS = (2, 12)  # a partly not-visible window loses this many seconds
PARTLY_HIDDEN_CHANCE = 0.06  # per window, on top of the archetype's chance of a fully hidden one
MIN_ATTENTION, MAX_ATTENTION = 0.02, 0.98


@dataclass(frozen=True)
class SimStudent:
    register_no: str
    name: str
    section: str
    archetype: str
    baseline: float  # personal attention level, 0 to 1


@dataclass(frozen=True)
class SimLecture:
    """One dated occurrence of a slot. Times are UTC."""

    slot: SlotSpec
    date: date  # local calendar date
    starts_at: datetime
    ends_at: datetime

    @property
    def key(self) -> str:
        return f"{self.date.isoformat()}|{self.slot.room}|{self.slot.start}"


def _rng(config: SimulatorConfig, *parts: object) -> random.Random:
    return random.Random("|".join([str(config.seed), *map(str, parts)]))


def _clip(value: float, low: float = MIN_ATTENTION, high: float = MAX_ATTENTION) -> float:
    return max(low, min(high, value))


# ---- cohort -------------------------------------------------------------------------------


def _archetype_counts(config: SimulatorConfig) -> dict[str, int]:
    """Students per archetype in one section, by largest remainder so the total is exact."""
    total = config.students_per_section
    exact = {a.name: a.share * total for a in config.archetypes}
    counts = {name: int(value) for name, value in exact.items()}
    by_remainder = sorted(exact, key=lambda n: (exact[n] - counts[n], n), reverse=True)
    for name in by_remainder[: total - sum(counts.values())]:
        counts[name] += 1
    return counts


def build_students(config: SimulatorConfig) -> list[SimStudent]:
    """Both sections' students: SIM-A01 .. SIM-B30, archetypes mixed within each section."""
    kinds = {a.name: a for a in config.archetypes}
    counts = _archetype_counts(config)
    students: list[SimStudent] = []
    for section_index, section in enumerate(config.sections):
        letter = section[-1]
        order = [name for name, n in counts.items() for _ in range(n)]
        _rng(config, "mix", section).shuffle(order)
        for i, archetype in enumerate(order):
            register_no = f"SIM-{letter}{i + 1:02d}"
            first = FIRST_NAMES[(i + 7 * section_index) % len(FIRST_NAMES)]
            last = LAST_NAMES[(i * 3 + section_index * 5) % len(LAST_NAMES)]
            baseline = _clip(
                kinds[archetype].baseline + _rng(config, "baseline", register_no).gauss(0, 0.04),
                0.10,
                0.95,
            )
            students.append(
                SimStudent(register_no, f"{first} {last}", section, archetype, round(baseline, 4))
            )
    return students


def true_effects(config: SimulatorConfig) -> list[dict[str, Any]]:
    """The built-in effects in attention points, in the shape of PUT /api/simulation."""
    effects: list[dict[str, Any]] = []
    for i, teacher in enumerate(config.teachers):
        value = config.best_teacher_points if i == 0 else 0.0
        effects.append({"kind": "teacher", "key": teacher, "value": value})
    for start in SLOT_STARTS:
        value = config.afternoon_slot_points if start == AFTERNOON else 0.0
        effects.append({"kind": "slot", "key": start, "value": value})
    for i, section in enumerate(config.sections):
        value = config.better_section_points if i == 0 else 0.0
        effects.append({"kind": "section", "key": section, "value": value})
    return effects


# ---- schedule -------------------------------------------------------------------------------


def _clock(text: str) -> datetime:
    return datetime.strptime(text, "%H:%M")


def lecture_schedule(config: SimulatorConfig, tz: ZoneInfo, now: datetime) -> list[SimLecture]:
    """Every lecture of the `weeks` weeks ending on today's local date that has ended by `now`."""
    today = now.astimezone(tz).date()
    first = today - timedelta(days=7 * config.weeks - 1)
    slots = timetable_slots(config)
    lectures: list[SimLecture] = []
    day = first
    while day <= today:
        for slot in (s for s in slots if s.day == day.weekday()):
            starts = datetime.combine(day, _clock(slot.start).time(), tz).astimezone(UTC)
            ends = datetime.combine(day, _clock(slot.end).time(), tz).astimezone(UTC)
            if ends <= now:
                lectures.append(SimLecture(slot, day, starts, ends))
        day += timedelta(days=1)
    return sorted(lectures, key=lambda x: (x.starts_at, x.slot.room))


# ---- records -------------------------------------------------------------------------------


def mean_attention(config: SimulatorConfig, student: SimStudent, lecture: SimLecture) -> float:
    """Baseline plus the section, teacher and slot effects (before any noise), as a share 0 to 1."""
    slot = lecture.slot
    points = 0.0
    if slot.section == config.sections[0]:
        points += config.better_section_points
    if slot.teacher == config.teachers[0]:
        points += config.best_teacher_points
    if slot.start == AFTERNOON:
        points += config.afternoon_slot_points
    return student.baseline + points / 100


def lecture_records(
    config: SimulatorConfig, lecture: SimLecture, students: list[SimStudent]
) -> list[dict[str, Any]]:
    """The records of the given students (those in the lecture's section) for one lecture.

    A student who is absent has no records at all. Records are in the JSON shape of
    POST /api/records.
    """
    kinds = {a.name: a for a in config.archetypes}
    window = config.window_seconds
    windows = int((lecture.ends_at - lecture.starts_at).total_seconds()) // window
    shared_shock = _rng(config, "lecture", lecture.key).gauss(0, config.lecture_noise)
    records: list[dict[str, Any]] = []
    for student in students:
        if student.section != lecture.slot.section:
            continue
        kind = kinds[student.archetype]
        rng = _rng(config, "student", lecture.key, student.register_no)
        if rng.random() < kind.absent:
            continue
        mean = mean_attention(config, student, lecture) + shared_shock
        mean += rng.gauss(0, config.student_lecture_noise)
        for k in range(windows):
            hidden = 0
            if rng.random() < kind.hidden:
                hidden = window
            elif rng.random() < PARTLY_HIDDEN_CHANCE:
                hidden = rng.randint(*HIDDEN_WINDOW_SECONDS)
            visible = window - hidden
            neutral = round(visible * config.neutral_share * rng.uniform(0.5, 1.5))
            scored = visible - neutral
            share = _clip(rng.gauss(mean, config.window_noise))
            attentive = round(scored * share)
            records.append(
                {
                    "camera_id": lecture.slot.camera,
                    "student_id": student.register_no,
                    "window_start": (lecture.starts_at + timedelta(seconds=k * window)).isoformat(),
                    "attentive": attentive,
                    "neutral": neutral,
                    "distracted": scored - attentive,
                    "not_visible": hidden,
                }
            )
    return records


def all_records(
    config: SimulatorConfig, tz: ZoneInfo, now: datetime
) -> Iterator[tuple[SimLecture, list[dict[str, Any]]]]:
    """Each lecture of the schedule with its records, oldest lecture first."""
    students = build_students(config)
    for lecture in lecture_schedule(config, tz, now):
        yield lecture, lecture_records(config, lecture, students)


# ---- marks -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SimTest:
    """One test for a teacher, subject and section. `date` is the local date of the last lecture it covers."""

    number: int  # 1-based, within the pairing
    name: str  # "Mid-term 1 (SIM-A)": the section is in the name so the two sections' tests stay apart
    teacher: str
    subject: str
    section: str
    date: date
    max_marks: float

    @property
    def key(self) -> str:
        return f"{self.teacher}|{self.section}|{self.date.isoformat()}"


def marks_schedule(config: SimulatorConfig, tz: ZoneInfo, now: datetime) -> list[SimTest]:
    """Two tests (by default) for each teacher and section pairing, ordered by date.

    The lecture days are split into `marks.tests_per_pairing` blocks of consecutive days, and each
    test is dated on the last lecture day of its block, so it is dated after every lecture it
    covers. A pairing with no lecture in a block gets no test for it.
    """
    lectures = lecture_schedule(config, tz, now)
    days = sorted({x.date for x in lectures})
    n = config.marks.tests_per_pairing
    tests: list[SimTest] = []
    pairings = sorted({(x.slot.teacher, x.slot.subject, x.slot.section) for x in lectures})
    for k in range(1, n + 1):
        block = days[math.ceil(len(days) * (k - 1) / n) : math.ceil(len(days) * k / n)]
        if not block:
            continue
        for teacher, subject, section in pairings:
            if not any(
                x.slot.teacher == teacher and x.slot.section == section and block[0] <= x.date <= block[-1]
                for x in lectures
            ):
                continue
            tests.append(
                SimTest(
                    number=k,
                    name=f"Mid-term {k} ({section})",
                    teacher=teacher,
                    subject=subject,
                    section=section,
                    date=block[-1],
                    max_marks=config.marks.max_marks[(k - 1) % len(config.marks.max_marks)],
                )
            )
    return sorted(tests, key=lambda t: (t.date, t.teacher, t.section))


def marks_for(config: SimulatorConfig, test: SimTest, students: list[SimStudent]) -> list[dict[str, Any]]:
    """The marks of the section's students who sat the test, as rows of the marks upload."""
    marks = config.marks
    offset_teacher = marks.teacher_points if test.teacher == config.teachers[0] else 0.0
    rows: list[dict[str, Any]] = []
    for student in students:
        if student.section != test.section:
            continue
        own = _rng(config, "marks-offset", student.register_no).gauss(0, marks.student_sd)
        rng = _rng(config, "marks", test.key, student.register_no)
        percent = _clip(
            marks.ability[student.archetype] + own + offset_teacher + rng.gauss(0, marks.test_sd), 0.0, 100.0
        )
        if percent < marks.skip_below and rng.random() < marks.skip_chance:
            continue  # expected to fail, stays away
        rows.append(
            {
                "register_no": student.register_no,
                "marks": round(percent / 100 * test.max_marks, 1),
                "max_marks": test.max_marks,
            }
        )
    return rows


def marks_csv(rows: list[dict[str, Any]]) -> bytes:
    """The rows as the CSV the marks upload takes."""
    lines = ["register_no,marks,max_marks"]
    lines += [f"{r['register_no']},{r['marks']:g},{r['max_marks']:g}" for r in rows]
    return ("\n".join(lines) + "\n").encode()
