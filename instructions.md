# SlideGen Project Handoff

This document describes the current implementation and practical development rules. For product context, see `idea.md`; for repository and deployment details, see `context.md`; for manual quality checks, see `test.md`.

## Current status (2026-10-15)

- The CLI, API, browser UI, SQLite job store, in-process runner, generation pipeline, and ten-layout PowerPoint renderer are implemented.
- The live URL is <https://slidegen-edz9.onrender.com/>. Render configuration enables shared access-code protection and persistent storage.
- Local checks: 105 tests passed; Ruff lint and format checks passed; the project virtual environment has no dependency conflicts.
- Tests mock LLM responses. Live generation, browser download, and inspection of generated output on Render are not confirmed by those results; use `test.md` for the live checklist.
- The live `/healthz` request timed out from this workspace, so deployment availability has not been confirmed.
- Docker build could not be verified locally because Docker Desktop's Linux engine was unavailable.
- `test.md` is excluded from commits as requested.

## Start and verify locally

Keep `.env` private and configure `GROQ_API_KEY` using `.env.example` as a template.

```powershell
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python .\run_deck.py "Create a 6-slide product launch presentation"
python -m pytest
python -m ruff check .
python -m ruff format --check app tests
```

Install test and lint dependencies with `python -m pip install -r requirements-dev.txt`.

## Implementation rules

- `app.core.deck_schema.Deck` is the contract between generation and rendering.
- Keep provider SDK code under `app/llm/`; the current provider factory supports Groq only.
- Keep rendering deterministic. The renderer should not call an LLM or depend on pipeline code.
- Automated tests should use fake LLM responses and must not make live provider calls.
- The worker pool and rate limiter are in process. Production is configured for one instance and a mounted persistent data directory.
- The access-code gate is enabled in Render through `SLIDEGEN_REQUIRE_ACCESS_CODE`; the browser sends `X-Access-Code` for protected API operations.
- The deadline is cooperative between pipeline stages. It cannot interrupt a provider call that is already in progress.
- Never print, log, copy, or commit secret values. Keep `.env`, `venv/`, `data/`, `outputs/`, and generated `.pptx` files out of commits.

## Deployment

`render.yaml` defines the Render Docker service, health check, disk, access-code enforcement, Cloudflare client-IP handling, and job settings. After applying Blueprint changes to an existing Render service, verify the required environment values and test the access-code flow before sharing the service. The persistent disk requires a paid service; keep one instance.

The local Docker image build was not verified because the Docker Desktop Linux engine was unavailable. CI runs on Python 3.11 and checks pytest plus Ruff; the Docker image uses Python 3.12 slim.

## Git checkpoints

The user requested work be split into reviewable checkpoints and pushed to GitHub. `test.md` must not be staged or committed. Stage explicit file paths, inspect the staged diff, and verify `test.md` is absent before each commit.
