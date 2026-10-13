# SlideGen Project Context

Use this file as the handoff when resuming work in this repository. Do not put API keys or other secret values here.

## Project goal

SlideGen turns a natural-language request into a structured presentation, reviews the generated content, and renders a downloadable PowerPoint. The service uses FastAPI, a SQLite job store, an in-process worker pool, the generation pipeline, and the deterministic renderer.

## Current repository state

- Branch: `main`. Use `git log -1` for the latest commit; Phase 1 was committed and pushed as `48411b4`.
- The CLI remains available as `run_deck.py`.
- The web app entry point is `app.main:app`; `app.app:app` is also available as a compatibility import.
- The API, SQLite job store, thread-pool runner, and browser page are implemented.
- Render deployment files are present: `Dockerfile`, `.dockerignore`, and `render.yaml`.
- Use `git status` and `git log -1` for the exact working tree and latest commit state.

## Implemented service

- `POST /api/jobs` accepts prompts from 5 to 4000 characters and returns HTTP 202 with the job ID and status.
- `GET /api/jobs/{id}` returns job ID, status, and a user-safe error when failed.
- `GET /api/jobs/{id}/download` serves completed `.pptx` files.
- `GET /healthz` returns `{"ok": true}`.
- Set `SLIDEGEN_ACCESS_CODE` to require `X-Access-Code`; downloads also accept `?code=` for browser links.
- Active jobs are capped by `SLIDEGEN_MAX_ACTIVE_JOBS` (default 20); submissions are limited to 10 per IP per minute.
- The SQLite store and generated files live under `SLIDEGEN_DATA_DIR` (default `./data`). Jobs and files expire after `SLIDEGEN_RETENTION_HOURS` (default 24).
- Interrupted `running` jobs are marked failed during startup recovery.
- `SLIDEGEN_MAX_JOBS` sets generation worker concurrency (default 1).

## Run locally (Windows PowerShell)

```powershell
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000`. Set `GROQ_API_KEY` in `.env`; use `.env.example` for all supported variables. Keep `.env` private.

The CLI still works:

```powershell
python .\run_deck.py "Create a 6-slide product launch presentation"
```

## Tests and verification

Run the full suite:

```powershell
python -m pytest
```

At the latest checkpoint, **66 tests passed** and `pip check` reported no broken requirements. Tests cover the schema, pipeline, renderer, job transitions and recovery, API flow, access control, queue limits, rate limiting, serving the browser page, and download persistence across app restart. Generation tests mock `generate_deck()` with `tests/fixtures/decks/all_layouts.json`.

Live Phase 0 runs recorded:

- Blush prompt: five slides in 7.28 seconds, with an opening, three content slides, and a closing.
- Product launch prompt: after an initial provider rate limit, retry succeeded in 26.26 seconds with six slides.
- A detailed prompt hit Groq's token rate limit and has not yet succeeded.
- Two local API attempts with the Blush prompt failed safely on model semantic validation (one missed an assigned verbatim fact; one review did not request regeneration for an affected slide). The earlier CLI Blush run succeeded. The UI returns a clean failure message in the unsuccessful API case.
- A fixture job's completed PowerPoint remained downloadable after restarting the local Uvicorn process (10 slides, 48,131 bytes).
- A live browser preview was unavailable because the computer-use session exposed no browser surfaces. The live Blush browser flow remains unverified.
- Render YAML parsed successfully. Docker image build could not run because the Docker Desktop Linux engine pipe was unavailable.

## Deployment

- `render.yaml` defines one Render Docker web service, a `/healthz` check, and a 1 GB persistent disk mounted at `/var/data`.
- Render persistent disks require a paid web service. Keep one service instance because the queue and rate limiter are in process and the SQLite file belongs to one disk.
- Set `GROQ_API_KEY` and a private `SLIDEGEN_ACCESS_CODE` in the host environment before sharing the URL.
- The Docker image uses Python 3.12 slim and starts one Uvicorn process on `${PORT:-8000}`.
- See the README Deploy section for the environment variable table, setup steps, and runtime limits.

## Remaining pre-share verification

1. Complete the Blush request through a live browser UI and confirm the resulting deck opens with a sensible structure. The local API currently has intermittent model semantic-validation failures.
2. Before sharing, create the host service, set the environment variables, deploy, run three prompts on the public URL, then send the URL and access code separately.

Do not send messages to the client. Do not commit API keys, `.env`, `venv/`, `outputs/`, `data/`, or generated `.pptx` files.

## Commit preference

Current instruction: work through the phases without waiting for per-phase approval, keep the checkpoints visible, and make the commit after the requested implementation and verification work is complete, then notify the user. The earlier handoff authorizes pushing approved commits to the existing GitHub repository.
