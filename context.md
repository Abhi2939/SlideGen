# SlideGen Project Context

Use this as the implementation handoff when resuming work. Never put API keys or other secrets here. `follow.md` is the current client-readiness plan; `test.md` is the manual test record.

## Current checkpoint (2026-10-21)

- Branch `main` contains checkpoint commits through `19b51ee` (`Format QA render helper`).
- The project has a CLI, FastAPI web app, browser UI, SQLite-backed asynchronous jobs, Groq-powered generation pipeline, review/repair, and deterministic PowerPoint rendering.
- Fourteen layouts are registered: title, section, bullets, two-column, stat, process, comparison, chart, quote, closing, `ask_allocation`, `market_snapshot`, `flow_steps`, and `quadrants`.
- The four newer reference layouts are integrated into schema validation, planner routing, typed slide writing, review guidance, and renderer dispatch.
- Latest local verification passed 163 pytest tests, Ruff lint, Ruff format, and `git diff --check`.
- A fresh Acme Pay generation completed and was inspected against all four reference screenshots. It included all four reference layouts and preserved supplied allocations; missing market metrics and competitor names remained placeholders. The workflow renderer now expands shorter source workflows to an eight-stage arc with explicit `[Add verified step]` labels.
- Repeated current-code investor runs were blocked at outline generation by Groq daily token quota. The latest report showed 199,976/200,000 tokens used, requested 1,609, and estimated an 11m24s cooldown. No updated investor deck was produced. The public `/healthz` endpoint and Render deployment revision/logs also remain unverified.
- Current generated output quality has not been accepted by the client. Broad layout composition exists, but some reference styling/assets (photos, logos, icons) are not implemented and visual fidelity remains open.

## Service behavior

- `POST /api/jobs` accepts a prompt and returns HTTP 202 with a job ID and status.
- `GET /api/jobs/{id}` returns status, current stage, queue position, and a user-safe error on failure.
- `GET /api/jobs/{id}/download` serves a completed `.pptx`; `GET /healthz` is the health endpoint.
- Active jobs are capped by `SLIDEGEN_MAX_ACTIVE_JOBS` (default 20). Submissions are limited to 10 per client IP per minute. Render is configured to use validated `CF-Connecting-IP` for this limit.
- SQLite job data and generated web decks live under `SLIDEGEN_DATA_DIR` (default `./data`); records and files expire after 24 hours by default. Web output files are under `data/outputs/<job-id>.pptx` by default; the CLI default is `outputs/generated.pptx`.
- The queue and rate limiter are in process. Run one service instance with its persistent disk attached.
- The default job deadline is 900 seconds. It is checked between pipeline stages and after rendering; it cannot interrupt a provider request already in progress.
- Structured output has bounded retry handling for invalid responses. Quota and rate-limit errors are not retried by that structured-output loop.
- For unsupported claims, qualitative structure and conceptual workflows may be inferred; specific figures, prices, named competitors, traction, team credentials, and funding terms require prompt facts or explicit placeholders.

## Local development

Keep `.env` private. Configure it from `.env.example` and provide a valid `GROQ_API_KEY` for live generation. The provider factory supports Groq only.

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python -m pytest
python -m ruff check .
python -m ruff format --check app tests
```

Install development dependencies with `python -m pip install -r requirements-dev.txt`. The automated tests use fake provider responses and do not require a live API key.

## Deployment notes

- `render.yaml` defines one Docker web service, `/healthz`, and a 1 GB persistent disk at `/var/data`.
- The Blueprint requires `GROQ_API_KEY`, enables Cloudflare client-IP handling, and configures the 900-second deadline. It does not configure authentication.
- Persistent disks require a paid Render service. Keep one instance because jobs, rate limits, and SQLite storage are local to that instance.
- The current service's deployed commit and logs have not been confirmed. The public health check could not be fetched from this environment.
- Docker image build and server startup have been confirmed by the owner. Successful generation inside the image is still outstanding.

## Local-only generated artifacts

- `outputs/reference-layouts-check.pptx` is a deterministic fixture preview, not an LLM-generated client example.
- Existing PowerPoints in `outputs/` may be stale. Generate a new web job to create `data/outputs/<job-id>.pptx`, or pass an explicit path to `run_deck.py`.
- `expected pics/` contains the local reference screenshots. It is ignored by Git and must remain local.

## Git and client handling

- The user asked to commit everything at each checkpoint. Include project Markdown notes when they are part of a checkpoint; keep `.env`, generated artifacts, `expected pics/`, and `golden/` out of Git.
- Commit completed, reviewed checkpoints. Do not push unless asked.
- Do not contact the client without explicit authorization.
- Do not commit `.env`, credentials, generated presentations, `venv/`, `data/`, `outputs/`, screenshots, or raster QA artifacts.
