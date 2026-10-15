# SlideGen Project Context

Use this file as the implementation handoff when resuming work. Never put API keys, access codes, or other secrets here.

## Current status (2026-10-15)

- The project provides a CLI, a FastAPI web app, an asynchronous SQLite-backed job queue, a browser UI, a Groq-powered generation pipeline, and a deterministic ten-layout PowerPoint renderer.
- The live Render URL is <https://slidegen-edz9.onrender.com/>. The repository includes a Render Blueprint and Docker deployment configuration.
- The API supports optional shared-code protection. The Render Blueprint enables it and asks Render to generate `SLIDEGEN_ACCESS_CODE`; the code must be read from Render's private environment settings and shared separately from the URL.
- Job state reports the current pipeline stage and queue position. Queued and running jobs are failed during startup recovery. The default job deadline is 900 seconds.
- Local verification on 2026-10-15: **105 tests passed**, `ruff check .` passed, `ruff format --check app tests` passed, and the project virtual environment's `pip check` found no broken requirements.
- Automated tests use fake provider responses. The real prompt checklist in `test.md` has not been run against the live Render service in this checkpoint. Do not describe live generation quality as verified.
- A `/healthz` request from this workspace timed out, so the live service's current availability and deployed configuration could not be confirmed here.
- Docker image build could not be verified in this environment because Docker Desktop's Linux engine was unavailable.
- `test.md` is a local checklist and is intentionally excluded from commits at the user's request.

## Service behavior

- `POST /api/jobs` accepts a prompt (5–4000 characters) and returns HTTP 202 with the job ID and status.
- `GET /api/jobs/{id}` returns status, current stage, queue position, and a user-safe error when failed.
- `GET /api/jobs/{id}/download` serves a completed `.pptx`.
- `GET /healthz` is the deployment health endpoint.
- `X-Access-Code` is required when `SLIDEGEN_REQUIRE_ACCESS_CODE=true`. The browser keeps the code in session storage and sends it with API calls.
- Active jobs are capped by `SLIDEGEN_MAX_ACTIVE_JOBS` (default 20). Submissions are limited to 10 per client IP per minute. Render is configured to use `CF-Connecting-IP` for that limit.
- The SQLite job database and files are stored under `SLIDEGEN_DATA_DIR` (default `./data`); expired jobs and files are removed after 24 hours by default.
- The queue and rate limiter are in process. Run one instance with its persistent disk attached.
- A job deadline is checked between pipeline stages and after rendering. It cannot interrupt a provider request that is already in flight; the worker can remain occupied until that request's network timeout.

## Local development

Configure `.env` from `.env.example`, set `GROQ_API_KEY`, and keep the file private. The provider factory supports Groq only; changing the provider name does not add another integration.

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python .\run_deck.py "Create a 6-slide product launch presentation"
python -m pytest
```

Install development dependencies with `python -m pip install -r requirements-dev.txt`. CI runs pytest and Ruff on Python 3.11.

## Deployment notes

- `render.yaml` defines a single Docker web service in Singapore, `/healthz`, and a 1 GB persistent disk at `/var/data`.
- The Blueprint requires `GROQ_API_KEY`, generates a shared access code, enables the access-code check, and configures a 900-second job deadline.
- When connecting an existing Render service, verify the Blueprint changes have been applied and confirm the access-code environment variable exists before sharing the app.
- Persistent disks require a paid Render service. Keep one instance because jobs, rate limits, and SQLite storage are local to that instance.
- Docker build verification remains outstanding because the local Docker Desktop Linux engine was unavailable.

## Client-readiness follow-up

Before representing the public deployment as fully verified, run the live checks in `test.md`: confirm `/healthz`, exercise the access-code flow, generate representative prompts, download/open the PowerPoint, and check prompt-specific output quality. Automated tests do not replace these checks. Do not commit `.env`, API keys, the shared access code, generated presentations, `venv/`, `data/`, or `outputs/`.
