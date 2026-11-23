"""The `simulator:` section of config/attentio.yaml as a typed object."""

from dataclasses import dataclass
from typing import Any

from shared.config import load_config

MIN_TEACHERS = 5  # the default timetable (simulator/timetable.py) uses five teachers
MIN_SECTIONS = 2


@dataclass(frozen=True)
class Archetype:
    """A kind of student: share of each section, personal baseline attention (0 to 1),
    chance of missing a whole lecture and chance that a window is not visible."""

    name: str
    share: float
    baseline: float
    absent: float
    hidden: float


@dataclass(frozen=True)
class MarksSimConfig:
    """How the simulator makes marks: tests per section and teacher, size and the built-in teacher effect."""

    tests_per_pairing: int
    max_marks: tuple[float, ...]  # out of, by test number (repeats)
    ability: dict[str, float]  # archetype -> mean mark, percent of the maximum
    student_sd: float
    test_sd: float
    teacher_points: float  # added to every mark in the first teacher's tests
    skip_below: float  # a mark (percent) below this may be skipped ...
    skip_chance: float  # ... with this probability


@dataclass(frozen=True)
class SimulatorConfig:
    weeks: int
    seed: int
    students_per_section: int
    records_per_request: int
    window_seconds: int
    sections: tuple[str, ...]  # the first is the more attentive one
    teachers: tuple[str, ...]  # the first is the better one, the second is flattered by its section
    subjects: tuple[str, ...]  # one per teacher
    best_teacher_points: float
    afternoon_slot_points: float
    better_section_points: float
    window_noise: float
    student_lecture_noise: float
    lecture_noise: float
    neutral_share: float
    archetypes: tuple[Archetype, ...]
    marks: MarksSimConfig


def load_simulator_config(small: bool = False) -> SimulatorConfig:
    """Read the section. `small` applies its `small:` overrides (a week, a few students)."""
    shared = load_config()
    raw: dict[str, Any] = shared.raw["simulator"]
    sizes = {"weeks": raw["weeks"], "students_per_section": raw["students_per_section"]}
    if small:
        sizes.update(raw["small"])
    effects, noise = raw["effects"], raw["noise"]
    config = SimulatorConfig(
        weeks=int(sizes["weeks"]),
        seed=int(raw["seed"]),
        students_per_section=int(sizes["students_per_section"]),
        records_per_request=int(raw["records_per_request"]),
        window_seconds=shared.window_seconds,
        sections=tuple(str(x) for x in raw["sections"]),
        teachers=tuple(str(x) for x in raw["teachers"]),
        subjects=tuple(str(x) for x in raw["subjects"]),
        best_teacher_points=float(effects["best_teacher_points"]),
        afternoon_slot_points=float(effects["afternoon_slot_points"]),
        better_section_points=float(effects["better_section_points"]),
        window_noise=float(noise["window"]),
        student_lecture_noise=float(noise["student_lecture"]),
        lecture_noise=float(noise["lecture"]),
        neutral_share=float(noise["neutral_share"]),
        archetypes=tuple(
            Archetype(
                name=name,
                share=float(a["share"]),
                baseline=float(a["baseline"]),
                absent=float(a["absent"]),
                hidden=float(a["hidden"]),
            )
            for name, a in raw["archetypes"].items()
        ),
        marks=_marks(raw["marks"]),
    )
    validate(config)
    return config


def _marks(raw: dict[str, Any]) -> MarksSimConfig:
    return MarksSimConfig(
        tests_per_pairing=int(raw["tests_per_pairing"]),
        max_marks=tuple(float(x) for x in raw["max_marks"]),
        ability={str(k): float(v) for k, v in raw["ability"].items()},
        student_sd=float(raw["student_sd"]),
        test_sd=float(raw["test_sd"]),
        teacher_points=float(raw["teacher_points"]),
        skip_below=float(raw["skip_below"]),
        skip_chance=float(raw["skip_chance"]),
    )


def validate(config: SimulatorConfig) -> None:
    if len(config.teachers) < MIN_TEACHERS or len(config.subjects) < len(config.teachers):
        raise ValueError(f"simulator needs at least {MIN_TEACHERS} teachers and a subject for each")
    if len(config.sections) != MIN_SECTIONS:
        raise ValueError("simulator needs exactly two sections")
    if config.weeks < 1 or config.students_per_section < 1:
        raise ValueError("weeks and students_per_section must be at least 1")
    if abs(sum(a.share for a in config.archetypes) - 1.0) > 1e-6:
        raise ValueError("the archetype shares must add up to 1")
    if config.marks.tests_per_pairing < 1 or not config.marks.max_marks or min(config.marks.max_marks) <= 0:
        raise ValueError("marks needs at least one test per pairing and positive maximum marks")
    missing = [a.name for a in config.archetypes if a.name not in config.marks.ability]
    if missing:
        raise ValueError(f"marks.ability has no value for the archetype(s): {', '.join(missing)}")
