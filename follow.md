# SlideGen: Client Follow-up Plan

Status date: 2026-10-21
Repository: <https://github.com/Abhi2939/SlideGen>
Live app: <https://slidegen-edz9.onrender.com/>

This is a project handoff. The user asked to commit everything at each checkpoint. Do not message the client unless the project owner explicitly authorizes it.

## Client request

The client said the live link did not work and clarified: “I need the generation to be as per examples shared.” The request is to generate complete decks whose layouts and style follow the supplied examples, not to paste the example images into a deck. They also asked for work to proceed as stated.

The reference screenshots are in the ignored local folder `expected pics/`. Four are reference slides (Ask and Allocation, Global FinTech Market, How it works, Competitive Landscape); two capture the client conversation.

## Honest project status

- **Four reference layout types:** implemented in schema, planner/writer/review contracts, and deterministic renderers: `ask_allocation`, `market_snapshot`, `flow_steps`, and `quadrants`.
- **Automated local checks:** latest checkpoint passes 163 pytest tests, Ruff lint/format, and `git diff --check`. Fixtures prove renderers can create editable PowerPoint, not complete visual similarity.
- **Visual match:** inspected the four reference screenshots against the generated Acme Pay slides and a PowerPoint render of the updated eight-stage workflow. The broad layouts are present; supplied data is represented correctly. Market values and competitor names are intentionally placeholders, five workflow stages are marked for completion, and reference photography/logos/icons are not reproduced. Detailed visual fidelity remains incomplete.
- **Prompt selection and content quality:** a fresh Acme Pay generation completed and was inspected. It used all four requested layouts, retained the supplied allocations, and used placeholders for absent market metrics and competitor names. The workflow renderer now expands to eight numbered stages, clearly marking stages absent from the prompt. The corrected investor run is blocked by Groq daily token quota. An earlier investor output contained unsupported comparative claims; the latest guard has not yet been verified live. Other prompts and short-prompt completeness remain unverified.
- **Live service:** unverified. The public `/healthz` endpoint was inaccessible from this workspace. Current Render revision, logs, API-key/quota errors, and live downloads have not been checked.
- **Client contact:** none sent. The sample message below is only a draft.

The previous fixture file `outputs/reference-layouts-check.pptx` is a renderer preview generated from fixed JSON, not an output from the prompt-to-deck pipeline. Older `.pptx` files in `outputs/` may predate the latest code. The web app writes jobs to `data/outputs/<job-id>.pptx` by default.

## Work phases

### Phase 1: Reference renderers and contract — implemented, needs visual review

- Add the four layout schemas and deterministic editable renderers.
- Route content types to layouts and preserve the rule that specific numeric values and unsourced competitor names must be supplied or clearly marked.
- Current code has automated schema/routing/render coverage. Raster QA and comparison remain open.

### Phase 2: Generation behavior — implemented, live result unverified

- Planner and writer prompts support the four layout types.
- Qualitative structure and conceptual workflow steps can be inferred; unsupported figures, prices, names, traction, team credentials, and funding terms must not be fabricated.
- Generate and inspect Acme Pay, Blush, “Make a deck about dogs,” baseline, and investor examples. Record actual outcomes in `test.md`.

### Phase 3: Visual quality and live operation — outstanding

- Produce raster previews for the generated Acme Pay deck and compare side by side with the four reference slides.
- Tune spacing, overlaps, fonts, labels, arc, table, timeline colors, and quadrants. Add missing visual assets only after clarifying whether the client expects them.
- Local Docker build and server startup were confirmed by the owner. Successful live investor generation is still blocked by Groq quota. Check Render deployment/logs, `/healthz`, job submission, and downloadable live jobs when access is available.

### Phase 4: Client closeout — not started

- Ask the client (through the owner) for the original prompts behind the examples and preferences for absent numeric data (placeholder or labeled illustrative), logos, icons, and photos.
- Only after successful live examples and review, prepare a client update. Do not send it without explicit owner authorization.

## Local verification already recorded

```powershell
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\ruff.exe check .
.\venv\Scripts\ruff.exe format --check app tests
git diff --check
```

Latest local verification: 163 tests passed; Ruff lint, format, and `git diff --check` passed. A fresh Acme Pay deck was generated and inspected, but complete screenshot comparison remains open. Fixtures use no live API calls.

## Suggested client-style prompt

Use this as a local/live generation check after configuring a valid `GROQ_API_KEY`:

```text
Acme Pay: fintech for SMEs. Raising Rs. 14 Cr. Use of funds: Contingency 85.30%, Other indirect 6.94%, Team 3.02%, Marketing 2.57%, Direct 1.95%, Capex 0.22%. Steps: signs up, verifies KYC, gets funded. Include market, how it works, competitive landscape.
```

This provides funding and allocation data, but does not provide defensible market statistics or competitor names; those must not be fabricated. Inspect the downloaded PowerPoint, not the fixed JSON fixture, when judging generation quality.

## Draft client update (do not send without authorization)

> I’ve added the four example-oriented slide structures and tightened how missing facts are handled. I’m still checking generated decks against your examples and the live link, so I’ll share it for you to try after those checks pass. If you have the original prompts behind the example slides, please share them; that will help match the output more closely.

## Repository handling

The user asked to commit everything at each checkpoint. Include project Markdown notes in reviewed checkpoints; keep `expected pics/`, `golden/`, credentials, and generated artifacts out of Git.
