# SlideGen: Review and Test Plan

Repo: https://github.com/Abhi2939/SlideGen
Live: https://slidegen-edz9.onrender.com/
Status updated: 2026-10-21

This file began as a review checklist. Since then, the repository has changed substantially. The original findings below are historical; check the current implementation and results in the README and `context.md`.

**Latest local verification (2026-10-21):** 162 tests passed; Ruff lint, Ruff format, and `git diff --check` passed. A fresh Acme Pay generation completed and was inspected: the deck used all four requested reference layouts, preserved all provided allocation values, and used placeholders for missing market metrics and competitor names. Full screenshot comparison remains incomplete, and the renderers omit some reference photography, logos, and icons. The current-code investor rerun was blocked at outline generation by Groq daily quota (200,000/200,000 tokens used). An earlier investor deck exposed unsupported comparison claims; the latest comparative-claim guard has not yet been verified by a successful live rerun. Prompt 18 is partial; prompt 2 is blocked pending quota reset and rerun.

**Historical observed live failure:** A seed-investor prompt reached final review but failed after two repair rounds. Review identified invented product features and workflow details, pricing, founder/advisor credentials, and a `$2M` funding ask. Later pipeline changes added stronger investor-prompt grounding and a placeholder fallback, but these changes have not been confirmed by a live rerun. Keep prompt 2's status blank until it passes against the current deployment.

This file is a local working checklist and must not be committed, as requested by the repository owner.

---

## 1. Client-readiness status

The code includes the core generation pipeline and four new reference-oriented layouts, with automated regression coverage. This does not establish that a live generated deck matches the client examples. The Render Blueprint configures startup recovery for queued/running jobs, job progress, queue and rate limits, and a cooperative job deadline. The web app has no login or shared access-code gate.

**Live client readiness remains unverified.** Confirm the deployed Blueprint settings, then generate and inspect representative `.pptx` files using the public app. A passing offline suite does not verify provider quotas, live output quality, or the current Render deployment.

---

## 2. Findings from code review

The initial review identified the issues below. Their current implementation status is recorded here; live behavior still requires the manual checks in section 4.

| Original finding | Current status |
|---|---|
| Queued jobs could remain stuck after restart. | Addressed: startup recovery marks queued and running jobs failed. Regression tests cover recovery. |
| API had no gate and rate limiting trusted spoofable forwarding data. | The app remains public without authentication. Render rate limiting is configured to use validated `CF-Connecting-IP`; confirm the current Blueprint and live service settings before relying on that behavior. |
| Prompt-specific grounding rules could cause false positives and the writer could echo deck instructions. | Improved: analyzer separates instructions from source facts; writer retries instruction echoes and checks unsupported email/URL and copied placeholder labels. The live prompt matrix below remains necessary to assess false positives. |
| Jobs had no useful stage/queue progress or overall time budget. | Addressed: job status reports stage and queue position; default deadline is 900 seconds. A provider request already in progress cannot be interrupted. |
| SQLite connections and journaling needed cleanup. | Addressed: store calls close connections and use WAL. |
| Test dependencies were in the production image; container ran as root. | Addressed: dev dependencies moved to `requirements-dev.txt`; Docker starts the app as a non-root user. The owner has confirmed a local image build and server startup. |
| Formatting and chart validation needed improvement. | Addressed: Ruff configuration/checks are included; pie/doughnut data validation rejects negative values or a non-positive total; chart rendering includes data labels. |
| Groq structured output could use a native schema response. | Open design item: current structured-output validation and bounded retry behavior remain in place. |
| LibreOffice visual QA in CI, screenshot/sample deck, and GitHub About metadata. | Not completed/verified in this checkpoint. The README has a live demo link, Linux/macOS setup, CI instructions, and limitations; About metadata is outside the repository docs. |

---

## 3. Historical observed result and implemented fixes

**Prompt:** `generate a 6 slides ppt for my electronics company and include charts and figures in it`

**Historical result: FAIL (content quality).** The generated file passed structural validation but had weak content, an empty section slide, only one requested visual, and echoed prompt instructions. That result predates the current fixes and has not yet been re-run against the live service.

| Slide | Title | Observed | Problem |
|-------|-------|----------|---------|
| 1 | Electronics Company Presentation | Title only | No subtitle |
| 2 | Company Overview | Section-divider layout, no body | Empty content slide |
| 3 | Product Portfolio | 2 generic bullets in a large blank area | Filler, poor layout use |
| 4 | Sales Performance | Line chart, 2022 vs 2023 by quarter, tagged "Illustrative data" | Only chart. No title, unit or data labels. Line chart is a weak choice. |
| 5 | Market Position | Bullets: "Create a 6-slide presentation for the electronics company." and "Include charts and figures throughout the deck." | **Prompt echoed as content** |
| 6 | Thank You | Closing | Fine |

The current pipeline treats format directions as instructions rather than source facts, detects/retries imperative instruction echo, rejects empty section subtitles, avoids using the entire prompt as an outline key point, and requires additional visual coverage when charts or figures are requested. Chart output has data labels and stricter pie/doughnut value validation.

### Original recommendations and status

1. Separate instructions from source facts — implemented.
2. Detect/retry echoed instructions — implemented; live quality retest pending.
3. Handle sparse prompts without inventing unsupported specifics — safeguards improved; representative live outputs still need review.
4. Require meaningful section-slide content — section subtitles are required; live quality retest pending.
5. Require multiple visuals when charts/figures are explicitly requested — implemented in outline validation; live quality retest pending.
6. Improve charts — data labels and pie/doughnut validation implemented; chart titles/units depend on generated content and should be checked in real PowerPoint.

---

## 4. Test prompts

Run on the live site and open each downloaded result in real PowerPoint. Update a status cell only after performing that check. Confirm the live service is healthy and its deployed settings match `render.yaml` first. The app has no login or access-code flow.

### Baseline

| # | Prompt | Expect | Status |
|---|--------|--------|--------|
| 1 | `Create a 6-slide product launch presentation for small business owners` | Valid 6-slide deck | |
| 2 | `Create an 8-slide investor pitch for a B2B SaaS startup that automates invoice reconciliation for accounting firms. Audience: seed investors.` | Job completes with 8 slides; no invented features, workflow, pricing, traction, team credentials, or funding amount. Unsupported topics use clear placeholders. | Blocked: latest run stopped at outline on Groq daily quota; prior successful output had unsupported comparisons before the latest guard. |

### Grounding false positives (most likely to fail)

| # | Prompt | Expect | Status |
|---|--------|--------|--------|
| 3 | `Startup name: SwiftCrate. We ship refrigerated groceries to apartments in Bangalore within 30 minutes. Customers order through our website and app. Make a 5-slide deck.` | Deck generated. Words "ship", "website", "delivery" are in the prompt, so they should not be rejected. Also try without mentioning shipping or the website. | |
| 4 | `Create a 6-slide deck introducing our AI-powered resume screening tool to HR managers.` | No failure from the "AI" pattern | |
| 5 | `Create a deck on why customers often struggle to find affordable housing, for a city council briefing.` | No failure from the "customers often" or "finding ... difficult" patterns | |

### Layout coverage

| # | Prompt | Expect | Status |
|---|--------|--------|--------|
| 6 | `Create a 10-slide deck on renewable energy in India. Include a chart comparing solar, wind and hydro capacity, a process slide for installing rooftop solar, a stats slide, a comparison of solar vs wind, and a closing slide with contact info hello@example.com.` | Chart, process, stat, comparison and closing layouts. Email passes through. | |
| 7 | `Create a 5-slide deck with a quote slide from Steve Jobs about design, plus slides on our design philosophy.` | Quote not presented as real if generated (provenance) | |
| 18 | `Acme Pay: fintech for SMEs. Raising Rs. 14 Cr. Use of funds: Contingency 85.30%, Other indirect 6.94%, Team 3.02%, Marketing 2.57%, Direct 1.95%, Capex 0.22%. Steps: signs up, verifies KYC, gets funded. Include market, how it works, competitive landscape.` | Fresh multi-slide deck uses ask/allocation, market snapshot, flow steps, and quadrants. Missing market metrics and competitor names are not invented; inspect visual output against all four screenshots. | Partial: fresh deck generated and inspected; all four layouts and supplied allocations are present, with placeholders for missing metrics/names. Full screenshot comparison remains open. |
| 19 | `Create a concise presentation for a fintech payments product with slides for a funding allocation, market snapshot, how it works, and competitive landscape. Use placeholders for missing facts.` | All four reference-oriented layouts appear; placeholder handling is legible and the deck has no unsupported figures or named competitors. | |

### Hallucination guard

| # | Prompt | Expect | Status |
|---|--------|--------|--------|
| 8 | `Create a 5-slide deck for Acme Pay, a payments startup. Do not invent any statistics or customer names.` | No fabricated numbers on stat or chart slides | |

### Edge cases

| # | Prompt | Expect | Status |
|---|--------|--------|--------|
| 9 | `Make a deck about dogs` | Sensible output from a very sparse prompt (compare with the electronics failure) | |
| 20 | `My startup makes custom foundation, lipstick, and skincare products matched to skin tone, undertone, and style. Make a short presentation.` | A complete multi-slide deck; no one-step workflow, unsupported product capabilities, or fabricated statistics. | |
| 10 | `Create a 25-slide deck on the history of computing` | Near the 30-slide limit. Slow with one worker. | |
| 11 | ~3,500 characters of your own project notes plus "turn this into a 7-slide deck" | Faithful to the source, no overflow | |
| 12 | `Crée une présentation de 5 diapositives sur le changement climatique` | Non-English handled | |
| 13 | `generate a 6 slides ppt for my electronics company and include charts and figures in it` | Historical regression case; verify the echo, content, and visual-coverage fixes | |

### Abuse and limits

| # | Test | Expect | Status |
|---|------|--------|--------|
| 14 | Submit 3-4 prompts quickly from two tabs | Queued jobs complete. UI shows something useful while waiting. | |
| 15 | Prompt of 3 characters (`hi`) | Rejected (minimum is 5) | |
| 16 | 11 submissions within one minute | 429 on the 11th | |
| 17 | Redeploy while jobs are queued | Queued jobs not stuck (bug #1) | |

### Per-result checklist

- No text overflow or cut-off in any box
- Chart values match the request
- No invented emails, URLs or statistics
- No prompt text echoed as slide content
- No empty content slides
- Failure messages are understandable
- For prompt 2, no fabricated product, team, commercial, traction, or fundraising details; any missing investor inputs are clearly marked as placeholders
