# SlideGen: Prompt-to-PowerPoint Generator

## Product summary

SlideGen turns a short or detailed natural-language prompt into a reviewed, downloadable PowerPoint. The current product has a Python CLI and a minimal browser interface. Its live Render URL is <https://slidegen-edz9.onrender.com/>.

The intended scope is the core flow: prompt in, `.pptx` out. An in-browser editor, collaboration, per-user accounts, and image generation are not implemented.

## Current implementation

- **Pipeline:** analyze the request, plan slides, write typed slide content, review and repair findings, validate, and render.
- **Grounding:** user-provided facts remain the evidence source; the pipeline is designed to avoid unsupported details, prompt echoes, repeated facts, and unrequested contact information.
- **Rendering:** deterministic `python-pptx` output with ten layouts: title, section, bullets, two-column, stat, process, comparison, chart, quote, and closing.
- **Service:** FastAPI job endpoints, browser UI, SQLite persistence, in-process worker pool, progress reporting, queue and rate limits, retention cleanup, and a 900-second job deadline.
- **Provider:** Groq only, with configurable models for analysis, outlining, writing, and review. No external-source retrieval or citation system is implemented.
- **Deployment:** Docker and a Render Blueprint with one instance and a persistent disk. The Blueprint enables shared access-code protection.
- **Verification:** 105 automated tests passed locally on 2026-10-15; Ruff lint and formatting checks passed. The tests use mocked LLM output. Live prompt checks are tracked separately in `test.md` and remain to be completed.

## Product limits and operating assumptions

- A shared access code is a simple gate for job submission, status, and downloads. It is not per-user authentication or a user account system.
- The worker queue, rate limiter, and SQLite store are designed for one service instance.
- Generation depends on Groq availability, account limits, and model availability. The product does not independently verify factual claims against external sources.
- A job deadline cannot stop a provider request already in progress; that worker can remain busy until the provider client's network timeout.
- Visual quality and prompt coverage still require representative live generations and review of downloaded decks.

## Product decisions

1. Keep the deck schema as the contract between generation and rendering.
2. Keep rendering deterministic and independent of provider calls.
3. Preserve supplied facts and label invented chart or statistic values as illustrative, or omit them.
4. Validate structured output and use bounded retries and targeted repairs.
5. Keep the client flow focused on prompt submission, progress, and PowerPoint download.

## Remaining evaluation work

- Run the manual prompts and abuse/limit checks in `test.md` against the deployed service.
- Inspect representative downloaded decks in PowerPoint for layout, overflow, grounding, and prompt adherence.
- Revisit product scope only when client expectations require editing, accounts, images, citations, or another provider.
