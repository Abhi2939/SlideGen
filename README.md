# SlideGen

SlideGen turns a natural-language prompt into a structured presentation, reviews the generated content, and renders a downloadable PowerPoint (`.pptx`) file. You can use the command-line interface or the FastAPI web app.

**Live demo:** [slidegen-edz9.onrender.com](https://slidegen-edz9.onrender.com/)

## Current project scope

- Generate a deck from a prompt with the Groq provider.
- Choose separate models for analysis, outlining, writing, and review.
- Write slides concurrently with a configurable worker limit.
- Review generated content and selectively regenerate slides with findings.
- Render ten layouts: title, section, bullets, two-column, stat, process, comparison, chart, quote, and closing.
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

SlideGen can work from a short brand or product description. When the prompt does not specify an audience or deck type, the analysis stage treats it as a general product introduction rather than assuming an investor pitch. It uses supplied product details as source facts and directs the outline, writing, and review stages to avoid adding unsupported statistics, capabilities, outcomes, business scope, or business details. The writer retries numeric claims not present in the analyzed facts and common unsupported claims about products, operations, reach, strategy, and company capabilities. If a company prompt supplies no specific facts, SlideGen uses explicit placeholders and illustrative charts; provide concrete product, market, and operating details for a finished company-specific deck.

For investor pitches, asking for a seed-investor audience does not establish product features, integrations, workflows, pricing, traction, founder experience, valuation, runway, or a funding amount. The writer is instructed to use placeholders for those details when the prompt omits them. If the reviewer still finds high-severity unsupported claims after its rewrite rounds, the pipeline replaces those slides with explicit placeholders that preserve facts assigned to the slides, then reviews the deck again. Supply verified details for a more complete pitch.

The slide writer also checks generated text for email addresses and URLs that were not supplied, and for copied metadata labels such as `Startup name`. A match triggers a retry; if the slide still contains it, generation fails rather than accepting that text.

Deck-format directions such as slide count and requested layouts are treated as instructions, not slide facts. The writer retries when generated copy repeats those directions or describes the deck's own slide and visual counts. If charts or figures are requested without numeric source data, charts use example values marked as illustrative and identified as replaceable. The outline must include the chart and at least one additional visual slide.

## Run the web app locally

With the virtual environment activated and `.env` configured:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>. The job database and presentations are written under `SLIDEGEN_DATA_DIR` (default `./data`).

The API accepts `POST /api/jobs` with `{"prompt": "..."}`, returns an ID and status, and serves the current stage and queue position from `GET /api/jobs/{id}`. Download completed jobs from `GET /api/jobs/{id}/download`. `GET /healthz` is the public health check.

If a job fails, the browser shows a generic error. Check the terminal running Uvicorn for the job's traceback; when final review fails, the log also includes the review findings. The API keeps this detail out of the user-facing error.

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

The Blueprint uses a paid Render web plan because Render persistent disks are available only to paid web services. The disk is required for the SQLite job store and downloads to survive restarts. Free web services spin down after 15 minutes without inbound traffic and take about a minute to start again; they also have ephemeral storage, so they are unsuitable for this disk-backed setup. See Render's [persistent disk documentation](https://render.com/docs/disks) and [free service limits](https://render.com/docs/free).

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | Required | Private credential used for generation. |
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

- SlideGen uses Groq for generation and does not retrieve or cite external sources. Review checks the generated deck against the prompt; it is not independent fact-checking.
- Numeric data invented for a chart or statistic must be marked illustrative. Avoid prompts that require current market or financial figures unless the source values are supplied.
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
5. **Validate and render** validates the final deck against the shared schema and creates the `.pptx`.

Structured output is validated at each LLM stage. Invalid structured responses receive bounded retries. Groq's `json_validate_failed` response is retried with a targeted JSON correction; quota and rate-limit errors are not repeated by the structured-output loop. The review repair path removes exact duplicate bullets reported by review, including duplicates across two-column slides, and can fall back to placeholders for persistent high-severity unsupported claims. If the final review still fails, `generate_deck()` raises `DeckReviewError` instead of returning a deck that failed QA.

The deck schema is the shared contract between generation and rendering. It supports ten layouts and enforces content limits and layout-specific requirements in [app/core/deck_schema.py](app/core/deck_schema.py).

## Tests

Install development dependencies with `python -m pip install -r requirements-dev.txt`, then run the suite from the project root:

```powershell
python -m pytest
```

Run `ruff check .` and `ruff format --check app tests` for lint and format checks.

The tests cover configuration, schema validation, structured-output retries, pipeline stages, targeted repairs, orchestration, PowerPoint rendering, job persistence, restart recovery, and the FastAPI flow, including queue limits and rate limiting. They use fake LLM responses and do not require a live API key.

Latest recorded local verification (2026-10-15): **122 tests passed** before the access-code removal. The automated suite has not been rerun after the subsequent pipeline changes through commit `74d9ff2`; no current test count is claimed. Ruff lint and format checks passed previously, and the project virtual environment had no dependency conflicts. Recent Render logs showed an investor-pitch job failing review for unsupported feature, workflow, pricing, team, and funding claims. Commit `74d9ff2` adds stronger investor-pitch grounding and a placeholder fallback, but that fix has not yet been confirmed by a live generation. The Docker image build also remains unverified because Docker Desktop's Linux engine was unavailable. Use `test.md` as the manual prompt checklist.

## Visual QA

The optional render QA utility converts the fixture deck to PDF and per-slide PNG files. Install PyMuPDF and LibreOffice first:

```powershell
python -m pip install pymupdf
winget install TheDocumentFoundation.LibreOffice
```

Then render the fixture:

```powershell
python -m evals.qa_render tests/fixtures/decks/all_layouts.json
```

The `.pptx`, `.pdf`, and PNG outputs are written under `evals/outputs/`. LibreOffice may substitute fonts, so its text wrapping can differ slightly from Microsoft PowerPoint.

## Project structure

```text
app/
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
    structured.py            JSON parsing, schema validation, and retries
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
    layout/                  Ten slide layout renderers
run_deck.py                  Command-line entry point
Dockerfile                   Container entry point for the web service
docker-entrypoint.sh         Writable data directory and non-root runtime user
render.yaml                  Render Blueprint with persistent disk and health check
requirements-dev.txt         Test and lint dependencies
pyproject.toml               Ruff configuration
.github/workflows/ci.yml     GitHub test and lint workflow
tests/                       Automated test suite and deck fixture
evals/qa_render.py            Optional PDF and PNG visual QA
```
