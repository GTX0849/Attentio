"""The default simulated timetable, built so that a plain class average misleads.

Two sections (SIM-A, the more attentive, in room SIM-R201; SIM-B in SIM-R202), five days, four
slots a day (three mornings and 14:00). Teachers are numbered by their order in the config:

- teacher 1 (better by `best_teacher_points`) teaches four of their five weekly lectures in
  the 14:00 slot, which is worse by `afternoon_slot_points`, so their plain average is punished;
- teacher 2 (neutral) teaches only the better section, only in the mornings, so their plain
  average is flattered;
- the 14:00 slot is shared by teachers 1, 3, 4 and 5 on different days, and teacher 1 also
  teaches one morning lecture, so the slot effect can be told apart from any one teacher.

Each grid row below is one section and slot; the five numbers are the teacher (1 to 5) on
Mon to Fri. In a column, the two sections never have the same teacher.
"""

from dataclasses import dataclass

from simulator.config import SimulatorConfig

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
SLOT_STARTS = ["09:00", "10:00", "11:00", "14:00"]
SLOT_ENDS = {"09:00": "09:50", "10:00": "10:50", "11:00": "11:50", "14:00": "14:50"}
AFTERNOON = "14:00"

# (section index, slot start) -> teacher number per weekday
GRID: dict[tuple[int, str], list[int]] = {
    (0, "09:00"): [2, 2, 2, 2, 2],
    (0, "10:00"): [3, 4, 3, 5, 4],
    (0, "11:00"): [5, 3, 4, 3, 5],
    (0, "14:00"): [1, 3, 1, 4, 5],
    (1, "09:00"): [3, 4, 5, 3, 1],
    (1, "10:00"): [4, 5, 4, 3, 5],
    (1, "11:00"): [3, 4, 5, 4, 3],
    (1, "14:00"): [3, 1, 4, 1, 4],
}


@dataclass(frozen=True)
class SlotSpec:
    """One weekly slot. `day` is 0 for Monday; times are local wall-clock."""

    day: int
    start: str
    end: str
    room: str
    section: str
    subject: str
    teacher: str
    camera: str


def room_for(section_index: int) -> str:
    return f"SIM-R{201 + section_index}"


def camera_for(section_index: int) -> str:
    return f"SIM-CAM-{201 + section_index}"


def timetable_slots(config: SimulatorConfig) -> list[SlotSpec]:
    slots: list[SlotSpec] = []
    for (section_index, start), row in GRID.items():
        for day, teacher_number in enumerate(row):
            teacher_index = teacher_number - 1
            slots.append(
                SlotSpec(
                    day=day,
                    start=start,
                    end=SLOT_ENDS[start],
                    room=room_for(section_index),
                    section=config.sections[section_index],
                    subject=config.subjects[teacher_index],
                    teacher=config.teachers[teacher_index],
                    camera=camera_for(section_index),
                )
            )
    return sorted(slots, key=lambda s: (s.day, s.start, s.room))


def timetable_csv(config: SimulatorConfig) -> str:
    """The slots as the CSV the timetable upload takes."""
    lines = ["day,start,end,room,section,subject,teacher"]
    lines += [
        ",".join([DAYS[s.day], s.start, s.end, s.room, s.section, s.subject, s.teacher])
        for s in timetable_slots(config)
    ]
    return "\n".join(lines) + "\n"
