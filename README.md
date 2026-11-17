# ClassPulse

A laptop webcam worker classifies the person in view into attention states and sends one record per student per 30-second window to a FastAPI backend (PostgreSQL). A Streamlit dashboard reads only from the backend over HTTP.

The worker decides states with the rules in the `states:` section of `config/classpulse.yaml` (eyes-closed grace, look-down allowance, pitch and yaw bands). It feeds those rules with real readings from MediaPipe (head pitch and yaw, eyes closed, shoulders in view); see Real readings below.

## Layout

| Folder | What it holds |
| --- | --- |
| `shared/` | Config loader (`config.py`) and the attention record schema (`schema.py`) used by worker and backend |
| `worker/` | Webcam capture, window aggregation, spool and retry |
| `backend/` | FastAPI app, SQLAlchemy models, timetable and lecture logic, seeding |
| `dashboard/` | Streamlit app; one module per role in `dashboard/views/`, shared look in `dashboard/theme.py` |
| `.streamlit/` | Streamlit theme (colours and IBM Plex fonts) |
| `samples/` | `timetable.csv`, a sample weekly timetable for the demo |
| `simulator/` | Simulated classroom history: cohort, timetable and records generated and sent through the backend API (see Simulator) |
| `tests/` | pytest suite |
| `config/classpulse.yaml` | The one shared config file (fps, window length, backend URL, refresh, simulator sizes and effects) |

## Setup (Windows)

Requires Python 3.12 and a running PostgreSQL server.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and put your own PostgreSQL password in both URLs:

```
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/classpulse
TEST_DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/classpulse_test
```

You do not need to create the databases. When the backend (or the tests) start, a missing database is created by connecting to the server's `postgres` database with the same credentials. Tables are created automatically and the demo data is seeded: room `R101`, camera `CAM1`, student `STU001`. `.env` is git-ignored; never commit it.

## Run

Use three terminals, each with the venv active, in the project root.

**Backend** (binds to 127.0.0.1:8000 only):

```powershell
python -m backend
```

Equivalent: `python -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8000`. API docs are at http://127.0.0.1:8000/docs.

**Dashboard:**

```powershell
streamlit run dashboard/app.py
```

**Worker** (needs a webcam; press `q` in the preview window or Ctrl+C to stop):

```powershell
python -m worker --student STU001 --camera CAM1
```

Options: `--no-preview` (no window, stop with Ctrl+C), `--camera-index N`, `--spool PATH`, `--detector {mediapipe,haar}` (default `mediapipe`; `haar` is the old face-only fallback), `--debug` (log every reading). Frames are never written to disk. Download the models first (next section).

If a window cannot be sent (for example the backend is stopped), the worker appends it to `.spool/unsent.jsonl` and resends everything spooled with the next window and at start-up. Stopping and restarting the backend loses nothing.

## Real readings

**Download the models once** (about 9.5 MB; written to the git-ignored `models/` folder, files already present are skipped):

```powershell
python -m worker.models download
```

This fetches only the official MediaPipe `face_landmarker` and `pose_landmarker_lite` (float16, latest) from `storage.googleapis.com/mediapipe-models`. Without them `python -m worker` stops with a message telling you to run this.

**What MediaPipe 1.2.3 provides, and the route taken.** This version has only the Tasks API (no `mp.solutions`). FaceLandmarker there can output a facial transformation matrix (`output_facial_transformation_matrixes`) and blendshapes (`output_face_blendshapes`, including `eyeBlinkLeft` and `eyeBlinkRight`), so both are used and no `solvePnP` or eye-aspect-ratio code was needed:

- Head pitch and yaw come from the rotation part of the transformation matrix (the direction the face points).
- Eyes closed means both `eyeBlinkLeft` and `eyeBlinkRight` are at or above `perception.blink_threshold`. The raw value goes to the rules, which apply the 1.5 s grace.
- Shoulders come from PoseLandmarker (lite): both shoulder landmarks need a visibility at or above `perception.shoulder_visibility_threshold`.
- Both models run in VIDEO mode on the unmirrored camera frame. Only the preview is mirrored.

**Calibration.** At launch, look at the screen for `perception.calibration_seconds` (3 s). The mean raw pitch and yaw over that time (face frames only) become the zero, and every later reading is relative to it. While calibrating, readings still go to the state rules but carry pitch 0 and yaw 0 (looking at the screen), so the first seconds count as Attentive. If nobody is in view, calibration waits for a face. Press the key in `perception.recalibrate_key` (`c`) in the preview window to calibrate again at any time. There is no key with `--no-preview`.

**Sign flags.** Positive pitch must mean looking down, and the preview shows the value live. The signs were derived from the matrix maths and the live check showed sensible magnitudes, but a human pose could not be judged. If looking down shows a negative pitch, set `perception.invert_pitch: true` in `config/classpulse.yaml`. `invert_yaw` flips yaw; only its absolute value is used by the rules, so it is mostly cosmetic.

**Preview window.** The mirrored video with a banner in the dashboard state colour (Attentive `#1B5FD0`, Neutral `#9DB3D6`, Distracted `#D4622A`, Not visible `#C9CDD2`), relative pitch and yaw, eyes open or closed, the look-down allowance left, and a "Calibrating..." overlay. `q` quits and flushes the partial window.

**Demo script (act it out, about two minutes).** Start the worker, look at the screen through the 3 s calibration, then:

| Do this | Expect |
| --- | --- |
| Watch the screen | Mostly Attentive |
| Take notes at a normal rhythm (glance down and back up) | Mostly Neutral or Attentive |
| Hold a phone in your lap (head down) | Neutral for the allowance (8 s), then Distracted |
| Close your eyes | Attentive for 1.5 s, then Distracted |
| Put your head down on the desk with your shoulders still in view | Distracted |
| Leave the frame | Not visible |

If the readings look off, run `python -m worker --student STU001 --camera CAM1 --debug --no-preview` to log pitch, yaw, eyes and state for every frame.

## Timetable, lectures and sessions

Every attention record is tied to a lecture: a teacher, a subject, a section and a time.

**Timetable.** An admin uploads a weekly CSV with the columns `day` (Mon to Sun), `start` and `end` (`HH:MM`), `room`, `section`, `subject`, `teacher`; see `samples/timetable.csv`. Names that do not exist yet are created. The upload is all or nothing: if any row is invalid, nothing changes and every bad row is reported by line number. Rows in the same room may not overlap.

**A new upload replaces the template of its own kind.** A normal upload replaces only the live (non-simulated) slots; an upload with `simulated=true` (what the simulator sends) replaces only the simulated ones, so the live timetable is never wiped by the simulator and the other way round. Slots that are identical to ones already stored are kept, so uploading the same file again changes nothing. Lectures already created keep working after a replace, because each lecture holds its own copy of room, teacher, subject and section.

**Time zone.** Timetable times are local wall-clock time in the zone named by the `CLASSPULSE_TIMEZONE` environment variable (default `Asia/Kolkata`, can go in `.env`). Records and lectures are stored in UTC, and the backend converts, including daylight saving.

**Rooms and cameras.** Cameras belong to rooms. The seeded `CAM1` is in `R101`. Register another with `PUT /api/cameras/CAM2` and `{"room_id": "R202"}` (the room is created if new; add `"simulated": true` for simulated ones).

**Which lecture a record belongs to.** The backend takes the camera's room and the middle of the record's window (`window_start` plus half the window length), then picks:

1. a manual session in that room that covers that moment, else
2. the timetable slot of that room covering that weekday and local time, else
3. nothing: the record is stored with no lecture. It still shows in the student's live view, and teacher figures ignore it.

A lecture is one occurrence of a slot on a date, or one manual session. It is created by the first record that needs it and reused after that. A re-sent record keeps the lecture it already has; a record that was unassigned can gain one when it is re-sent after a timetable upload.

**Manual sessions** are started and stopped from the Admin view. While one is open it overrides the timetable for its room, and a room can have only one open session. A window counts as inside a session when its middle falls between the start and the stop.

## Student profile

The Student view shows one student's own figures; the backend answers from that student's records only.

**How the figures are worked out.** Every figure comes from summed seconds over the period shown, never from averaging window percentages. Attention is attentive / (attentive + distracted); presence is (attentive + neutral + distracted) / all recorded seconds. The functions are plain code in `backend/metrics.py` so later teacher figures reuse them.

**Visibility gate.** When presence over the period is below `scores.min_presence_ratio` in `config/classpulse.yaml` (0.5), attention for that period is "insufficient data": the API returns `null` with a `reason`, and the dashboard shows a dash or leaves the week blank.

**Attendance.** A lecture counts as attended when the time in view is at least 50% of the lecture's full scheduled length (start to end, or start to now for an open session). Time with no record counts as not present, and presence is capped at 100%. A student who belongs to a section (the simulated students do) is expected at every lecture of that section that has started, so a lecture they have no records in counts as not present. A student without a section, such as `STU001`, is listed only for lectures they have at least one record in. A lecture still running is measured against its full length, so it can read "so far".

**Which records count.** Records with no lecture count toward the overall, live, time-of-day and weekly figures, but not toward by-subject, by-teacher, attendance or the current-lecture stats.

**Time of day.** Attention by time of day groups windows by the local hour they start in (10:00 covers 10:00 to 10:59), in the backend's time zone. Weeks start on Monday, local time, and run from the first week with records to the current one; "this semester" means everything stored for the student.

**Layout.** The live part (header, four stat cards, last 15 minutes of records as stacked columns) refreshes every `dashboard.refresh_seconds`. The semester part (by subject, weekly trend, by teacher, by time of day, attendance) refreshes every minute.

## Teacher figures

The Teacher view gives a teacher feedback on their lectures, not a ranking. The figures are plain functions in `backend/teacher_figures.py` (the docstring there is the full definition), checked by `tests/test_teacher_figures.py` against hand-worked numbers. Records with no lecture are ignored throughout. Attention is worked from summed seconds with the visibility gate, as everywhere else: a student's time in a lecture counts only when they were in view for at least `scores.min_presence_ratio` of it.

**Students vs their own average (self-comparison).** For each student and lecture, the difference is the student's attention in that lecture minus their own attention over all their lectures (every teacher), in percentage points. A teacher's figure is the mean over their students of each student's mean difference across that teacher's lectures. This removes differences between students and between sections: a teacher of a strong section is not flattered, and a teacher of a weak one is not punished.

**After removing time-slot effect (slot-adjusted).** The slot is the lecture's local start time (such as `14:00`; a manual session uses its start hour). The slot effect is the mean difference over every student and lecture in that slot, across all teachers. The slot-adjusted figure is the self-comparison after subtracting each lecture's slot effect first.

**Weighting.** Not weighted by seconds. Seconds enter only inside "attention in this lecture" and "own average". Each lecture counts once for a student, however long they were in view, and each student counts once for the teacher, however many of the teacher's lectures they attended.

**Range.** Across the teacher's students, mean +/- z x sd / sqrt(n), with sd the sample standard deviation of the per-student means and z the normal quantile for `teachers.range_level` (1.96 for 0.95). A normal approximation: fine for tens of students, rough for a handful. One student gives no range. The number of students is returned with every figure and the number of lectures with the teacher.

**Hiding.** Until a teacher has `teachers.min_lectures` (5) lectures with records, both figures are `null` with the reason "fewer than 5 lectures". Both settings are in the `teachers:` section of `config/classpulse.yaml`.

**What this can and cannot do.** The self-comparison does not remove a time-slot penalty: a teacher who mostly teaches at a bad time of day is still marked down by it. The slot-adjusted figure takes the slot's average effect out, but it cannot separate a teacher from a slot they always teach in: if only one teacher ever teaches at 14:00, the 14:00 effect and that teacher's effect are the same number, and the adjustment removes both. The figures are feedback, not proof of a teacher's quality. They need several teachers sharing each slot, and the Teacher view says so under the Student groups card.

**On the simulated cohort** (4 weeks, 60 students, built-in effects in the Simulator section) the self-comparison leaves Dr. Anita Menon at about -1.8 points, because 4 of her 5 weekly lectures are at 14:00, which is built 12 points worse. After the slot adjustment she is at about +3.5 and the other four teachers are between -0.8 and -0.1. Of the +6 built in, the slot adjustment recovers about +3.5: the slot effect is estimated from lectures she teaches herself, so some of her advantage is taken out with it.

**Dashboard.** Pick a teacher in the sidebar. The header, four stat cards (self-comparison, slot-adjusted, marks vs own percentile, attendance), the in-lecture attention line for a chosen lecture (attentive / (attentive + distracted) over all students' summed seconds in each 30-second window), Recent lectures and Student groups. The marks card and the Student groups card show a plain placeholder until marks are uploaded (hooks: `marks_card` and `student_groups` in `dashboard/views/teacher.py`).

**Attendance.** A teacher's attendance is the students present (in view for at least half of a lecture's full length) over the students expected (the lecture's section size, or the students with records if it has no section), summed over their lectures.

## Simulator

The simulator fills ClassPulse with a realistic classroom history so the analytics have something to show. It is an ordinary client of the backend: it uploads its timetable through the timetable upload (`simulated=true`), registers cameras and students, stores the true effects, and sends attention records through `POST /api/records`. It never writes to the database. Only the data is artificial.

```powershell
python -m simulator seed                 # wipes the simulated data, then generates and sends a cohort
python -m simulator seed --small         # a week and 6 students per section, in seconds
python -m simulator seed --weeks 2 --seed 7 --backend-url http://127.0.0.1:8012
python -m simulator wipe                 # removes all simulated data
```

The defaults are in the `simulator:` section of `config/classpulse.yaml`: 2 sections (`SIM-A`, `SIM-B`) of 30 students (`SIM-A01` to `SIM-B30`), 5 teachers, 4 weeks of lectures ending today (weekdays, four 50-minute slots a day: 09:00, 10:00, 11:00, 14:00; rooms `SIM-R201`, `SIM-R202`, cameras `SIM-CAM-201`, `SIM-CAM-202`). The backend must be running. A full run is about 450,000 records and takes a few minutes; progress is printed. The same seed gives the same data.

**Built-in effects (the right answer).** The mean attention of a window is `student baseline + section effect + teacher effect + slot effect`, plus noise, with some neutral time, some not-visible windows and a few absences:

| Effect | Default | Which |
| --- | --- | --- |
| Better teacher | +6 points | Dr. Anita Menon (the first teacher); the other four are 0 |
| Worse slot | -12 points | 14:00 (afternoon); the morning slots are 0 |
| More attentive section | +5 points | `SIM-A`; `SIM-B` is 0 |

**Why the plain average misleads.** The timetable is built so that the plain class average ranks teachers wrongly: Dr. Anita Menon teaches 4 of her 5 weekly lectures at 14:00 (so she is punished), Prof. Rahul Bhat teaches only `SIM-A` and only in the mornings (so he is flattered), and the 14:00 slot is shared by four teachers on different days, so the slot effect can be told apart from any one teacher. A test (`tests/test_simulator_generation.py`) checks that the plain averages mis-order the teachers.

**Student archetypes** (`engaged`, `average`, `struggling` (attentive, but marks will be low), `disengaged`) set each student's baseline and how often they are absent or not visible. The archetype is stored on the student (`students.archetype`, returned by `GET /api/students`) for the marks simulator to read. The true effects are stored in the `simulation_effects` table and returned by `GET /api/simulation`.

**Marking in the dashboard.** Any view showing simulated data shows a banner ("Simulated data — generated cohort of 60 students over 4 weeks, not real classroom records", counts taken from `GET /api/simulation`). In the Student view the Register number box lists `STU001` and every simulated student; pick `SIM-A01` or type any register number.

**Before a demo, reseed.** The data ends on the day it was generated, so "the last 4 weeks" move on. Run `python -m simulator seed` on the day (it wipes the old simulation first), and `python -m simulator wipe` to take it all out. Live data (`STU001`, `R101`, `CAM1`, the live timetable and live records) is never touched by either.

## Pointing at another backend

Set `CLASSPULSE_BACKEND_URL` (in the environment, or in `.env`) to override `backend.url` in `config/classpulse.yaml` for the dashboard, the worker and the simulator, for example:

```powershell
$env:CLASSPULSE_BACKEND_URL = "http://127.0.0.1:8012"
streamlit run dashboard/app.py
```

The simulator's `--backend-url` option wins over it.

## Demo steps

1. Start the backend, then the dashboard, then the worker as above (the worker command is in the Run section).
2. In the dashboard sidebar, set View as to Admin.
3. Upload a timetable: under Timetable CSV choose `samples/timetable.csv`. A preview appears; press Replace timetable. The result message lists the slots and the teachers, subjects, rooms and sections created, or the rows that need fixing. To see the timetable pick up a lecture by itself, first edit a row in the CSV so its day and time cover the current local time in room `R101`.
4. Start a session: under Manual session choose room `R101`, a teacher, a subject and optionally a section, then press Start session. It appears under Manual sessions and overrides the timetable for `R101`.
5. Run the worker: `python -m worker --student STU001 --camera CAM1`. Sit in view for about 40 seconds. Switch View as to Student: the header names the lecture, and Recent lectures in the Admin view lists it.
6. Stop the session: press Stop next to it in the Admin view. New windows now follow the timetable, or have no lecture if no slot covers that time.

## API

Interactive docs are at http://127.0.0.1:8000/docs. Times in responses are UTC.

**Records**

- `POST /api/records` takes a JSON list of records:
  `{"camera_id": "CAM1", "student_id": "STU001", "window_start": "2026-01-01T10:00:00+00:00", "attentive": 20, "neutral": 0, "distracted": 0, "not_visible": 10}`.
  `window_start` must be timezone-aware. A repeat of the same `(student_id, window_start)` replaces the stored row and keeps its lecture. Returns `{"received", "inserted", "updated"}`. An unknown student or camera returns 404 and the whole batch is rejected.
- `GET /api/students/{register_no}/live?minutes=N` (default 10) returns `{"register_no", "minutes", "windows": [...]}`, oldest first. Each window has `window_start`, the four state seconds, `attention` (attentive / (attentive + distracted), or null), `presence` ((attentive + neutral + distracted) / total) and `lecture` (a lecture object as below, or null when unassigned). Unknown student returns 404.

**Student profile** (see Student profile above; a figure object is `{"value", "reason", "presence", "attentive", "neutral", "distracted", "not_visible"}` with `value` null and `reason` set to `no_records`, `low_presence` or `no_scored_time` when it cannot be shown)

- `GET /api/students/{register_no}/profile` returns `{"register_no", "timezone", "now", "min_presence_ratio", "overall": figure, "by_subject": [{"id", "name", "figure"}], "by_teacher": [...], "by_time_of_day": [{"hour", "label", "figure"}], "weekly": [{"week_start", "figure"}], "attendance": [{"lecture", "presence", "present", "in_progress", "figure"}], "month": {"label", "present", "total", "min_presence_ratio"}}`. Attendance is newest first. Unknown student returns 404.
- `GET /api/students/{register_no}/current` returns `{"register_no", "timezone", "now", "min_presence_ratio", "lecture", "attention_last_5_min": figure, "attention_average": figure, "presence_this_lecture": {"ratio", "visible_seconds", "elapsed_seconds"} or null, "distracted_this_lecture": {"seconds", "share_of_visible"} or null, "month"}`. `lecture` is the running lecture the student has records in, or null. "Now", "this month" and "last 5 minutes" come from the backend clock.

**Teacher feedback** (see Teacher figures above; times in UTC, figures in percentage points)

- `GET /api/teachers/{id}/feedback` returns `{"teacher_id", "teacher", "simulated", "timezone", "now", "min_lectures", "range_level", "n_lectures", "hidden", "reason", "self_comparison": {"value", "low", "high", "n_students"}, "slot_adjusted": {...}, "attendance": {"present", "expected", "rate"}, "lectures": [{"lecture", "present", "expected", "in_progress", "attention": figure}]}`. Lectures are the teacher's own, newest first; nothing about other teachers is returned. While `hidden` the figures are all `null` and `reason` is "fewer than 5 lectures". Unknown teacher returns 404.
- `GET /api/teachers/feedback` returns the same summary (without `lectures`) for every teacher, sorted by name, from one pass over the records. It is for the admin view.
- `GET /api/lectures/{id}/timeline` returns `{"lecture", "window_seconds", "points": [{"window", "offset_seconds", "students", "attentive", "distracted", "attention"}]}`: class attention per 30-second window counted from the lecture's start, only for windows that have records. Unknown lecture returns 404.

**Timetable**

- `POST /api/timetable` takes a multipart form with a CSV in the field `file` and an optional `simulated` field (`true` replaces and creates only simulated slots and entities; default `false` touches only live ones). Returns `{"applied", "slots_total", "slots_added", "slots_kept", "slots_removed", "created": {"teachers", "subjects", "rooms", "sections"}, "errors": [{"row", "message"}]}`. `applied` is false when any row is invalid. A file without the required columns, not UTF-8 or empty returns 422.
- `GET /api/timetable` returns `{"timezone", "slots": [{"id", "day", "day_index", "start", "end", "room", "section", "subject", "teacher"}]}` ordered by day and start.

**Students and simulation**

- `POST /api/students` takes `{"register_no", "name" (optional), "section" (optional name, created if new), "simulated" (default false), "archetype" (optional)}` and returns the student with 201 (409 if it exists). `GET /api/students` lists every student: `[{"register_no", "name", "section", "simulated", "archetype"}]`, live ones first.
- `PUT /api/simulation` takes `{"effects": [{"kind": "teacher" | "slot" | "section", "key", "value"}], "weeks", "seed"}` (values in attention points; keys are a teacher name, a slot start such as `14:00`, a section name) and replaces the stored effects. `GET /api/simulation` returns `null` when no simulation is loaded, else `{"students", "teachers", "sections", "rooms", "lectures", "records", "weeks", "seed", "effects", "archetypes"}`. `DELETE /api/simulation` removes all simulated records, lectures, slots, students, teachers, subjects, sections, rooms, cameras and effects (live data is untouched) and returns the counts removed.
- Profile, current and live answers, lectures, rooms, teachers, subjects, sections and timetable slots carry a `simulated` flag.

**Rooms, cameras and pickers**

- `GET /api/rooms` returns `[{"id", "cameras": [...]}]`.
- `PUT /api/cameras/{camera_id}` with `{"room_id"}` registers or moves a camera. Returns `{"id", "room_id", "camera_created", "room_created"}`.
- `GET /api/teachers`, `GET /api/subjects`, `GET /api/sections` return `[{"id", "name"}]` sorted by name.

**Lectures and sessions**

- A lecture object is `{"id", "source" ("timetable" or "manual"), "room_id", "teacher_id", "teacher", "subject_id", "subject", "section_id", "section", "start", "end", "open"}`. `end` is null and `open` is true while a manual session runs.
- `GET /api/lectures?limit=50&room_id=R101` lists lectures, newest first.
- `GET /api/sessions?open=true` lists manual sessions, newest first (`open=false` for stopped ones, omit for all).
- `POST /api/sessions` with `{"room_id", "teacher_id", "subject_id", "section_id" (optional)}` starts a session and returns it with 201. 404 for an unknown room, teacher, subject or section; 409 if the room already has an open session.
- `POST /api/sessions/{id}/stop` stops it and returns it. 404 if unknown, 409 if already stopped.

## Tests and type checking

```powershell
pytest                       # full suite
pytest tests/test_window_aggregator.py
pytest tests/test_dashboard_render.py   # dashboard checks, no backend or database needed
mypy                         # configured in pyproject.toml
```

The backend API tests run against `TEST_DATABASE_URL` and drop and recreate the tables in that database for each test, so point it at a database you do not mind wiping. If `TEST_DATABASE_URL` is not set they are skipped with a message.
