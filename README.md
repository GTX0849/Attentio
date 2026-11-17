# ClassPulse

A laptop webcam worker classifies the person in view into attention states and sends one record per student per 30-second window to a FastAPI backend (PostgreSQL). A Streamlit dashboard reads only from the backend over HTTP.

The worker decides states with the rules in the `states:` section of `config/classpulse.yaml` (eyes-closed grace, look-down allowance, pitch and yaw bands). Until real head-pose readings land, it feeds those rules from a face detector only, so a visible face counts as Attentive and no face as Not visible.

## Layout

| Folder | What it holds |
| --- | --- |
| `shared/` | Config loader (`config.py`) and the attention record schema (`schema.py`) used by worker and backend |
| `worker/` | Webcam capture, window aggregation, spool and retry |
| `backend/` | FastAPI app, SQLAlchemy models, timetable and lecture logic, seeding |
| `dashboard/` | Streamlit app; one module per role in `dashboard/views/`, shared look in `dashboard/theme.py` |
| `.streamlit/` | Streamlit theme (colours and IBM Plex fonts) |
| `samples/` | `timetable.csv`, a sample weekly timetable for the demo |
| `simulator/` | Empty placeholder for a later ticket |
| `tests/` | pytest suite |
| `config/classpulse.yaml` | The one shared config file (fps, window length, backend URL, refresh) |

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

Options: `--no-preview` (no window, stop with Ctrl+C), `--camera-index N`, `--spool PATH`. Frames are never written to disk.

If a window cannot be sent (for example the backend is stopped), the worker appends it to `.spool/unsent.jsonl` and resends everything spooled with the next window and at start-up. Stopping and restarting the backend loses nothing.

## Timetable, lectures and sessions

Every attention record is tied to a lecture: a teacher, a subject, a section and a time.

**Timetable.** An admin uploads a weekly CSV with the columns `day` (Mon to Sun), `start` and `end` (`HH:MM`), `room`, `section`, `subject`, `teacher`; see `samples/timetable.csv`. Names that do not exist yet are created. The upload is all or nothing: if any row is invalid, nothing changes and every bad row is reported by line number. Rows in the same room may not overlap.

**A new upload replaces the whole template.** Slots that are identical to ones already stored are kept, so uploading the same file again changes nothing. Lectures already created keep working after a replace, because each lecture holds its own copy of room, teacher, subject and section.

**Time zone.** Timetable times are local wall-clock time in the zone named by the `CLASSPULSE_TIMEZONE` environment variable (default `Asia/Kolkata`, can go in `.env`). Records and lectures are stored in UTC, and the backend converts, including daylight saving.

**Rooms and cameras.** Cameras belong to rooms. The seeded `CAM1` is in `R101`. Register another with `PUT /api/cameras/CAM2` and `{"room_id": "R202"}` (the room is created if new).

**Which lecture a record belongs to.** The backend takes the camera's room and the middle of the record's window (`window_start` plus half the window length), then picks:

1. a manual session in that room that covers that moment, else
2. the timetable slot of that room covering that weekday and local time, else
3. nothing: the record is stored with no lecture. It still shows in the student's live view, and teacher figures ignore it.

A lecture is one occurrence of a slot on a date, or one manual session. It is created by the first record that needs it and reused after that. A re-sent record keeps the lecture it already has; a record that was unassigned can gain one when it is re-sent after a timetable upload.

**Manual sessions** are started and stopped from the Admin view. While one is open it overrides the timetable for its room, and a room can have only one open session. A window counts as inside a session when its middle falls between the start and the stop.

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

**Timetable**

- `POST /api/timetable` takes a multipart form with a CSV in the field `file`. Returns `{"applied", "slots_total", "slots_added", "slots_kept", "slots_removed", "created": {"teachers", "subjects", "rooms", "sections"}, "errors": [{"row", "message"}]}`. `applied` is false when any row is invalid. A file without the required columns, not UTF-8 or empty returns 422.
- `GET /api/timetable` returns `{"timezone", "slots": [{"id", "day", "day_index", "start", "end", "room", "section", "subject", "teacher"}]}` ordered by day and start.

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
