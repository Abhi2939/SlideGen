# SlideGen

SlideGen turns a natural-language prompt into a structured presentation, reviews the generated content, and renders a downloadable PowerPoint (`.pptx`) file. You can use the command-line interface or the FastAPI web app.

**Live demo:** [slidegen-edz9.onrender.com](https://slidegen-edz9.onrender.com/)

Implementation status (2026-10-23): reference styling now extends to generic layouts, and an optional cached asset stage resolves icons, prompt-named competitor logos, and Pexels photos before deterministic rendering. Content guards reject unsupported CTAs and allow fewer fuller slides when no exact count was requested. The original 260-test checkpoint is historical; current results and live limitations are recorded below. LibreOffice inspection does not replace real PowerPoint or client acceptance.

## Current project scope

- Generate a deck from a prompt with the Groq provider.
- Choose separate models for analysis, outlining, writing, and review.
- Write slides concurrently with a configurable worker limit.
- Review generated content and selectively regenerate slides with findings.
- Render fourteen layouts: title, section, bullets, two-column, stat, process, comparison, chart, quote, closing, ask/allocation, market snapshot, workflow steps, and quadrants.
- Resolve optional visual assets into the shared deck contract, with source notes and a disk cache; rendering never fetches URLs.
- Save the result as a `.pptx` file or submit an asynchronous web job and download it in the browser.

The web app stores jobs and generated files in SQLite and uses a bounded in-process worker pool. See [Deploy](#deploy) for a public service setup.

The web app is open to anyone who can reach its URL; it has no login or shared access-code gate. Job submission is limited by per-IP rate limits and the configured queue capacity.

## Requirements

- Python 3.11 or newer.
- A Groq API key.
- Internet access for LLM requests.
- For PNG visual QA only: LibreOffice and PyMuPDF.

## Setup (Windows PowerShell)

Run these commands from the project root:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

If PowerShell blocks virtual-environment activation, run the Python executable directly instead:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Configuration

Create `.env` from the example if you do not already have one:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set your Groq key. Keep the key private and do not commit `.env`:

```dotenv
GROQ_API_KEY=your_groq_api_key
SLIDEGEN_LLM_PROVIDER=groq
```

The application has model defaults, so the key and provider are enough to get started. Groq requests bypass system proxy environment variables by default; set `SLIDEGEN_USE_SYSTEM_PROXY=true` only if your network requires an HTTP proxy. The defaults are:

| Pipeline stage | Default model |
| --- | --- |
| Analyze | `openai/gpt-oss-20b` |
| Outline | `openai/gpt-oss-20b` |
| Write slides | `openai/gpt-oss-120b` |
| Review | `openai/gpt-oss-20b` |

You can override all stages with `SLIDEGEN_MODEL`, or override an individual stage with `SLIDEGEN_ANALYSIS_MODEL`, `SLIDEGEN_OUTLINE_MODEL`, `SLIDEGEN_WRITING_MODEL`, or `SLIDEGEN_REVIEW_MODEL`. `SLIDEGEN_STRUCTURED_ATTEMPTS` controls retries when provider output is invalid JSON or does not match its schema; it defaults to `2`.

Model availability can change. If a configured model ID is no longer enabled for your Groq account, update the corresponding variable in `.env` to an active model ID.

## Generate a presentation

With the virtual environment activated:

```powershell
python .\run_deck.py "Create a 6-slide product launch presentation for small business owners"
```

The default output is `outputs/generated.pptx`. Specify a different output path and theme with:

```powershell
python .\run_deck.py "Create a product launch plan" --output .\outputs\launch-plan.pptx --theme default
```

To see all CLI options:

```powershell
python .\run_deck.py --help
```

The CLI sends prompts to the configured LLM provider. Generation can fail if the API key is missing or invalid, a selected model is unavailable, the provider returns invalid structured output repeatedly, or the deck does not pass review after the configured repair rounds.

## Prompt handling

SlideGen can work from a short brand or product description. When the prompt does not specify an audience or deck type, analysis treats it as a general product introduction rather than assuming an investor pitch. Broad educational prompts target six to eight substantive slides; short company briefs target four to six, and the planner may use fewer while preserving every required fact. Explicit counts remain exact. Growth/roadmap content uses a visual workflow without fabricated dates or targets. Unsupported CTAs, benefit claims, numeric values and company specifics are rejected or replaced with explicit placeholders. A company-specific finished deck needs source facts; missing evidence must not be disguised as generated proof.

For investor pitches, asking for a seed-investor audience does not establish product features, integrations, workflows, pricing, traction, founder experience, valuation, runway, or a funding amount. The writer is instructed to use placeholders for those details when the prompt omits them. If the reviewer still finds high-severity unsupported claims after its rewrite rounds, the pipeline replaces those slides with explicit placeholders that preserve facts assigned to the slides, then reviews the deck again. Supply verified details for a more complete pitch.

Named beauty briefs use conservative source-name extraction: "Blush is a custom beauty brand offering foundation..." identifies Blush, not "Offering Foundation." Brand recognition selects the title and theme without replacing the analyzed fact inventory or forcing four slides. Beauty briefs use the normal outline stage; supplied launch channels and later growth activities need visible content slides and remain plans rather than achieved results. In one full Blush run the outline stage reported 2,608 tokens; this is one observation, not a general cost or savings estimate.

Required facts cannot be satisfied by speaker notes alone. Writer checks exclude notes before and after restoring verbatim facts, and the review payload contains visible slide fields only. Blush shares the reference family's Calibri, navy/green palette, white cards and title placement. Optional assets use the separate stage described below. Company workflow details inferred beyond source wording receive a visible conceptual-workflow label.

The slide writer also checks generated text for email addresses and URLs that were not supplied, and for copied metadata labels such as `Startup name`. A match triggers a retry; if the slide still contains it, generation fails rather than accepting that text.

Deck-format directions such as slide count and requested layouts are treated as instructions, not slide facts. The writer retries when generated copy repeats those directions or describes the deck's own slide and visual counts. If charts or figures are requested without numeric source data, charts show editable missing-data placeholders and do not plot invented example values. Illustrative figures require an explicit request for sample/illustrative data. The outline must include the chart and at least one additional visual slide.

## Run the web app locally

With the virtual environment activated and `.env` configured:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>. The job database and presentations are written under `SLIDEGEN_DATA_DIR` (default `./data`).

The API accepts `POST /api/jobs` with `{"prompt": "..."}`, returns an ID and status, and serves the current stage and queue position from `GET /api/jobs/{id}`. Download completed jobs from `GET /api/jobs/{id}/download`. `GET /healthz` is the public health check.

For a confirmed temporary per-minute limit, the app waits for Groq's numeric `retry-after` value (up to 60 seconds) and retries only the interrupted call, at most twice. Completed slides are retained, and the browser shows the waiting stage. Daily quotas, requests larger than the provider's limit, unknown limit types, missing/invalid wait times, and exhausted retries stop with "The AI provider's daily or rate limit was reached. Please try again later." Waits respect the job deadline. SDK retries remain disabled. Safe logs identify the stage, model, limit type, numeric limit/used/requested counts, and allowlisted remaining-budget headers without exposing raw errors, prompts, or credentials. Other failures show a generic error. Terminal job diagnostics retain allowlisted status/code/reason fields and review counts, without tracebacks, raw review copy, slide titles or request text. Slide scheduling submits only active work and stops submitting new slides after any failure; an already running request cannot be cancelled.

### Linux and macOS

From the project root, create and activate a virtual environment, then install runtime dependencies:

```bash
python3.11 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Set `GROQ_API_KEY` in `.env`, then start the server with:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Deploy

The repository includes a `Dockerfile` and a Render Blueprint in `render.yaml`. The Blueprint runs one Docker web service in Singapore, checks `/healthz`, and attaches a 1 GB persistent disk at `/var/data` for SQLite and completed presentations.

Live Render service: <https://slidegen-edz9.onrender.com/>.

### Deploy on Render

1. Push the repository to GitHub and connect it to Render.
2. In Render, create a **Blueprint** from this repository and review the service settings from `render.yaml`.
3. Set the required `GROQ_API_KEY` in the service's environment.
4. Apply the Blueprint and wait for `/healthz` to pass.
5. Open the service URL, submit a test prompt, and download the resulting PowerPoint.

For the current fixes, compare Render's deployed Git commit with `git rev-parse origin/main` after fetching the latest pushed checkpoints. Open `/healthz` and expect HTTP 200 with `{"ok": true}`. Check the actual service environment against `render.yaml`; these values are supplied by the Blueprint:

```dotenv
SLIDEGEN_LLM_PROVIDER=groq
SLIDEGEN_STRUCTURED_ATTEMPTS=2
SLIDEGEN_DATA_DIR=/var/data
SLIDEGEN_MAX_JOBS=1
```

Keep the required `GROQ_API_KEY` private, confirm a persistent disk is mounted at `/var/data`, and use one instance. Stage model variables are optional because the app has defaults; the Blueprint sets them explicitly. These configuration instructions do not establish the current deployed settings.

After confirming available Groq quota, retry the full Blush launch/growth prompt and check for a completed job, successful download, visible launch/growth content, and a counts-only stage token summary. The shorter Blush-style prompt in `scripts/live_smoke.py` does not include the launch/growth plan, so run the full regression prompt separately. Inspect the downloaded file in real PowerPoint; startup and HTTP 202 alone do not establish generation success.

The Blueprint uses a paid Render web plan because Render persistent disks are available only to paid web services. The disk is required for the SQLite job store and downloads to survive restarts. Free web services spin down after 15 minutes without inbound traffic and take about a minute to start again; they also have ephemeral storage, so they are unsuitable for this disk-backed setup. See Render's [persistent disk documentation](https://render.com/docs/disks) and [free service limits](https://render.com/docs/free).

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | Required | Private credential used for generation. |
| `SLIDEGEN_ASSETS_ENABLED` | `true` | Resolve optional icons/logos/photos before rendering. |
| `SLIDEGEN_ASSET_CACHE_DIR` | `<data dir>/assets` | Render uses `/var/data/assets`; downloaded images and manifests stay local. |
| `PEXELS_API_KEY` | Optional | Enables topic-based Pexels photo searches; missing keys keep shape fallbacks. Never print the key. |
| `SLIDEGEN_TRUST_CLOUDFLARE_IP_HEADER` | `false` locally; `true` on Render | Use `CF-Connecting-IP` for Render rate limiting; malformed or missing values share a safe fallback limit bucket. |
| `SLIDEGEN_DATA_DIR` | `./data` | Directory for SQLite and deck files; Render uses `/var/data`. |
| `SLIDEGEN_MAX_JOBS` | `1` | Concurrent generation workers. |
| `SLIDEGEN_MAX_ACTIVE_JOBS` | `20` | Maximum queued plus running jobs. |
| `SLIDEGEN_RETENTION_HOURS` | `24` | Age after which job records and deck files are deleted. |
| `SLIDEGEN_JOB_TIMEOUT_SECONDS` | `900` | Maximum time budget for a generation job. The deadline is checked between pipeline stages and after rendering. |
| `SLIDEGEN_LLM_PROVIDER` | `groq` | Configured provider name. |
| `SLIDEGEN_USE_SYSTEM_PROXY` | `false` | Use system HTTP proxy variables for Groq requests when the network requires a proxy. |
| `SLIDEGEN_MODEL` | Provider defaults | Optional default model for all pipeline stages. |
| `SLIDEGEN_ANALYSIS_MODEL` | `openai/gpt-oss-20b` | Optional analysis-stage override. |
| `SLIDEGEN_OUTLINE_MODEL` | `openai/gpt-oss-20b` | Optional outline-stage override. |
| `SLIDEGEN_WRITING_MODEL` | `openai/gpt-oss-120b` | Optional writing-stage override. |
| `SLIDEGEN_REVIEW_MODEL` | `openai/gpt-oss-20b` | Optional review-stage override. |
| `SLIDEGEN_STRUCTURED_ATTEMPTS` | `2` | Structured-output attempts per pipeline stage. |

Copy `.env.example` for local configuration. It contains placeholders only; never commit `.env` or paste secret values into logs or documentation.

### Runtime limits

- Run a single service instance. The queue is in-process and the SQLite database and files live on one attached disk. The Blueprint sets `numInstances: 1`.
- The API accepts at most 20 queued or running jobs overall and 10 submissions per client IP per minute by default. The Render Blueprint uses Cloudflare's client-IP header for this limit instead of the caller-controlled leftmost `X-Forwarded-For` value.
- Job status includes the current pipeline stage and queue position. The default job time budget is 900 seconds. A deadline can mark a job failed while an in-flight provider request is still waiting for its own network timeout; Python worker threads cannot interrupt that request.
- Render disks are available on paid web services and attach to one instance. Redeploys with a disk can briefly take the service offline.
- The default retention window is 24 hours. Increase it only if the persistent disk has enough room for the expected deck volume.
- On a free web service, idle spin-down adds a cold start and local files are lost on restart. The configured persistent disk requires a paid plan.

## Limitations and design decisions

- SlideGen uses Groq for generation and does not retrieve external factual research. Review checks the generated deck against the prompt; it is not independent fact-checking. Asset source notes describe images, not evidence for claims.
- Missing numeric data defaults to placeholders. Illustrative chart/statistic figures require an explicit request and provenance labels. Supply verified market or financial values.
- A passed model review is insufficient for client acceptance: live manual QA found unsupported details after a pass. The final guards are verified offline, with a post-guard live rerun still required.
- There is no in-browser editor, collaboration, account system, or image generation. The public job API uses per-IP rate limits and a bounded queue, not user authentication.
- The job queue and request limiter are in process. Run one Render instance with its persistent disk attached.
- The 900-second job deadline marks a job failed at the deadline, but a provider request already in progress cannot be interrupted; its worker can remain occupied until the request times out.

## Use the pipeline from Python

`generate_deck()` returns a validated `Deck` model. Pass it to the renderer to create a PowerPoint:

```python
from app.pipeline.orchestrator import generate_deck
from app.render.builder import build_deck

deck = generate_deck(
    "Create a 6-slide product launch presentation",
    theme="default",
    max_workers=1,
    max_repair_rounds=2,
)
output_path = build_deck(deck, "outputs/product-launch.pptx", strict=True)
print(output_path)
```

## Pipeline overview

1. **Analyze** extracts the deck title, audience, tone, slide count, constraints, and required facts from the prompt.
2. **Outline** creates a slide-by-slide plan with a purpose, supported layout, key points, and required-fact references. During review, the original request and its required facts remain the source of truth; unsupported outline points do not become requirements.
3. **Write** generates typed slide content. Slide calls run concurrently, subject to `max_workers` (default `1`), and output order follows the outline. The original request and assigned facts are the evidence source; outline key points only organize that evidence. Generated email addresses and URLs must also appear in the original request.
4. **Review and repair** checks for missing facts, duplicates, unsupported claims, likely overflow, and coherence. It removes exact duplicate bullets when possible, regenerates flagged slides up to `max_repair_rounds`, and replaces persistent high-severity unsupported claims with placeholders before a final review.
5. **Validate** validates the final deck against the shared schema.
6. **Resolve visual assets** fetches optional images with a 20-second total budget, four workers, a 6-second request budget, and a disk cache. Failures fall back to shapes/text. The renderer consumes only the resolved local files in `Deck.assets`.
7. **Render** creates the editable `.pptx`; images retain source/photographer/terms in notes.

## Visual assets and source terms

Icons come from the Lucide collection through Iconify and map step keywords such as KYC, payments, invoices and funding to icons. Lucide uses ISC, with Feather-derived icons such as `circle` and `check` under MIT; the notices are included in slide notes. See [Lucide's licence](https://lucide.dev/license). `resvg-py` provides SVG-to-PNG conversion under MIT. Native text, tables, allocation charts, cards and workflows remain editable PowerPoint objects.

Photos use the [Pexels API](https://www.pexels.com/api/documentation/) and [Pexels License](https://www.pexels.com/license/). Queries use a deck-topic mapping rather than raw user prompts. Photos appear on covers and allocation hero/edge areas when a key is configured; credit and a Pexels link appear on the slide, with photographer/source/terms in notes and the cache manifest. Stock photos are illustrative and do not imply endorsement. No Pexels key was available for the local QA session; photo retrieval is covered by fake HTTP tests, not real photo evidence. Unsplash and logo.dev are not used and need no keys.

Competitor logos use [Simple Icons](https://github.com/simple-icons/simple-icons), only for exact names supplied in the original prompt and only in competitive quadrants. Missing logos remain clean text chips. The collection uses CC0, but [individual brand licences and trademark rights still apply](https://github.com/simple-icons/simple-icons/blob/develop/DISCLAIMER.md); inclusion is not endorsement or trademark clearance. Real fixture retrieval succeeded for Visa, Polygon and WazirX; other fixture names fell back to chips. Live Acme Pay supplied no competitor names, so its grid correctly contains placeholders.

The downloader allowlists four HTTPS hosts, validates all DNS results and pins the connection to a public IP with the original TLS SNI. Redirects, private/local addresses, files over 5 MiB, oversized decoded images, incorrect MIME types and SVG external resources are rejected. Images are checked with Pillow and downscaled to at most 1600×1200; SVG icons are 256×256. The cache is pruned to a 128 MiB budget before each resolution run, including manifests; newly downloaded files can temporarily add to this until the next run. Asset timeout workers may finish caching after the resolution budget, but rendering proceeds without waiting for them. Keep assets and manifests out of Git.

Manual reference-asset QA: `python scripts/asset_preview.py`, then render its decks with `python -m evals.qa_render`. These are deterministic fixtures, not LLM generation evidence. Local sources are recorded in `outputs/qa/client-ready/asset-sources.json` and cache manifests. No reference screenshots are pasted into decks.

Structured output is validated at each LLM stage. Invalid structured responses receive bounded retries. Groq's HTTP 400 `json_validate_failed` and `output_parse_failed` errors receive bounded JSON-correction retries. Recovery attempts use native strict JSON schemas for the configured `openai/gpt-oss-20b` and `openai/gpt-oss-120b` models, with low reasoning effort and the same completion-token limit; normal first attempts keep their existing reasoning effort. Other Groq models retain JSON-object recovery. Schema preparation follows [Groq structured-output requirements](https://console.groq.com/docs/structured-outputs) and the [GPT-OSS reasoning options](https://console.groq.com/docs/reasoning). Pydantic and grounding/review checks still validate every result. Quota, authentication, unrelated HTTP 400, and other provider failures are not retried by this loop. The review repair path removes exact duplicate bullets reported by review, including duplicates across two-column slides, and can fall back to placeholders for persistent high-severity unsupported claims. If the final review still fails, `generate_deck()` raises `DeckReviewError` instead of returning a deck that failed QA. Review requests now label each slide with its explicit zero-based index and narrow the prompt JSON schema to the current deck's slide indices and required fact IDs. Reference IDs are checked before findings are filtered. Bounded correction requests include the exact allowed IDs and the rejected review; persistent invalid references still fail generation rather than being discarded or guessed.

The deck schema is the shared contract between generation and rendering. It supports fourteen layouts and enforces content limits and layout-specific requirements in [app/core/deck_schema.py](app/core/deck_schema.py).

The four reference-specific layouts are `ask_allocation`, `market_snapshot`, `flow_steps`, and `quadrants`. They are available to the planner for funding splits, market overviews, workflows, and competitive landscapes. Their renderers are deterministic and covered by fixture/schema tests. Automated checks confirm that the layouts render as PowerPoint shapes and charts; they do not establish that live generated decks match the reference images visually.

## Tests

The earlier `d21d769`/260-test checkpoint is historical. Current asset resolution, reference/generic styling and guardrail checkpoints are descendants. Fake job regressions cover out-of-range review references and analysis `output_parse_failed` recovery through saved PowerPoints. Owner-supplied production failures still require deployed-prompt confirmation; startup and accepted jobs alone do not establish generation success.

Install development dependencies with `python -m pip install -r requirements-dev.txt`, then run the suite from the project root:

```powershell
python -m pytest
```

Run `ruff check .` and `ruff format --check app tests` for lint and format checks.

The tests cover configuration, schema validation, structured-output retries, pipeline stages, targeted repairs, orchestration, PowerPoint rendering, job persistence, restart recovery, and the FastAPI flow, including queue limits and rate limiting. They use fake LLM responses and do not require a live API key.

Latest local verification (2026-10-23, implementation `67594e6`): **345 offline tests passed**; `ruff check .`, `ruff format --check app tests` (**71 files**), and `git diff --check` passed. The actual start-of-task baseline was 276 tests. Fake HTTP tests cover cache hits, missing keys, timeout, byte/MIME limits, blocked hosts/private DNS, no-logo text fallback and an image-embedded saved PowerPoint. Provider tests use fakes, including bounded retries, daily/unknown/oversized limits, deadlines, cancellation and sanitized logging.

Real Groq execution completed all five ordered smoke prompts and a separate full Blush prompt under `outputs/live-smoke/client-ready-latest/`. All 31 smoke slides and five full-Blush slides were rendered and inspected with LibreOffice/PyMuPDF. These runs preceded final guard changes: manual inspection found unsupported Acme features/market assertions, invented illustrative chart values, investor commercial/funding details, and Blush closing actions. The full Blush deck retained its name, products and three launch actions but used a chart-data placeholder for growth. The subsequent offline fixes route growth to workflow, reject the observed unsupported content, suppress fact-free company closings and default missing numbers to placeholders. A last full-Blush live attempt failed during writing and then reached the daily cap (198,801/200,000 used; 2,315 requested). No final-code live pass is claimed. Two numeric-placeholder QA decks are offline postprocessing of previous live JSON, not new live generations.

The owner's newer eight-slide file `ca1ed7ea1bbf4546add843983ff1406b.pptx` was unavailable locally, so its slides 3 and 8 could not be inspected. The available older four-slide file was rendered in `outputs/qa/blush-live/`, with defects recorded there. The deployed commit/environment/disk and real PowerPoint remain unverified. A direct health request timed out; this does not establish a deployment failure.

Previous local verification (2026-10-22): **260 tests passed**; Ruff lint, format (**61 files**), and `git diff --check` completed successfully. Offline checks cover reference-layout geometry, quota failures, token counting, smoke-script ordering/stop behavior, adversarial sparse prompts, the exact supplied Blush launch/growth prompt, and notes-only missing facts. LibreOffice/PyMuPDF PNGs of the reference fixture, dense fixtures, and existing Acme Pay deck were inspected against the four references in the earlier QA pass. The owner's four-slide Blush file was then rendered and all slides/notes inspected: its name was wrong, its source sentence was broken, and the launch/growth plan appeared only in closing notes. All five slides of a corrected fake-pipeline preview were rendered and inspected under `outputs/qa/blush-correction/`; this is offline fixture evidence, not a fresh Groq result. Photos, logos, icons, market evidence, and competitor identities remain absent or placeholders. The deployed revision, `/healthz`, live behavior after these corrections, and real PowerPoint remain unchecked.

## Token usage and manual live smoke

Each web job logs one counts-only summary for `analyze`, `outline`, `write`, `review`, and `repair`, including input, output, and total tokens reported by Groq. Completed calls count even when JSON validation triggers another attempt. Failed requests with no usage data cannot be counted. Provider failures retain only a safe HTTP status and allowlisted error code; SDK error bodies, `failed_generation`, reasoning text, and raw exception chains are excluded from terminal job errors/logs. Generated JSON/schema validation failures also use sanitized terminal descriptions. Repair rewrites are separate from initial writing; repeat reviews count under review. The public job response is unchanged. No new prompt or completion text is logged.

Structured requests to the two configured GPT-OSS models exclude reasoning from returned response fields. SDK retries are disabled. Confirmed temporary per-minute HTTP 429 errors receive the bounded waits described above, independently of JSON-correction attempts. Other or exhausted provider limits stop structured retries and pending slide work, mark the job failed with the safe limit message, and log no raw quota response. The job record is failed immediately even when another request is in flight. Those calls cannot be interrupted; the final token summary is emitted after they finish.

After confirming the key and available quota, run explicitly:

```powershell
python scripts/live_smoke.py
```

The script runs Acme Pay, product launch, Blush-style, dogs, and the seed-investor prompt in order, one at a time. It saves decks and per-prompt result/token/review JSON in `outputs/live-smoke/`, prints local review evidence, and exits non-zero on failure. It stops at the first quota/rate-limit error. The successful local sequence produced 6, 6, 5, 6 and 8 slides; this establishes runtime completion, not content acceptance. The subsequent full Blush run produced five slides. Re-run after quota resets and review the actual decks after the final guards.

Token-saving options were not applied: analysis/review already default to the smaller configured 20b model; evaluating a smaller review model needs a factual-regression comparison and may miss errors. A compact review payload could omit redundant schema/outline prose while retaining the original prompt, fact inventory and every visible slide; test factual recall before enabling it. The Python/CLI `max_repair_rounds` option can reduce rewrite cost while retaining final review, with more failed jobs as a tradeoff. There are no measured savings from these proposals. One full Blush run reported 22,526 tokens (outline 2,608, review 3,390); the eight-slide investor run reported 81,578, including 41,781 repair tokens. These are individual observations. Removing a repair round saves its rewrites plus a review; shrinking review input by 25% saves about 25% of review input, not total job tokens.

## Visual QA

The optional render QA utility converts the fixture deck to PDF and per-slide PNG files. Install PyMuPDF and LibreOffice first:

```powershell
python -m pip install pymupdf
winget install TheDocumentFoundation.LibreOffice
```

Then render the fixtures or an existing generated deck:

```powershell
python -m evals.qa_render tests/fixtures/decks/reference_layouts.json
python -m evals.qa_render tests/fixtures/decks/all_layouts.json
python -m evals.qa_render data/outputs/acme-pay-final.pptx
```

The `.pptx`, `.pdf`, and PNG outputs are written under `outputs/qa/` (override with `--output-dir`). LibreOffice uses an isolated temporary profile. It may substitute fonts or interpret charts differently from Microsoft PowerPoint.

The corrected local Blush preview is `outputs/qa/blush-correction/blush-launch-offline.pptx`; its five PNGs are under `outputs/qa/blush-correction/blush-launch-offline/`. It was produced with fake stage responses through the real pipeline and renderer. Its five-slide sequence is fixture data, not a fixed production template or a live-provider result. These generated files remain ignored and are not included in Git.

`tests/test_reference_geometry.py` checks slide bounds and nonempty text-region overlaps (including table cells) for minimum, typical, and maximum fixture content. These checks require neither an LLM nor LibreOffice; they do not prove glyph fit or reference similarity. Final reference, dense and generic fixtures are under `outputs/qa/client-ready/`; real live renders are under `outputs/qa/client-ready-latest/`. All four source screenshots were viewed separately and compared with rendered fixtures. Final dense eight-step workflow text stays at or above 9 pt with wider alternating cards. Allocation uses source proportions and a centered icon/label, but concentrated small slices still crowd the leader tracks. Market hero cards, driver-table styling and timeline chips, and quadrant pills/logo/text grids share the reference family. Missing photography, partial logo coverage and an inset allocation panel remain visual gaps. No pixel-match or client-acceptance claim is made. Real PowerPoint inspection remains required.

## Project structure

```text
app/
  assets/                   Bounded cached image resolution and source manifests
  config.py                 Environment-backed provider and model settings
  main.py                   FastAPI app factory and server entry point
  api/                      Request models and job routes
  jobs/                     SQLite job store and thread-pool runner
  static/index.html         Browser prompt, progress, and download UI
  core/
    deck_schema.py           Validated deck and slide models
    errors.py                Pipeline and provider errors
  llm/
    client.py                Provider-neutral client interface
    factory.py               Provider construction
    groq_provider.py         Groq chat-completion adapter
    schema.py                Strict-schema preparation for bounded recovery
    structured.py            JSON parsing, schema validation, and retries
    usage.py                 Counts-only per-stage token accounting
  pipeline/
    analyze.py               Prompt analysis
    models.py                Stage contracts
    outline.py               Slide planning
    write_slides.py          Parallel writing and selected-slide rewriting
    review.py                Review and repair loop
    orchestrator.py          End-to-end generation entry point
  render/
    builder.py               Deck model to PowerPoint renderer
    fit.py                   Text sizing estimates
    theme.py                 Theme colors, fonts, and dimensions
    layout/                  Fourteen slide layout renderers
run_deck.py                  Command-line entry point
scripts/live_smoke.py         Ordered manual live-generation checks
Dockerfile                   Container entry point for the web service
docker-entrypoint.sh         Writable data directory and non-root runtime user
render.yaml                  Render Blueprint with persistent disk and health check
requirements-dev.txt         Test and lint dependencies
pyproject.toml               Ruff configuration
.github/workflows/ci.yml     GitHub test and lint workflow
tests/                       Automated test suite and deck fixture
evals/qa_render.py            Optional PDF and PNG visual QA
```
