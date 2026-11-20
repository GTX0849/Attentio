# ClassPulse

A laptop webcam worker classifies the person in view into attention states and sends one record per student per 30-second window to a FastAPI backend (PostgreSQL). A Streamlit dashboard reads only from the backend over HTTP. People sign in with an email and a password, and the backend decides what each role may read (see Signing in).

The worker decides states with the rules in the `states:` section of `config/classpulse.yaml` (eyes-closed grace, look-down allowance, pitch and yaw bands, and the learned front region). It feeds those rules with real readings from MediaPipe (head pitch and yaw, eyes closed, shoulders in view); see Real readings below.

## Layout

| Folder | What it holds |
| --- | --- |
| `shared/` | Config loader (`config.py`) and the attention record schema (`schema.py`) used by worker and backend |
| `worker/` | Webcam capture, window aggregation, spool and retry |
| `backend/` | FastAPI app, SQLAlchemy models, timetable and lecture logic, seeding |
| `dashboard/` | Streamlit app; the sign-in page (`signin.py`), one module per role in `dashboard/views/`, shared look in `dashboard/theme.py` |
| `.streamlit/` | Streamlit theme (colours and IBM Plex fonts) |
| `samples/` | `timetable.csv`, a sample weekly timetable, and `marks.csv`, a sample marks file for the simulated cohort, for the demo |
| `simulator/` | Simulated classroom history: cohort, timetable and records generated and sent through the backend API (see Simulator) |
| `config/tuned/` | Per-camera tuned limits and charts made by `python -m worker.autotune` (the small limits files are committed and shared; the charts stay local; see Tuning the limits) |
| `tests/` | pytest suite |
| `config/classpulse.yaml` | The one shared config file (fps, window length, backend URL, refresh, the marks attention line, simulator sizes and effects) |

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

Also put a long random value in `.env` as `CLASSPULSE_SERVICE_KEY` (used by the worker and the simulator, see Signing in), for example the output of `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

You do not need to create the databases. When the backend (or the tests) start, a missing database is created by connecting to the server's `postgres` database with the same credentials. Tables are created automatically and the demo data is seeded: room `R101`, camera `CAM1`, student `STU001`. `.env` is git-ignored; never commit it.

Nobody can sign in until an account exists. After the backend has run once (or right away: the command creates the tables too), make the first admin:

```powershell
python -m backend.accounts create --role admin --email admin@yourschool.edu
```

It asks for the password twice. The rest of the accounts are in Signing in.

## Run

Use three terminals, each with the venv active, in the project root.

**Backend** (binds to 127.0.0.1:8000 only):

```powershell
python -m backend
```

Equivalent: `python -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8000`. API docs are at http://127.0.0.1:8000/docs.

**Dashboard** (open it and sign in with the account you made; there is no other page before that):

```powershell
streamlit run dashboard/app.py
```

**Worker** (needs a webcam and `CLASSPULSE_SERVICE_KEY` in `.env`, the same value the backend reads; press `q` in the preview window or Ctrl+C to stop):

```powershell
python -m worker --student STU001 --camera CAM1
```

Options: `--no-preview` (no window, stop with Ctrl+C), `--camera-index N`, `--spool PATH`, `--detector {mediapipe,haar}` (default `mediapipe`; `haar` is the old face-only fallback), `--debug` (log every reading). Frames are never written to disk. Download the models first (next section).

The worker runs four things at once: a capture thread reads the camera continuously, the preview shows every frame live (about 30 fps) with the latest state drawn on it, scoring samples the newest frame at `worker.target_fps` (10), and finished windows are sent in the background. A slow or stopped backend therefore never freezes the preview; unsent windows wait in the spool and are retried every 5 seconds.

If a window cannot be sent (for example the backend is stopped), the worker appends it to `.spool/unsent.jsonl` and resends everything spooled with the next window and at start-up. Stopping and restarting the backend loses nothing.

## Signing in

One sign-in page serves everyone: an email and a password. The role (student, teacher or admin) comes from the account, and the person lands in their own view. The role is checked by the backend on every request, not only by the dashboard, so another front end would get the same limits.

**Accounts.** An account has an email (stored lower-cased, unique ignoring case), a role, a password and a link: a student account to one register number, a teacher account to one teacher (the names the timetable upload created), an admin account to nothing. Passwords are hashed with scrypt and a random salt per account (standard library only) and compared in constant time; there is no password in the database, the logs or any response. Session tokens are random, only a SHA-256 of each is stored, and they expire after `auth.session_hours` (12) in `config/classpulse.yaml`.

**Create accounts** with the command line, which reads `DATABASE_URL` like the backend and asks for the password twice (a password is never an argument):

```powershell
python -m backend.accounts create --role admin --email admin@yourschool.edu
python -m backend.accounts create --role student --email asha@yourschool.edu --student STU001
python -m backend.accounts create --role teacher --email anita@yourschool.edu --teacher "Dr. Anita Menon"
python -m backend.accounts list
python -m backend.accounts reset-password --email asha@yourschool.edu
python -m backend.accounts delete --email asha@yourschool.edu
```

A student's register number must already exist (the worker or the simulator registers students, or an admin does with `POST /api/students`), and a teacher's name must match the timetable (upload it first). Passwords need at least 8 characters. An admin can also use Accounts at the bottom of the Admin view: list accounts by email, create one (role, link, initial password) and reset a password. Resetting a password ends that account's open sign-ins.

**Sign-in limits.** A wrong email or password always gives the same message ("Email or password is incorrect"), so it does not show which emails exist. After 5 failed attempts for one email inside 5 minutes (`auth.login_max_failures`, `auth.login_window_minutes`) the backend answers 429 for that email until the window has passed. The count is kept in the backend's memory. The dashboard keeps the token in the browser session's server-side state and never in the URL; reloading the page signs you out, and a session that ended shows "Your session ended. Sign in again".

**The service key.** The worker and the simulator are not people. They send `X-ClassPulse-Key: <CLASSPULSE_SERVICE_KEY>`, and the backend compares it in constant time. Put the same value in `.env` for the backend, the worker and the simulator. There is no default key: with none set, the machine endpoints accept only an admin token, the backend logs that at start-up, the worker logs a clear message and keeps its records in the spool, and the simulator stops with a message. The key opens only the machine endpoints below (and, for the simulator, creating simulated student and teacher accounts); it never opens a student's or teacher's view or the admin overview.

**Simulated demo accounts.** `python -m simulator seed` also creates a simulated account for every simulated teacher and student, on the reserved `classpulse.test` domain: `anita.menon@classpulse.test` for Dr. Anita Menon (first and last name, titles dropped) and `sim-a01@classpulse.test` for `SIM-A01`. They share one password: `CLASSPULSE_DEMO_PASSWORD` if set in `.env`, otherwise a random one made for each run. A generated password is printed once at the end of the run and written, with the emails, to the git-ignored `.spool/demo-credentials.txt`; no password is in the repository. `python -m simulator wipe` removes the accounts with the rest of the simulation.

**What each role can see.**

| Role | Can read | Cannot read |
| --- | --- | --- |
| Student | Their own live view, profile, current lecture and marks | Any other student; teacher figures; the admin overview; the list of students |
| Teacher | Their own feedback, lecture timelines, marks tests, student groups (with their students) and subjects; they upload their own marks | Other teachers' figures, lectures, tests or groups; any student's own live, profile, current or marks endpoints; the admin overview |
| Admin | The overview, every teacher's figures (alphabetical), the timetable, rooms, lectures, sessions (start and stop), the student list, accounts, simulation | A student's own endpoints (live, profile, current, marks), one teacher's own feedback, groups with students |

Shared lists: `GET /api/teachers`, `/api/subjects` and `/api/sections` are for teachers, admins and the service key, never a student; `GET /api/students`, `/api/lectures`, `/api/sessions`, `/api/rooms` and `/api/timetable` are for admins and the service key; `GET /api/simulation` is open to any signed-in role for the "simulated data" banner, but only an admin or the service key also gets the true effects and the archetypes.

## Real readings

**Download the models once** (about 9.5 MB; written to the git-ignored `models/` folder, files already present are skipped):

```powershell
python -m worker.models download
```

This fetches only the official MediaPipe `face_landmarker` and `pose_landmarker_lite` (float16, latest) from `storage.googleapis.com/mediapipe-models`. Without them `python -m worker` stops with a message telling you to run this.

**What MediaPipe 1.2.3 provides, and the route taken.** This version has only the Tasks API (no `mp.solutions`). FaceLandmarker there can output a facial transformation matrix (`output_facial_transformation_matrixes`) and blendshapes (`output_face_blendshapes`, including `eyeBlinkLeft` and `eyeBlinkRight`), so both are used and no `solvePnP` or eye-aspect-ratio code was needed:

- Head pitch and yaw come from the rotation part of the transformation matrix (the direction the face points).
- Eyes closed means both `eyeBlinkLeft` and `eyeBlinkRight` are at or above `perception.blink_threshold`. The raw value goes to the rules, which apply the 1.5 s grace.
- Shoulders come from PoseLandmarker (lite): both shoulder landmarks of the scored person's body need a visibility at or above `perception.shoulder_visibility_threshold` (see "Several people in view" for which body that is).
- Both models run in VIDEO mode on the unmirrored camera frame. Only the preview is mirrored.

**Calibration.** At launch, look at the teacher for `perception.calibration_seconds` (3 s). The mean raw pitch and yaw over that time (face frames only) become the zero, and every later reading is relative to it. While calibrating, readings still go to the state rules but carry pitch 0 and yaw 0 (looking at the teacher), so the first seconds count as Attentive. If nobody is in view, calibration waits for a face. Press the key in `perception.recalibrate_key` (`c`) in the preview window to calibrate again at any time. There is no key with `--no-preview`.

**Face quality numbers.** The first line of the preview's readout panel reads, for example, "In frame 100% - Size 18% - Steady +/-0.9 deg". It describes the scored face only, is shown whenever that face is in view, and is worked out from the landmark points alone (MediaPipe gives no detection confidence, so none is shown):

- **In frame**: the share of the face's landmark points whose position lies inside the picture. 100% means the whole face is in view; a head cut off by the edge gives less.
- **Size**: the face box (the extent of its landmark points) as a share of the picture width. A face far from the camera reads small.
- **Steady**: how much the head moves, as one standard deviation of the raw head pitch and of the raw head yaw over the last `perception.steady_window_s` (1 s); the larger of the two is shown. Raw means relative to the camera, so it works before and after calibration. Zero is a head held perfectly still.

**Calibration check.** Only while calibrating, the 3 s countdown (and the collection of samples) runs only while all of these hold. When one fails, the countdown pauses, the samples collected so far are thrown away (so the zero is made only from good frames and a full `calibration_seconds` of good frames is needed), and the yellow message names the first failing one, in this order:

| Message | The check | Config key (`perception:`) | Default |
| --- | --- | --- | --- |
| Another face is about as close as you | The scored face's area is at least this many times the area of every other face (or it is the only face) | `calibration_min_size_ratio` | 2.0 |
| Centre your face | In frame is at least this share (face cut off at an edge) | `calibration_min_in_frame` | 0.98 |
| Move closer | Size is at least this share of the picture width | `calibration_min_face_width` | 0.12 |
| Hold still | Steady is within +/- this many degrees | `calibration_max_wobble_deg` | 2.0 |

The white line under the message says what is wrong and what is needed, for example "Calibration paused - head moving +/-3.8 deg, needs under +/-2.0 deg for 3 s", "face is 8% of the picture width, needs 12%", "62% of your face is in the picture, needs 98%" or "your face is only 1.4x the area of the nearest other face, needs 2.0x". When everything holds it shows "Calibrating" with "Look at the teacher and hold still - 2.1 s left". If nobody is in view there is nothing to check and calibration waits for a face as before. The check is never run after calibration has finished: during the lecture a small, cut-off or fidgety face is scored normally and only the readout line shows it.

**The front region (state rules).** A student counts as Attentive while facing the front of the room in any natural way: looking up at a standing teacher, turning toward one who walks across the room, slouching, leaning, sitting up. Out of the box (the `states:` section of `config/classpulse.yaml`) Attentive runs from 30 degrees up (pitch -30) to 15 degrees down (pitch +15), and up to 35 degrees to either side (|yaw| at most 35). Looking further up or further sideways is Distracted, as is a long look down, turning well to the side or back, and closed eyes. These angles are measured from a *reference direction*, not from calibration alone:

- **Moving reference.** The median of head pitch and yaw over the last 120 s, learned all through the lecture, only from readings already inside the current front region (Attentive) and only while the face is present. It is capped at 15 degrees of pitch and 20 degrees of yaw from calibration, so it can follow a slow posture shift but never wander far. Effective pitch is pitch minus the reference pitch, and likewise for yaw.
- **Personal spread.** How much the head varies around the reference while Attentive: the 90th percentile (nearest rank) of the absolute effective angle, kept separately for pitch and yaw. It sets this student's extents: up extent = k x pitch spread, sideways extent = k x yaw spread, with k = 2.5. A fidgety student gets wider extents, a still one tighter, but always within a range anchored on the camera's limit: each student's range can tighten to 15 degrees or widen to 10 degrees beyond the camera's limit (`min_up_extent_deg`, `min_side_extent_deg`, `extent_headroom_deg`). The camera's limit is the tuned one when the camera has a tuned file (see "Tuning the limits") and the default above (30 up, 35 sideways) otherwise, so with the defaults the extents stay between 15 and 40 degrees (up) or 15 and 45 degrees (sideways), and a camera tuned to 25 up and 45 sideways gives 15 to 35 and 15 to 55. Tuning therefore bounds every student's learned range for the whole lecture, not just the first 30 s. Until 30 s of Attentive data are in the window, nothing is learned: the reference stays at calibration and the extents are the camera's limits.
- **Fixed downward rule, never learned.** Up to +15 degrees below the reference is Attentive; +15 to +38 is Neutral and uses the 8 s look-down allowance (refilled by 2 s of continuous Attentive); beyond +38 is Distracted. A phone held in the lap is never learned as "front", because only Attentive readings teach the reference.
- **Calibration is not learned.** The readings taken while the person is calibrating carry zero angles by construction, so they are still counted as Attentive but never teach the reference or the spread.
- **Limits.** A sudden, permanent jump of the head beyond the student's extents (a 20 degree change of posture in one move) is not learned, because those readings are not Attentive; only gradual shifts are followed. Recalibration (`reset_learning()`) clears the reference and extents.
- **Storage.** The learned profile lives in memory (`worker/front_region.py`, behind the `FrontRegionProfile` interface) and is discarded at the end of the session. Nothing is written to disk, and it never changes a camera's tuned limits (only labelled tuning does, see "Tuning the limits"). A persistent store may be added once consent and retention are designed.

Every number is in `states:` and `states.learning:` of `config/classpulse.yaml` (window, caps, k, minimum extents, headroom, data needed).

**Sign flags.** Positive pitch must mean looking down, and the preview shows the value live. The signs were derived from the matrix maths and the live check showed sensible magnitudes, but a human pose could not be judged. If looking down shows a negative pitch, set `perception.invert_pitch: true` in `config/classpulse.yaml`. `invert_yaw` flips yaw; only its absolute value is used by the rules, so it is mostly cosmetic.

**Several people in view.** The models look for up to `perception.max_faces` faces and `perception.max_poses` bodies per frame (both default 5; they are maximums, and each extra person costs processing time, so lower them on a slow machine). Only one person is scored, and the choice is made like this:

- **At calibration the worker locks onto the largest face** (the person nearest the camera) and follows it from frame to frame by box overlap. If a clearly larger face (1.5 times the area) shows up before calibration has finished, the lock moves to it and the calibration timer restarts. After calibration the lock never moves because of size.
- **It never switches to another face by itself.** If the locked face is missing for longer than `perception.face_lost_after_s` (1 s) the readings become face-absent under the usual rules (shorter dropouts keep the last reading). The lock is taken again when you press `c`, or when a face reappears where the locked one was last seen.
- **Shoulders come only from the body whose head lies inside the locked face's box**, so someone behind who has both shoulders in view does not count. With no locked face, shoulders count as not in view.
- Other faces are never scored, stored or sent. They only appear in the preview as "Ignored", and `--debug` logs just how many faces were seen.

**Speed.** Measured on the development laptop with a 640x480 webcam: with `max_faces` and `max_poses` both 5 and one or two people in view, scoring ran at about 8 to 10 readings a second (dipping once to 7.4); with both set to 1 it ran at about 13 to 15 readings a second. If it falls below 8 readings a second on your machine, lower the two maximums.

**Preview window.** The mirrored video with a banner across the top (the dashboard state colour: Attentive `#1B5FD0`, Neutral `#9DB3D6`, Distracted `#D4622A`, Not visible `#C9CDD2`; yellow `#FFD34D` while calibrating) showing the state on the left and "Faces 3 - tracking 1" on the right; a readout panel at bottom-left (the face quality line, relative pitch and yaw, eyes open or closed, the learned reference and front region, the look-down allowance left, and "No face" with the shoulder state when the face is lost); a key hints bar along the bottom; and, while calibrating, a message panel saying "Look at the teacher and hold still" with the seconds left (or the reason the countdown is paused, see "Calibration check"). Keys: `q` quits and flushes the partial window, `c` recalibrates (and clears the learned front region), `m` shows or hides the landmark overlay.

The **landmark overlay** is on by itself while calibrating and otherwise toggled with `m`. It draws a 2 px box and a "Tracked" tag on the scored face, in the state colour (yellow while calibrating); a grey box and an "Ignored" tag on every other face; on the tracked face only, contours for the face oval, eyebrows, eyes and lips, small dots for the irises, white dots with a coloured ring at the nose tip, chin, outer eye corners and mouth corners, an arrow from the nose tip (about 90 px) pointing where the head points, and both shoulder points joined by a line. The key-point dots (with their ring), the iris dots and the arrow are drawn at those sizes for a face at least 18% of the picture width and shrink in proportion for a smaller, more distant one (down to 0.35 of the design size, dots never below 2 px), so they do not cover a small face. Each line has a thin dark outline so it stays readable on real video. The face-mesh indices are in `worker/face_mesh.py`: nose tip 1, chin 152, eye outer corners 33 and 263, mouth corners 61 and 291, iris centres 468 and 473.

**Demo script (act it out, about two minutes).** Start the worker, look at the teacher through the 3 s calibration, then:

| Do this | Expect |
| --- | --- |
| Watch the screen | Mostly Attentive |
| Take notes at a normal rhythm (glance down and back up) | Mostly Neutral or Attentive |
| Hold a phone in your lap (head down) | Neutral for the allowance (8 s), then Distracted |
| Close your eyes | Attentive for 1.5 s, then Distracted |
| Put your head down on the desk with your shoulders still in view | Distracted |
| Leave the frame | Not visible |

If the readings look off, run `python -m worker --student STU001 --camera CAM1 --debug --no-preview` to log pitch, yaw, eyes and state for every frame.

## Tuning sessions

**For test sessions only.** Tune mode records what a person is doing next to their head angles, so the front-region limits can be tuned from real evidence. Never use it in a real lecture with students who have not agreed to it.

```
python -m worker --student STU001 --camera CAM1 --tune
```

The worker runs as usual (scoring, preview, records sent to the backend and the `q`, `c` and `m` keys are unchanged). The person testing then presses a number key in the preview window to say what they are doing; the label starts at 0:

| Key | Label |
| --- | --- |
| 1 | watching the teacher |
| 2 | taking notes |
| 3 | phone in lap |
| 4 | talking to a neighbour |
| 5 | eyes closed |
| 0 | nothing (the default) |

The preview shows a yellow "TUNE" chip under the state banner with the current label (for example "1 - Watching the teacher"), and the label keys on the left of the key hints bar. Without `--tune` the number keys do nothing and none of this is drawn. Tune mode needs the MediaPipe detector and the preview (without the preview there are no keys and every row gets label 0).

**What is saved.** Each run writes its own CSV file, `.spool/tuning/<camera id>/<UTC timestamp>.csv` (git-ignored; odd characters in the camera id become `_`). It has one row per scoring tick (about 10 a second), only while a face is locked and in view and calibration has finished, so there are no rows during calibration or when nobody is in view. The first line is `# classpulse tune v1 camera=<id>`, the second is the header, and the columns are `t` (epoch seconds), `label` (0 to 5), `pitch` and `yaw` (relative to calibration), `ref_pitch` and `ref_yaw` (the learned reference at that moment), `rel_pitch` and `rel_yaw` (pitch and yaw minus the reference), `eyes_closed` (0 or 1), `face_size_pct` and `state`. No names, student ids, images or other faces are saved, and the records sent to the backend are not changed. The full format is in the docstring of `worker/tuning.py`.

The file is written in small buffered steps (flushed about once a second and on exit), so it never slows the preview. When you quit with `q` or Ctrl+C the worker prints the file path and the number of rows per label, then tunes the camera from all its files (see "Tuning the limits" below). Delete or move the files of runs you do not want counted.

### Tuning the limits

As more people are recorded in tune mode, each camera's attention limits tune themselves from that evidence. Nothing else is needed: at the end of every `--tune` run the worker does it, and you can also run it by hand:

```
python -m worker.autotune CAM1              # tune camera CAM1 and write config/tuned/CAM1.yaml
python -m worker.autotune CAM1 --dry-run    # show the suggested limits and write nothing
```

**What it reads.** All the tune files of that camera in `.spool/tuning/<camera id>/`, not just the latest run. To leave a run out, delete or move its file. A file that cannot be read (damaged, or another format version) is skipped with a warning. Rows labelled 0 are ignored. Each file counts as one run (one `--tune` session); the number of people is not recorded, so the summary reports runs, not people.

**Minimum data.** Time is added up from the row timestamps, ignoring gaps over 2 s. At least 60 s of label 1 (watching the teacher), 30 s of label 2 (taking notes) and 30 s of label 3 (phone in lap) are needed. Below that nothing is changed and the summary says what is missing, for example "needs 22 s more of taking notes". The amounts are in the `tuning:` section of `config/classpulse.yaml`.

**What is tuned.** Pitch and yaw are the angles relative to the learned reference (`rel_pitch`, `rel_yaw`), as the state rules use them.

- **Up limit** (`pitch_attentive_min_deg`): the 95th percentile of how far up the head went while watching the teacher, plus a 3 degree margin, kept between 10 and 45 degrees. This is the up limit the adaptive front region is anchored on: each student's learned up extent can widen to 10 degrees beyond it (and tighten to 15), for the whole lecture.
- **Side limit** (`yaw_max_deg`): the 95th percentile of the sideways turn either way while watching the teacher, plus 3 degrees, kept between 10 and 50 degrees. The front region's sideways anchor, in the same way.
- **Notes/phone boundary** (`pitch_neutral_max_deg`): the pitch that best separates taking notes from phone in lap (the one with the best balanced accuracy, between the two groups' medians), kept between 20 and 60 degrees. If the two overlap so much that the balanced accuracy is under 70% (`tuning.min_separation`), the current value is kept and the summary says so.
- Not tuned: `pitch_attentive_max_deg` (+15), the look-down allowance and refill, and the eyes-closed grace.

**Reading the summary.** It prints the runs used, the minutes of each label, a table with the previous and new value of each limit, the percentiles behind the up and side limits, the notes/phone separation accuracy, and the paths of the file and chart. Previous means what the camera used before: its earlier tuned file, or the `states:` defaults.

**Reading the chart.** `config/tuned/<camera id>.html` (open it in a browser) plots every labelled row: sideways turn on the x axis, pitch on the y axis (up is up), coloured by label. The dashed lines are the limits chosen: the up limit (top), the two side limits and the notes/phone boundary (below). The watching-the-teacher dots should sit inside the up and side lines, and the notes dots should sit above the boundary and the phone dots below it. Dots outside the lines, or notes and phone mixed together, mean more or cleaner labelled data is needed.

**What is written.** `config/tuned/<camera id>.yaml` has the three values and, for people to read, when it was made, the runs, the minutes per label, the percentiles and the separation accuracy. Each new tune keeps the version it replaces in `config/tuned/history/<camera id>/` (the last 5 are kept). The limits file and its history are small and safe to share, so they are meant to be committed. The chart is local only and is not committed (`config/tuned/*.html` is git-ignored), because it holds up to 6000 raw head-angle points from the tuning runs; run `python -m worker.autotune` again to redraw it.

**How the worker uses it.** At start-up the worker loads `config/tuned/<camera id>.yaml` for its `--camera` id if there is one. Its values replace the up limit, side limit and notes/phone boundary of the `states:` section for that camera only; nothing else changes, and other cameras keep the defaults. It prints which it is using, for example `Using tuned limits for CAM1 from config\tuned\CAM1.yaml (made 2026-11-20): up 28.0, side 31.0, notes/phone boundary 41.0 degrees.` or `Using the default limits for CAM1 (no tuned file): up 30.0, side 35.0, notes/phone boundary 38.0 degrees.`

**Reverting.** Delete `config/tuned/<camera id>.yaml` to go back to the defaults. To undo one bad tune, copy the version you want from `config/tuned/history/<camera id>/` over `config/tuned/<camera id>.yaml`. To keep a run out of future tunes, delete or move its CSV. Then run `python -m worker.autotune <camera id>` again if you want a fresh tune.

**Lecture learning never writes these files.** The reference and spread the worker learns during a lecture live in memory and are discarded; only labelled tuning (this section) changes a camera's limits.

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

**Dashboard.** Sign in as the teacher. The header, four stat cards (self-comparison, slot-adjusted, marks vs own percentile, attendance), the in-lecture attention line for a chosen lecture (attentive / (attentive + distracted) over all students' summed seconds in each 30-second window), Recent lectures and Student groups. The marks card and the Student groups card read the marks the teacher uploads (see Marks) and show "No marks uploaded yet" until then.

**Attendance.** A teacher's attendance is the students present (in view for at least half of a lecture's full length) over the students expected (the lecture's section size, or the students with records if it has no section), summed over their lectures.

## Simulator

The simulator fills ClassPulse with a realistic classroom history so the analytics have something to show. It is an ordinary client of the backend: it uploads its timetable through the timetable upload (`simulated=true`), registers cameras and students, stores the true effects, and sends attention records through `POST /api/records`. It never writes to the database. Only the data is artificial.

```powershell
python -m simulator seed                 # wipes the simulated data, then generates and sends a cohort
python -m simulator seed --small         # a week and 6 students per section, in seconds
python -m simulator seed --weeks 2 --seed 7 --backend-url http://127.0.0.1:8012
python -m simulator wipe                 # removes all simulated data
```

The defaults are in the `simulator:` section of `config/classpulse.yaml`: 2 sections (`SIM-A`, `SIM-B`) of 30 students (`SIM-A01` to `SIM-B30`), 5 teachers, 4 weeks of lectures ending today (weekdays, four 50-minute slots a day: 09:00, 10:00, 11:00, 14:00; rooms `SIM-R201`, `SIM-R202`, cameras `SIM-CAM-201`, `SIM-CAM-202`). The backend must be running. A full run is about 450,000 records and takes a few minutes; progress is printed. The same seed gives the same data. Seeding also uploads the simulated marks through the marks endpoint (see Marks), and `wipe` removes them.

**Built-in effects (the right answer).** The mean attention of a window is `student baseline + section effect + teacher effect + slot effect`, plus noise, with some neutral time, some not-visible windows and a few absences:

| Effect | Default | Which |
| --- | --- | --- |
| Better teacher | +6 points | Dr. Anita Menon (the first teacher); the other four are 0 |
| Worse slot | -12 points | 14:00 (afternoon); the morning slots are 0 |
| More attentive section | +5 points | `SIM-A`; `SIM-B` is 0 |

**Why the plain average misleads.** The timetable is built so that the plain class average ranks teachers wrongly: Dr. Anita Menon teaches 4 of her 5 weekly lectures at 14:00 (so she is punished), Prof. Rahul Bhat teaches only `SIM-A` and only in the mornings (so he is flattered), and the 14:00 slot is shared by four teachers on different days, so the slot effect can be told apart from any one teacher. A test (`tests/test_simulator_generation.py`) checks that the plain averages mis-order the teachers.

**Student archetypes** (`engaged`, `average`, `struggling` (attentive, but marks will be low), `disengaged`) set each student's baseline and how often they are absent or not visible. The archetype is stored on the student (`students.archetype`, returned by `GET /api/students`) and is read by the marks simulator (see Marks). The true effects are stored in the `simulation_effects` table and returned by `GET /api/simulation`.

**Marking in the dashboard.** Any view showing simulated data shows a banner ("Simulated data — generated cohort of 60 students over 4 weeks, not real classroom records", counts taken from `GET /api/simulation`). To look at a simulated student's view, sign in as that student (for example `sim-a01@classpulse.test`, see Signing in).

**Before a demo, reseed.** The data ends on the day it was generated, so "the last 4 weeks" move on. Run `python -m simulator seed` on the day (it wipes the old simulation first), and `python -m simulator wipe` to take it all out. Live data (`STU001`, `R101`, `CAM1`, the live timetable and live records) is never touched by either.

## Admin view

The Admin view opens with feedback for all teachers, then the timetable, manual sessions and recent lectures below it. It reads `GET /api/admin/overview`, which holds totals per teacher and per section and no figure for any individual student.

- **Attention figures with 95% ranges.** One row per teacher, alphabetical and never sorted by a score. Columns: lectures, the plain class average (attentive / (attentive + distracted) from the summed seconds of every record in the teacher's lectures, with the same presence gate; unassigned records are ignored), the self-comparison ("Vs own average") and the slot-adjusted figure as range bars from -10 to +10 points (pale bar is the range, dot is the figure), and marks (the marks figure in percentile points, a dash until the teacher has uploaded marks). Below `teachers.min_lectures` lectures the two figures are replaced by "Figures appear after 5 lectures"; the class average stays. With simulated data a Built-in column shows each teacher's true effect.
- **Attendance by section.** The present rate across each section's lectures and its size, then the number of unassigned record windows (records that matched no lecture).
- **Does each figure find the built-in best teacher?** Only with simulated data. For each method, the teacher with the highest value, marked "matches" when that is the teacher with the largest built-in effect and "wrong" when it is not. Teachers whose figures are hidden are not named.
- The slot-adjusted figure cannot separate a teacher from a slot they always teach in, and the page says so. The figures are feedback, not a ranking.

**Demo.** Reseed with `python -m simulator seed` (the data ends on the day it was generated), sign in as an admin, and read the comparison card. On the default cohort the plain class average and the self-comparison both put Prof. Rahul Bhat first (he teaches only the better section, in the mornings), and the slot-adjusted figure puts Dr. Anita Menon first, the teacher built to be best. On the small cohort (`seed --small`) the plain class average is also wrong and the slot-adjusted figure is right; the self-comparison is wrong there too.

## Marks

Teachers upload the marks of tests they already run, and ClassPulse links them to attention.

**File format.** A CSV (UTF-8) or Excel (.xlsx, first sheet) file with the columns `register_no`, `marks` and `max_marks`; see `samples/marks.csv`. Headers are matched ignoring case, spaces and punctuation, and a few variants are accepted (`Register No`, `Roll No`, `Marks Obtained`, `Score`, `Out of`, `Maximum Marks`, and so on). Other columns are ignored, blank lines are skipped, and a file that starts like a zip archive is read as Excel whatever its name. Register numbers match case-insensitively. A student who was away is simply left out of the file.

**Upload.** In the Teacher view (signed in as the teacher), use Upload marks in the sidebar: choose the file, the subject (the teacher's timetabled subject is preselected), the test date and, if you like, a test name such as "Mid-term 1", then press Upload marks. The test belongs to that teacher. Uploading the same teacher, subject, date and name again replaces that test's marks.

**The upload is all or nothing.** If any row is invalid (an empty or repeated register number, marks that are not numbers, below zero or above `max_marks`, a `max_marks` that is not above zero) or any register number is not a known student, nothing is stored and every problem is listed by line number. Percentiles depend on every row of a test, so storing part of a file would give wrong ones. A file that cannot be read as a whole (missing columns, empty, not UTF-8, a damaged workbook) is refused with a message.

**Percentiles.** A student's score is `marks / max_marks`, so tests out of 50 and out of 100 compare. Within a test, the percentile is the mid-rank percentile, 0 to 100: 100 x (students with a lower score + half the students with the same score, counting the student) / students in the test. Tied students share one value. Five distinct scores give 10, 30, 50, 70 and 90; one student gives 50; the percentiles of a test average exactly 50. The functions are in `backend/marks.py` and are tested against hand-worked numbers in `tests/test_marks_figures.py`.

**Covered lectures.** A test covers every lecture of its subject taught by its teacher to the sections of the students in the test, after the previous test of the same teacher and subject for that section (from the beginning when there is none) up to the test date. Dates are local calendar dates; a lecture on the day of a test counts for that test, and a test on the same date as another is a sibling, not its previous test. Manual sessions with no section are not covered.

**Student groups.** For a chosen test, each student's attention is attentive / (attentive + distracted) over their summed seconds in the covered lectures, with the usual visibility gate: a student in view for less than `scores.min_presence_ratio` of the recorded time, or with no records, is "insufficient data" and is not grouped. Attention "above 60%" means strictly above `marks.attention_line` in `config/classpulse.yaml` (0.60; exactly 60% is at or below), and "marks above the class median" means strictly above the median score of everybody in the test (a score equal to the median is at or below). Each student also gets an attention percentile among the students with a value, kept for reference; the groups use the attention value itself.

| | Marks above class median | Marks at or below median |
| --- | --- | --- |
| Attention above 60% | On track | Attentive but struggling |
| Attention at or below 60% | Knows it already | At risk |

The Student groups card shows the counts in this grid for the chosen test ("At risk" in the Distracted colour) and a "Students in each group" expander that lists the teacher's own students. The Admin view never asks for students.

**The marks figure.** For each student who sat the teacher's tests: the mean of their percentiles in the teacher's tests minus the mean of their percentiles in all the tests they sat (every teacher and subject, the teacher's own included). The figure is the mean of that over those students, with the 95% range from `figure_with_range` and the number of tests. It is shown as "+6 pctl" with "From 2 tests" in the Teacher view and as "+6" in the Marks column of the Admin view. It is hidden (null with the reason "no marks uploaded") for a teacher with no tests, and it does not wait for `teachers.min_lectures`.

*What this can and cannot do.* Percentiles are relative inside a test, so the percentiles of a test average 50 whatever the marks are. If every student sat every test, the figure is exactly zero, and a uniform lift of one teacher's marks changes nothing. The figure moves only when the students who sit one teacher's tests are not the ones who sit the others': students staying away from some tests, or a teacher whose tests are taken by a different mix of students (for example one section). It describes where a teacher's students rank against their own usual place; it is not a measure of how much they learned, and like the other figures it is feedback, not a ranking.

**Simulated marks.** `python -m simulator seed` uploads two tests for every teacher and section pairing (9 pairings, so 18 tests on the default cohort), named like "Mid-term 1 (SIM-A)", dated on the last lecture day of each half of the period, so after the lectures they cover. Test 1 is out of 50 and test 2 out of 100. A mark is the archetype's ability (engaged 82, average 62, struggling 38, disengaged 30, in percent), a personal offset, test noise and the teacher effect `simulator.marks.teacher_points` (8 marks points for the first teacher, Dr. Anita Menon, who is also the built-in best teacher for attention), all seeded. A student whose mark would fall below `skip_below` (35%) stays away from the test with probability `skip_chance` (0.9). That is how the teacher effect reaches the percentiles: fewer of the weaker students skip the better teacher's tests, which raises that teacher's figure. The marks are uploaded through the same endpoint with the simulated flag, and the wipe removes them. A test is also simulated whenever its teacher is.

On the default cohort the "struggling" students (attentive, low marks) land in Attentive but struggling in nearly every grouped test, the engaged ones mostly in On track and the disengaged ones mostly in At risk. The marks figure favours the built-in best teacher on every seed tried (30 of 30), by about +3 to +4 percentile points; on the small cohort (`seed --small`, 12 students) it is too noisy to say.

**Demo.**

1. Start the backend and the dashboard, and run `python -m simulator seed` (it also uploads the marks and creates the simulated accounts).
2. Sign in as `anita.menon@classpulse.test` with the demo password (Signing in). The Marks vs own percentile card shows the figure and "From N tests". The Student groups card shows the 2 x 2 grid for her newest test; use its picker to change test and open Students in each group to list the students.
3. To upload your own: in the sidebar choose `samples/marks.csv` under Marks file, check that Subject is Algorithms, choose a Test date and a name such as "Sample", and press Upload marks. A message says how many students were stored, or lists the rows to fix. The new test appears in the Student groups picker.
4. Sign out and sign in as an admin to see each teacher's marks figure in the Marks column (still no student), and as `sim-a01@classpulse.test` to see that student's own marks and percentile for each test. Without marks every view says "No marks yet".

## Pointing at another backend

Set `CLASSPULSE_BACKEND_URL` (in the environment, or in `.env`) to override `backend.url` in `config/classpulse.yaml` for the dashboard, the worker and the simulator, for example:

```powershell
$env:CLASSPULSE_BACKEND_URL = "http://127.0.0.1:8012"
streamlit run dashboard/app.py
```

The simulator's `--backend-url` option wins over it.

## Demo steps

1. Start the backend, then the dashboard, then the worker as above (the worker command is in the Run section). Make sure the accounts exist: an admin, and a student account for `STU001` (`python -m backend.accounts create --role student --email you@yourschool.edu --student STU001`).
2. Sign in to the dashboard as the admin.
3. Upload a timetable: under Timetable CSV choose `samples/timetable.csv`. A preview appears; press Replace timetable. The result message lists the slots and the teachers, subjects, rooms and sections created, or the rows that need fixing. To see the timetable pick up a lecture by itself, first edit a row in the CSV so its day and time cover the current local time in room `R101`.
4. Start a session: under Manual session choose room `R101`, a teacher, a subject and optionally a section, then press Start session. It appears under Manual sessions and overrides the timetable for `R101`.
5. Run the worker: `python -m worker --student STU001 --camera CAM1`. Sit in view for about 40 seconds. Sign out and sign in as the `STU001` student: the header names the lecture. As the admin, Recent lectures lists it.
6. Stop the session: press Stop next to it in the Admin view. New windows now follow the timetable, or have no lecture if no slot covers that time.

## API

Interactive docs are at http://127.0.0.1:8000/docs. Times in responses are UTC.

**Access.** Send `Authorization: Bearer <token>` (from `POST /api/auth/login`) on every call below. A missing, unknown, expired or revoked token is 401 (`WWW-Authenticate: Bearer`); a valid caller who may not use the endpoint is 403. "Student" endpoints (`/api/students/{register_no}/live|profile|current|marks`) are only for that student; "teacher" endpoints (`/api/teachers/{id}/feedback|marks/tests|subjects`, `/api/marks/tests/{id}/groups` and `/api/lectures/{id}/timeline` for the teacher's own test or lecture) only for that teacher; `GET /api/admin/overview`, `GET /api/teachers/feedback`, `POST /api/sessions` and `POST /api/sessions/{id}/stop` only for an admin; `POST /api/records`, `POST /api/timetable`, `GET /api/timetable`, `GET /api/rooms`, `PUT /api/cameras/{id}`, `GET` and `POST /api/students`, `GET /api/lectures`, `GET /api/sessions` and `PUT` and `DELETE /api/simulation` for an admin or the service key (`X-ClassPulse-Key`); `POST /api/marks/tests` for the teacher whose test it is, an admin or the service key. The full table is in Signing in.

**Sign-in and accounts**

- `POST /api/auth/login` takes `{"email", "password"}` and returns `{"token", "expires_at", "role", "email", "display_name", "student_register_no", "teacher_id"}` (the last two are null when they do not apply). 401 "Email or password is incorrect" for an unknown email and a wrong password alike; 429 "Too many attempts. Try again in a few minutes" (with `Retry-After`) after 5 failures for one email in 5 minutes.
- `GET /api/auth/me` returns `{"role", "email", "display_name", "student_register_no", "teacher_id"}` for the token. `POST /api/auth/logout` revokes the token (204).
- `GET /api/accounts` (admin) lists `[{"id", "email", "role", "student_register_no", "teacher_id", "linked_name", "created_at", "simulated"}]`; there is no password or hash in any response. `POST /api/accounts` (admin; the service key only for simulated student and teacher accounts) takes `{"email", "password", "role", "student_register_no", "teacher_id", "simulated"}` and returns 201 with the account (409 for a taken email or an already linked student or teacher, 422 for a bad email, short password or missing link). `POST /api/accounts/{id}/password` (admin) takes `{"password"}` and ends that account's sessions (204). `DELETE /api/accounts/{id}` (admin, not your own) returns 204.

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
- `GET /api/teachers/feedback` returns the same summary (without `lectures`) for every teacher, sorted by name, from one pass over the records. Every summary also carries `class_average`: the plain attention (0 to 1) from the summed seconds of all records in the teacher's lectures, with the presence gate, or `null`; it is kept while the figures are hidden.
- `GET /api/admin/overview` returns `{"timezone", "now", "min_lectures", "range_level", "teachers": [the summaries above], "sections": [{"section_id", "section", "simulated", "n_students", "n_lectures", "present", "expected", "rate"}], "unassigned_windows"}` from one pass over the records. Sections are sorted by name; `rate` is present over expected across the section's lectures (`null` with none); `unassigned_windows` counts records with no lecture. Nothing in it is about an individual student.
- `GET /api/lectures/{id}/timeline` returns `{"lecture", "window_seconds", "points": [{"window", "offset_seconds", "students", "attentive", "distracted", "attention"}]}`: class attention per 30-second window counted from the lecture's start, only for windows that have records. Unknown lecture returns 404.

**Timetable**

- `POST /api/timetable` takes a multipart form with a CSV in the field `file` and an optional `simulated` field (`true` replaces and creates only simulated slots and entities; default `false` touches only live ones). Returns `{"applied", "slots_total", "slots_added", "slots_kept", "slots_removed", "created": {"teachers", "subjects", "rooms", "sections"}, "errors": [{"row", "message"}]}`. `applied` is false when any row is invalid. A file without the required columns, not UTF-8 or empty returns 422.
- `GET /api/timetable` returns `{"timezone", "slots": [{"id", "day", "day_index", "start", "end", "room", "section", "subject", "teacher"}]}` ordered by day and start.

**Marks** (see Marks above)

- `POST /api/marks/tests` takes a multipart form with a CSV or .xlsx in the field `file`, `teacher_id`, `subject_id`, `test_date` (`YYYY-MM-DD`), an optional `name` and an optional `simulated` flag (a simulated teacher's test is simulated either way). Returns `{"applied", "test_id", "rows", "replaced", "errors": [{"row", "message"}]}`; `applied` is false, with every problem by line, when anything is wrong and nothing was stored. A file that cannot be read, or a bad date or name, returns 422; an unknown teacher or subject 404; a file over 1 MB 413.
- `GET /api/teachers/{id}/marks/tests` lists that teacher's tests, newest first: `[{"id", "name", "teacher_id", "teacher", "subject_id", "subject", "test_date", "simulated", "n_students", "sections"}]`.
- `GET /api/marks/tests/{id}/groups` returns `{"test", "attention_line", "median_score", "n_students", "n_covered_lectures", "counts": {"On track", "Attentive but struggling", "Knows it already", "At risk", "Insufficient data"}, "students"}`. `students` is null unless `?students=true`, which only the teacher's own view uses; each student has `register_no`, `name`, `section`, `marks`, `max_marks`, `marks_percentile`, `attention`, `attention_percentile`, `reason` and `group`.
- `GET /api/students/{register_no}/marks` returns `{"register_no", "simulated", "tests": [{"test_id", "name", "subject", "teacher", "test_date", "marks", "max_marks", "percentile", "n_students"}]}`, newest first, for that student only.
- Every teacher summary (`GET /api/teachers/{id}/feedback`, `GET /api/teachers/feedback`, `GET /api/admin/overview`) carries `marks`: `{"value", "low", "high", "n_students", "n_tests", "reason"}` in percentile points, with `value` null and `reason` "no marks uploaded" when the teacher has no tests.
- `DELETE /api/simulation` also removes simulated tests and the marks of simulated tests and simulated students, and simulated accounts (counts `tests`, `marks` and `accounts`).

**Students and simulation**

- `POST /api/students` takes `{"register_no", "name" (optional), "section" (optional name, created if new), "simulated" (default false), "archetype" (optional)}` and returns the student with 201 (409 if it exists). `GET /api/students` lists every student: `[{"register_no", "name", "section", "simulated", "archetype"}]`, live ones first.
- `PUT /api/simulation` takes `{"effects": [{"kind": "teacher" | "slot" | "section", "key", "value"}], "weeks", "seed"}` (values in attention points; keys are a teacher name, a slot start such as `14:00`, a section name) and replaces the stored effects. `GET /api/simulation` returns `null` when no simulation is loaded, else `{"students", "teachers", "sections", "rooms", "lectures", "records", "weeks", "seed", "effects", "archetypes"}`. `DELETE /api/simulation` removes all simulated records, lectures, slots, students, teachers, subjects, sections, rooms, cameras and effects (live data is untouched) and returns the counts removed.
- Profile, current and live answers, lectures, rooms, teachers, subjects, sections and timetable slots carry a `simulated` flag.

**Rooms, cameras and pickers**

- `GET /api/rooms` returns `[{"id", "cameras": [...]}]`.
- `PUT /api/cameras/{camera_id}` with `{"room_id"}` registers or moves a camera. Returns `{"id", "room_id", "camera_created", "room_created"}`.
- `GET /api/teachers`, `GET /api/subjects`, `GET /api/sections` return `[{"id", "name"}]` sorted by name. `GET /api/teachers/{id}/subjects` returns the subjects that teacher is timetabled for.

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
pytest tests/test_access_matrix.py      # who may call which endpoint, with sign-in enforced
mypy                         # configured in pyproject.toml
```

The logic tests build the backend with `auth_required=False` (every check is a no-op); the sign-in and access tests build it with `auth_required=True`, and `python -m backend` always does. The backend API tests run against `TEST_DATABASE_URL` and drop and recreate the tables in that database for each test, so point it at a database you do not mind wiping. If `TEST_DATABASE_URL` is not set they are skipped with a message.
