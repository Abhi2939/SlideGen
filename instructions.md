# SlideGen Project Handoff

This file records implementation rules and the verified state of the project. See `idea.md` for product scope, `context.md` for runtime/deployment details, `follow.md` for the client requirements, and `test.md` for outstanding checks.

## Status (2026-10-21)

- The project includes a CLI, FastAPI web app, browser UI, SQLite job queue, Groq generation pipeline, review/repair stages, and a deterministic PowerPoint renderer.
- Fourteen layouts are registered: the original ten plus `ask_allocation`, `market_snapshot`, `flow_steps`, and `quadrants`.
- Checkpoint commits through `19b51ee` include the four reference layouts and follow-up routing, fact-validation, investor-grounding, and renderer fixes.
- Latest local verification: 163 pytest tests passed; Ruff lint, Ruff format, and `git diff --check` passed.
- A fresh Acme Pay generation completed and was inspected. It included all four requested layouts, retained allocation values, and left missing market data and competitor names as placeholders. The four reference screenshots have been compared; broad compositions match, but full visual fidelity remains incomplete.
- Repeated current-code investor runs were blocked at outline generation by Groq daily token quota. The latest report showed 199,976/200,000 tokens used, requested 1,609, with an 11m24s cooldown. The latest successful investor output predates the comparative-claim guard; live behavior remains unverified. The public health endpoint and Render deployment remain unverified.
- Local Markdown handoff notes are included in the user's request to commit at each checkpoint. Keep generated artifacts, `.env`, `expected pics/`, and `golden/` out of commits.

## Start and verify locally

Keep `.env` private. Use `.env.example` as a template and configure `GROQ_API_KEY` for live generation.

```powershell
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python -m pytest
python -m ruff check .
python -m ruff format --check app tests
```

Open <http://127.0.0.1:8000> to use the local browser app. A generated web job is saved under `data/outputs/<job-id>.pptx`; files already in `outputs/` may be old CLI outputs. The reference-layout fixture can be rendered without an LLM to `outputs/reference-layouts-check.pptx`, but that fixture is only a renderer smoke check and not a client prompt result.

## Implementation rules

- `app.core.deck_schema.Deck` is the contract between generation and rendering.
- Keep rendering deterministic and independent of provider calls.
- Treat the original prompt and assigned facts as evidence. A seed-investor audience alone does not establish product features, integrations, workflows, pricing, traction, team credentials, valuation, runway, or a raise amount.
- Tier 2 qualitative structure (for example conceptual workflows and category headings) may be inferred when suitable. Do not invent Tier 3 specifics such as numeric values, named competitors, prices, traction, people, or funding terms; use explicit placeholders when needed.
- Automated tests must use fake LLM responses and must not make live provider calls.
- Keep provider SDK code under `app/llm/`; the current provider factory supports Groq only.
- The worker pool and rate limiter are in process. Production is configured for one instance and a mounted persistent data directory.
- The public app has no authentication. Rate limits and active-job capacity constrain usage but do not identify users.
- The deadline is cooperative between pipeline stages and cannot interrupt a provider call already in progress.
- Never print, log, copy, or commit secrets. Keep `.env`, `venv/`, `data/`, `outputs/`, generated presentations, `expected pics/`, and `golden/` out of commits.

## Deployment

`render.yaml` defines the Render Docker service, health check, persistent disk, Cloudflare client-IP handling, and job settings. It does not configure an access-code gate. The persistent disk requires a paid service; keep one instance because the queue, rate limiter, and SQLite store are local to that instance. The current live service status and deployed revision have not been confirmed.

CI runs pytest and Ruff on Python 3.11. The Docker image uses Python 3.12 slim. The owner has confirmed a local Docker build and server startup; successful generation in that image remains unverified.

## Client-readiness work still open

1. Compare the inspected Acme Pay/reference deck against all four images in `expected pics/` and tune remaining visual differences.
2. Obtain raster output and tune the renderers for visual fidelity, including the doughnut labels, market dashboard, eight-step arc, and quadrant layout.
3. After Groq quota resets, rerun the investor prompt against current code. Run Blush, sparse ?dogs,? and baseline prompts when provider quota allows; record outcomes in `test.md`.
4. Confirm the current Render deployment, health endpoint, and successful downloadable live jobs. Inspect Render logs for the prior API error if access is available.
5. Decide with the client whether missing numbers should be placeholders or labeled illustrative examples, and whether logos/photos/icons or original source prompts are required. Do not contact the client without the owner’s authorization.
6. Do not describe the client outcome as complete until representative generated decks have been inspected and the client checklist has evidence.

## Git checkpoints

Commit each completed checkpoint as requested. Before committing, run relevant checks, inspect the staged diff, and confirm `.env`, generated artifacts, `expected pics/`, and `golden/` are not staged. Do not push unless asked.
