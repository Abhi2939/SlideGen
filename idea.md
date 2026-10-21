# SlideGen: Prompt-to-PowerPoint Generator

## Product summary

SlideGen turns a natural-language prompt into a reviewed PowerPoint deck. The product has a Python CLI and a browser interface backed by a FastAPI service. Its intended core flow is prompt in, `.pptx` out.

The client requirement documented in `follow.md` is that generated decks follow the style and layouts of four supplied examples. The request is not satisfied by simply having similarly named renderers: prompts must route into those layouts and their generated output must be visually reviewed against the references.

## Current implementation

- **Pipeline:** analyze the request, plan slides, write typed slide content, review and repair findings, validate, and render.
- **Grounding:** supplied facts are the evidence source. Qualitative structure and conceptual workflows may be inferred; unsupported specific numbers, prices, named competitors, traction, team claims, and funding terms must not be invented. Placeholders are used for missing specifics.
- **Rendering:** deterministic `python-pptx` output with fourteen registered layouts: title, section, bullets, two-column, stat, process, comparison, chart, quote, closing, `ask_allocation`, `market_snapshot`, `flow_steps`, and `quadrants`.
- **Reference layouts:** the four newer layouts have schema, planning, writing, review, renderer, and fixture-test support. The fixture is deterministic and does not use the live LLM.
- **Service:** FastAPI job endpoints, browser UI, SQLite persistence, an in-process worker pool, progress reporting, queue/rate limits, retention cleanup, and a 900-second job deadline.
- **Provider:** Groq only, with configurable models for analysis, outlining, writing, and review. There is no external-source retrieval or citation system.
- **Deployment:** Docker and a Render Blueprint with one instance and a persistent disk. The web app is public without login or a shared access code.

## Verification state

The current checkpoint includes the four reference-specific layouts and follow-up grounding/routing fixes. The latest local run passed **162 tests**, Ruff lint, Ruff format, and `git diff --check`. This confirms contracts and fixture behavior, not all live LLM behavior.

A fresh Acme Pay deck was generated and inspected; it included the four layouts and preserved the supplied allocations while leaving unsupported market metrics and competitor names as placeholders. Full screenshot comparison remains open, and the renderer omits some reference photography, logos, and icons. A fresh investor run is blocked by the Groq daily token cap; an earlier output exposed comparative claims that the latest code now guards against. The fixed output still needs live verification. The public health check and Render deployment remain unverified. See `test.md` and `follow.md` for evidence still needed.

## Product limits and operating assumptions

- The public interface has no login or per-user identity. Rate limits and queue capacity are operational controls, not authentication.
- The worker queue, rate limiter, and SQLite store are designed for one service instance.
- Generation depends on Groq availability, account limits, and model availability. The product does not independently verify factual claims against external sources.
- A job deadline cannot stop a provider request already in progress; that worker may remain occupied until the provider client's network timeout.
- The reference images include photography, logos, and icons that the current deterministic renderer does not reproduce. Whether those assets are required remains a client decision; visual fidelity beyond layout structure is not yet confirmed.

## Product decisions

1. Keep the deck schema as the contract between generation and rendering.
2. Keep rendering deterministic and independent of provider calls.
3. Preserve supplied facts. Never invent specific numbers or names; use placeholders unless the owner/client decides on clearly labeled illustrative values.
4. Validate structured output and use bounded retries and targeted repairs.
5. Keep the client flow focused on prompt submission, progress, and PowerPoint download.
6. Do not claim that the client request is complete until the real generated decks have been inspected against the examples.

## Remaining evaluation work

- Inspect the actual generated Acme Pay/reference prompt deck against all four screenshots.
- Improve and raster-check layout fidelity, including the doughnut callouts, overlapping market cards and table, curved eight-step process, and four-quadrant composition.
- Run the Blush, sparse “dogs,” baseline, and investor prompts and record results.
- Verify the Render deployment, `/healthz`, live job completion, and downloadable PowerPoint.
- Confirm expectations for missing numeric inputs and any desired logos, icons, photos, or original example prompts before expanding the asset scope.
