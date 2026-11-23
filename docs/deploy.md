# Deploying Attentio on free tiers

This guide puts Attentio online without paying for anything:

| Part | Where it runs | Free tier |
| --- | --- | --- |
| Database | Neon (PostgreSQL) | 1 GB storage, 100 compute-hours a month |
| Backend (FastAPI) | Render web service | Sleeps after 15 minutes idle, wakes in about a minute |
| Dashboard (Streamlit) | Streamlit Community Cloud | Free for public GitHub repositories |
| Worker (webcam) | Your laptop | The webcam never leaves it |

The worker sends only 30-second summaries to the backend over HTTPS. Nothing about the cloud is written in `config/attentio.yaml`: everything comes from environment variables, so the local setup in the README keeps working unchanged.

Do the steps in this order. Keep two values at hand, because they are used in several places:

- the **Neon connection string** (step 1), used by Render and by the one command in step 3;
- the **service key**: a long random value you make up. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. The backend (Render) and the worker (your laptop) must have the same one.

Never commit either value, and never paste them into a file that is in the repository.

## 1. Neon: the database

1. Sign up at https://neon.com and create a project.
2. Pick a region. Neon has no Mumbai region. The nearest to India is **Asia Pacific (Singapore), `aws-ap-southeast-1`**; Sydney is the other Asia Pacific one and is farther. Neon's other regions are in the US, Europe and South America. The region cannot be changed after the project is made.
3. Neon creates a database (called `neondb`) and a role for you. Attentio uses that database as it is: it does not need permission to create databases, and it never tries to when the database exists.
4. Click **Connect** in the Neon console. Turn **Connection pooling** off to get the direct connection (the host name has no `-pooler` in it), then copy the connection string. It looks like this:

   ```
   postgresql://USER:PASSWORD@ep-something.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

   Paste it exactly as it is, `?sslmode=...&channel_binding=...` included. Attentio accepts `postgres://` and `postgresql://` and switches to its PostgreSQL driver itself, so you do not edit anything. The pooled string should also work; the direct one is simply the safer choice for a single small backend.

Neon stops the database computer after 5 minutes without queries and starts it again on the next one, which takes a second or two. The backend checks each connection before using it, so that is invisible apart from a slightly slower first request.

## 2. Render: the backend

1. Push the repository to GitHub (this guide uses `GTX0849/Attentio`). The repository holds `render.yaml`, which describes the service.
2. Sign up at https://render.com. Choose **New**, then **Blueprint**, and connect the GitHub repository `GTX0849/Attentio`.
3. Render reads `render.yaml` and shows one free web service, `attentio-backend`, and asks for two values:
   - `DATABASE_URL`: the Neon connection string from step 1.
   - `ATTENTIO_SERVICE_KEY`: the service key.
4. Apply the Blueprint. Render installs `requirements-backend.txt` (only what the backend needs, no webcam libraries), then starts `python -m backend`.
5. When the deploy is live, open `https://attentio-backend.onrender.com/api/health`. It should show `{"status":"ok"}`. If the name `attentio-backend` was already taken, Render adds a suffix, so use the address it shows. Note the address: you need it in steps 4 and 5.

What the Blueprint sets, so you know what is going on:

- Start command `python -m backend`, with `ATTENTIO_HOST=0.0.0.0`. Without that the backend listens on loopback only, which is the right default on your own machine but unreachable from outside on Render. Render sets `PORT` itself and the backend uses it.
- Health check `/api/health`. It is public and makes no database call, and it leaks nothing: it only says the process is up. The backend does not start unless it could reach the database, so a green health check also means the database was reachable at start-up. The same address is a handy way to wake a sleeping service.
- Python 3.12, from `.python-version` in the repository root. Render's default for new services is newer (3.14), and the code is built and tested on 3.12.
- `ATTENTIO_TIMEZONE=Asia/Kolkata`, the time zone the timetable is written in. Change it in `render.yaml` if you are elsewhere.
- Region Singapore, near the Neon database. Delete the `region:` line in `render.yaml` if Render refuses it for a free service; any region works, only slower.

The first start creates the tables and the demo room, camera and student (`R101`, `CAM1`, `STU001`), as it does locally.

## 3. The first admin account

Nobody can sign in until an account exists, and the backend has no public sign-up. Make the first admin from your own machine, pointing the one command at the Neon database. The command creates the tables too, so it works even before the backend has started.

In PowerShell, in the project folder with the venv active, set the variable for this command only by setting it, running the command and then removing it:

```powershell
$env:DATABASE_URL = "postgresql://USER:PASSWORD@ep-something.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
python -m backend.accounts create --role admin --email admin@yourschool.edu
Remove-Item Env:DATABASE_URL
```

It asks for a password twice (it is not shown, and is stored only as a hash). A variable set like this exists only in that PowerShell window, so close the window when you are done, and it overrides any `DATABASE_URL` in your local `.env` while it is set. Do not put the Neon string in `.env` unless you want your local backend to use it too.

More accounts are made from the dashboard's admin view, or with the same command (`--role teacher --email ... --teacher "Dr Name"`, `--role student --email ... --student STU001`).

## 4. Streamlit Community Cloud: the dashboard

1. Sign up at https://share.streamlit.io with GitHub and choose **Create app**, then deploy from an existing repository.
2. Repository `GTX0849/Attentio`, branch `main`, main file path `dashboard/app.py`.
3. Under **Advanced settings**, pick **Python 3.12**, and paste into **Secrets**:

   ```toml
   ATTENTIO_BACKEND_URL = "https://attentio-backend.onrender.com"
   ATTENTIO_HTTP_TIMEOUT = "75"
   ```

   `.streamlit/secrets.toml.example` in the repository has the same two lines with placeholders. These are plain environment-style settings: the app copies them into the environment before it reads its config. `ATTENTIO_HTTP_TIMEOUT` is how many seconds the dashboard waits for the backend. 75 is longer than Render's wake-up time, so the first visit after a quiet spell waits instead of failing.
4. Deploy. Streamlit installs `dashboard/requirements.txt`, the requirements file next to the app (it takes precedence over the one in the repository root, which is the full development set and would pull in OpenCV and MediaPipe).
5. Open the app and sign in with the admin account from step 3.

Free Community Cloud is meant for public repositories. Whether a private repository is allowed on the free plan has changed over time, so check Streamlit's current plan page. If you keep the repository private and it is not allowed, either make it public (it contains no secrets, but read the privacy note below first) or run the dashboard on your own machine against the cloud backend:

```powershell
$env:ATTENTIO_BACKEND_URL = "https://attentio-backend.onrender.com"
$env:ATTENTIO_HTTP_TIMEOUT = "75"
streamlit run dashboard/app.py
```

## 5. The worker on your laptop

The worker needs the webcam, so it stays on your machine and sends its records to the deployed backend.

1. In your local `.env` (never committed) set:

   ```
   ATTENTIO_BACKEND_URL=https://attentio-backend.onrender.com
   ATTENTIO_SERVICE_KEY=the-same-key-you-gave-render
   ```

   Do not set `ATTENTIO_HTTP_TIMEOUT` for the worker. Its default of 3 seconds is deliberate: a send that fails (the backend is waking up, or the network dropped) leaves the records in the spool at `.spool/unsent.jsonl`, and they are retried every few seconds, so nothing is lost. A long timeout would only make the worker's sender wait.
2. Install what the worker needs: `pip install -r requirements-worker.txt` (or the full `requirements.txt`, which you already have if you followed the README).
3. Download the models and run it as in the README: `python -m worker --student STU001 --camera CAM1`.
4. If the backend is asleep, open `https://attentio-backend.onrender.com/api/health` in a browser first to wake it, then start the worker.

## 6. Simulated data

To see charts without a class, load the simulator's data into the cloud backend from your machine, with the same `ATTENTIO_BACKEND_URL` and `ATTENTIO_SERVICE_KEY` in `.env` (or set in the window):

```powershell
python -m simulator seed --small
```

Use `--small`. A full seed sends about 430,000 records to a free instance with very little CPU that may be asleep, and could take a very long time (and use up Render's free hours and Neon's compute hours). `--small` is a week and a few students per section. It also creates simulated student and teacher accounts and prints their shared password once; keep it. `python -m simulator wipe` removes it all again.

## Limits and privacy

- **Sleep.** Render's free service sleeps after 15 minutes without requests and takes up to about a minute to wake. It also gets 750 free instance hours a month, shared by all free services in the workspace. The dashboard's longer timeout and the worker's spool exist because of this. Nothing is kept on Render's disk (it is erased on every restart), which is fine because everything is in Neon.
- **Neon.** The free plan has 1 GB of storage and 100 compute-hours a month per project, and the compute suspends after 5 minutes idle. There are no real backups: only a short history window (about 6 hours) for undoing a mistake. If the data matters, export it yourself (`pg_dump`) on a schedule.
- **Simulated data only, for now.** Do not put real students' attention records on these services yet. Real use means personal data about identifiable people, stored on servers in Singapore and the United States, and that needs a lawful basis (consent), a decision on where the data may live, and the other obligations under India's Digital Personal Data Protection (DPDP) Act. Until that is settled with the institution, run the cloud deployment with simulated data and, for a real trial, keep the backend, database and dashboard on a machine the department controls. The demo's `STU001` is a placeholder, not a person.
- **Public sign-in page.** The dashboard and the backend are on the open internet. Use strong passwords, and a long random service key. The backend rate-limits sign-in attempts.
- **The repository stays secret-free.** `.env`, `.streamlit/secrets.toml` and any file with a connection string must stay out of git. Both are already in `.gitignore`.

## Troubleshooting

**The dashboard says "The backend is waking up (free hosting sleeps when idle). Refresh in a moment".** Render's free service is asleep, or the dashboard's wait ran out. Wait about a minute and refresh. Open `/api/health` on the backend to wake it. If it keeps happening, check that `ATTENTIO_BACKEND_URL` in the Streamlit secrets is right (https, no trailing path), that `ATTENTIO_HTTP_TIMEOUT` is set to about 75, and that the Render service shows as live.

**The worker logs `backend refused the records (401)` or `(403)`.** `ATTENTIO_SERVICE_KEY` is missing or wrong. It must be the same value in your local `.env` and in Render's environment settings. If you change it on Render, the service restarts. The records stay in the spool until the key is right. The simulator reports the same problem as "was refused".

**The worker logs `backend unreachable` for a minute after a quiet spell.** That is the service waking. The spool keeps the records and resends them.

**Render's log shows `DATABASE_URL is not set`.** The value was left empty when the Blueprint was created. Add it under the service's Environment tab and redeploy.

**`The database "..." does not exist and could not be created with this user`.** The name in the URL is not a database that exists on that server, and the user may not create one. On Neon, copy the connection string again from the console (it names the database Neon made) and check you did not edit the part after the last `/`.

**`password authentication failed`, or `SSL`/`channel binding` errors.** The string was copied wrongly or the password was reset. Copy a fresh one from Neon's **Connect** dialog. Keep the whole `?sslmode=require&channel_binding=require` at the end.

**`No module named 'psycopg2'`.** Something is using an old driver name. Attentio changes `postgres://` and `postgresql://` to its own driver, so this means a different tool is reading the string, or the string starts with something else such as `postgresql+psycopg2://`. Use the plain `postgresql://` form.

**Render says the service failed its health check.** Look at the deploy log first: the usual cause is a start-up error such as a bad `DATABASE_URL`. The health check passes only once the app is up, and the app needs the database to start.

**Streamlit Cloud installs OpenCV or fails to build.** It picked up the root `requirements.txt` instead of `dashboard/requirements.txt`. Check that the main file path is exactly `dashboard/app.py` and that the file `dashboard/requirements.txt` exists on the branch.

**`/docs` and the rest of the API.** They live at the same address: `https://attentio-backend.onrender.com/docs`. Everything except `/api/health` and signing in needs a token or the service key.
