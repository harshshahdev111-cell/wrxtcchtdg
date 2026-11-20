# Run the project

## 1. Start the app with Docker

Run these commands from the project root in PowerShell. Start Docker Desktop first.

```powershell
# Only copy the example if you do not already have .env.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d --wait
```

- Chat: <http://127.0.0.1:8001/>
- API health: <http://127.0.0.1:8001/api/v1/health/>
- PostgreSQL: port `5440` on your computer; port `5432` inside Docker.
- Data stays in a Docker volume when the containers stop.
- Compose waits for a healthy database, runs migrations, then starts Gunicorn directly. If migrations fail, the server does not start.
- Times use India Standard Time, `Asia/Kolkata`.
- Compose also starts Redis, a Celery worker and one Beat scheduler for proactive cancellation checks. Redis has no public port; PostgreSQL holds the durable follow-up records.

## 2. Choose live or offline mode

Django has one shared settings file and three files for different ways of running the app:

| File in `backend/config/settings/` | What it does |
|---|---|
| `base.py` | Shared settings: database, apps, India time, AI and Celery |
| `development.py` | Settings for working locally; debug is on unless `.env` disables it |
| `production.py` | Settings used by Docker; debug is always off |
| `test.py` | Uses the offline model and runs queued tasks directly, without Redis |
| `__init__.py` | Marks this folder as a Python package |

The three environment files import `base.py`, then change only what they need. Tests still use PostgreSQL so database lock tests work correctly.

- `manage.py` uses development settings by default.
- Docker, ASGI, WSGI and Celery use production settings by default.
- The test commands below explicitly choose test settings with `--settings=config.settings.test`.
- You can also choose a settings file with the `DJANGO_SETTINGS_MODULE` environment variable.

Edit `.env`. Never commit or share that file.

| Mode | Settings | What the user types |
|---|---|---|
| Live AI | `LLM_BACKEND=live`, your `LLM_API_KEY`, compatible endpoint and model | Normal English |
| Offline demo | `LLM_BACKEND=demo` | A JSON plan inside the chat message |

The sample live endpoint is Groq, with LiteLLM model `groq/openai/gpt-oss-20b`. Restart the container after changing settings:

```powershell
docker compose up -d --force-recreate backend
```

An example offline message is:

```json
{"service":"plumber","area":"Indiranagar","budget":1500}
```

The app asks for the missing date range. Supply future dates, for example `{"date_range":["YYYY-MM-DD","YYYY-MM-DD"]}` with real values. Review the held quote, then click **Confirm booking**. Typing approval in a model response does not execute a booking.

## 3. Read an error safely

- AI timeout: retry the request.
- Booking outcome unknown: retry the **same confirmation**, keeping the same proposal.
- Changed provider, slot or price: request a new proposal and approve it again.
- Active execution claim: wait for it to finish or expire, then check the same confirmation.
- Slot conflict: propose another slot, then give fresh approval.

Never create a second booking just because the first response was lost.

## 4. Run tests

The full Linux suite, including actual ASGI concurrency, deadline and blocked-loop tests:

```powershell
docker compose exec -T backend python manage.py test --settings=config.settings.test chat_agent.tests --noinput
```

To regenerate the evaluation table from that run:

```powershell
docker compose run --rm --no-deps --volume "${PWD}/docs:/app/docs" backend python manage.py test --settings=config.settings.test chat_agent.tests --testrunner common.testing.EvalRunner --noinput
```

Run host and container tests one after the other. They use the same PostgreSQL test database. On Windows, three server checks and one static-serving check are skipped; all four run inside Linux Docker.

## 5. Open this documentation

Python packages are split into three files:

| File in `requirements/` | When to use it |
|---|---|
| `base.txt` | Packages the app needs in every environment |
| `development.txt` | Shared packages plus Black, Ruff and MkDocs |
| `production.txt` | Shared packages plus Gunicorn and its ASGI worker; Docker installs this file |

Both development and production files start with `-r base.txt`. This tells pip to install the shared packages first. You do not need to install both files to start Docker; the image installs its own packages.

For local documentation and code formatting, install development packages:

```powershell
.\.venv\Scripts\python -m pip install -r requirements/development.txt
.\.venv\Scripts\python -m mkdocs serve
```

Open <http://127.0.0.1:8002/>. This is a separate documentation server; the chat remains on port `8001`.

Build files for sharing, with broken links and warnings treated as errors:

```powershell
.\.venv\Scripts\python -m mkdocs build --strict
```

The output goes into `site/`, which is ignored by Git. MkDocs is a development dependency; it is not added to the backend runtime image.

## 6. Try proactive cancellation

1. Make and confirm a booking in the chat. Copy its booking ID from the trace result or the user's bookings endpoint.
2. Run this command with that ID. It changes the mock provider ledger only; it does not send a chat prompt or run the checker inline.

```powershell
docker compose exec -T backend python manage.py simulate_provider_cancellation BOOKING_ID
```

3. Leave the chat open. Beat sends a check every 10 seconds; the browser checks for saved updates every 5 seconds while idle.
4. A message explains the cancellation. A replacement uses the original request and current explicit preferences, excluding the cancelled provider.
5. Review and explicitly confirm the new proposal. The original approval cannot approve a replacement.

- No matching provider: the chat asks you to adjust requirements. It does not increase your budget.
- Another pending proposal or unresolved execution: notify once, then defer replacement until it is resolved.
- Repeated checks, repeated simulation and worker restarts reuse one follow-up record.
- Older bookings without a saved request ask for requirements rather than guessing.
- User-requested cancellation does not trigger replacement.
- Run one Beat scheduler. PostgreSQL row locks also protect overlapping checks. See [Celery's scheduling guidance](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html).
- These checks use local mock provider tables. Before using external APIs, replace the single database transaction with durable steps; do not add network calls inside it.

## Stop the app

```powershell
docker compose stop
```

This preserves the database. `docker compose down -v` deletes its volume; use that only when you intend to reset the data.
