# ClassPulse

A laptop webcam worker classifies the person in view into attention states and sends one record per student per 30-second window to a FastAPI backend (PostgreSQL). A Streamlit dashboard reads only from the backend over HTTP.

This is the walking skeleton: webcam to live chart. For now the worker uses a temporary rule (face detected = Attentive, no face = Not visible).

## Layout

| Folder | What it holds |
| --- | --- |
| `shared/` | Config loader (`config.py`) and the attention record schema (`schema.py`) used by worker and backend |
| `worker/` | Webcam capture, window aggregation, spool and retry |
| `backend/` | FastAPI app, SQLAlchemy models, seeding |
| `dashboard/` | Streamlit app; one module per role in `dashboard/views/` |
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

## Demo steps for this slice

1. Start the backend, then the dashboard, then the worker as above.
2. Open the dashboard (http://localhost:8501). The sidebar role is Student and the register number is STU001.
3. Sit in view of the webcam for about 40 seconds. The first window appears when the current 30-second clock window ends, and the charts refresh every 10 seconds. Attention and presence show 100%.
4. Leave the frame for a minute. The next windows show presence dropping towards 0%, and attention shows no point when there was no attentive or distracted time.
5. Optional: stop the backend for a minute while the worker runs, then start it again. The spooled windows arrive and appear on the chart.

## API

- `POST /api/records` takes a JSON list of records:
  `{"camera_id": "CAM1", "student_id": "STU001", "window_start": "2026-01-01T10:00:00+00:00", "attentive": 20, "neutral": 0, "distracted": 0, "not_visible": 10}`.
  `window_start` must be timezone-aware. A repeat of the same `(student_id, window_start)` replaces the stored row. Returns `{"received", "inserted", "updated"}`. An unknown student or camera returns 404 and the whole batch is rejected.
- `GET /api/students/{register_no}/live?minutes=N` (default 10) returns `{"register_no", "minutes", "windows": [...]}`, oldest first. Each window has `window_start`, the four state seconds, `attention` (attentive / (attentive + distracted), or null) and `presence` ((attentive + neutral + distracted) / total). Unknown student returns 404.

## Tests and type checking

```powershell
pytest                       # full suite
pytest tests/test_window_aggregator.py
mypy                         # configured in pyproject.toml
```

The backend API tests run against `TEST_DATABASE_URL` and drop and recreate the tables in that database for each test, so point it at a database you do not mind wiping. If `TEST_DATABASE_URL` is not set they are skipped with a message.
