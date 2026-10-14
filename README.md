# SlideGen

SlideGen turns a natural-language prompt into a structured presentation, reviews the generated content, and renders a downloadable PowerPoint (`.pptx`) file. You can use the command-line interface or the FastAPI web app.

## Current project scope

- Generate a deck from a prompt with the Groq provider.
- Choose separate models for analysis, outlining, writing, and review.
- Write slides concurrently with a configurable worker limit.
- Review generated content and selectively regenerate slides with findings.
- Render ten layouts: title, section, bullets, two-column, stat, process, comparison, chart, quote, and closing.
- Save the result as a `.pptx` file or submit an asynchronous web job and download it in the browser.

The web app stores jobs and generated files in SQLite and uses a bounded in-process worker pool. See [Deploy](#deploy) for a public service setup.

The web API currently has no authentication or access-code gate. Anyone who can reach a deployed URL can submit prompts and download generated decks. Add an access-control layer before sharing a deployment that should be private.

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
python -m pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, run the Python executable directly instead:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
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

SlideGen can work from a short brand or product description. When the prompt does not specify an audience or deck type, the analysis stage treats it as a general product introduction rather than assuming an investor pitch. It uses supplied product details as source facts and directs the outline, writing, and review stages to avoid adding unsupported statistics, capabilities, outcomes, or business details. If you want a particular audience, slide count, evidence, or contact information included, provide it in the prompt.

The slide writer also checks generated text for email addresses and URLs that were not supplied, and for copied metadata labels such as `Startup name`. A match triggers a retry; if the slide still contains it, generation fails rather than accepting that text.

## Run the web app locally

With the virtual environment activated and `.env` configured:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>. The job database and presentations are written under `SLIDEGEN_DATA_DIR` (default `./data`).

The API accepts `POST /api/jobs` with `{"prompt": "..."}`, returns an ID and status, and serves progress from `GET /api/jobs/{id}`. Download completed jobs from `GET /api/jobs/{id}/download`. `GET /healthz` is the health check.

If a job fails, the browser shows a generic error. Check the terminal running Uvicorn for the job's traceback; when final review fails, the log also includes the review findings. The API keeps this detail out of the user-facing error.

## Deploy

The repository includes a `Dockerfile` and a Render Blueprint in `render.yaml`. The Blueprint runs one Docker web service in Singapore, checks `/healthz`, and attaches a 1 GB persistent disk at `/var/data` for SQLite and completed presentations.

### Deploy on Render

1. Push the repository to GitHub and connect it to Render.
2. In Render, create a **Blueprint** from this repository and review the service settings from `render.yaml`.
3. Set the required `GROQ_API_KEY` in the service's environment. The deployed API is currently public and has no access-code protection.
4. Apply the Blueprint and wait for `/healthz` to pass.
5. Open the service URL, submit a test prompt, and download the resulting PowerPoint.

The Blueprint uses a paid Render web plan because Render persistent disks are available only to paid web services. The disk is required for the SQLite job store and downloads to survive restarts. Free web services spin down after 15 minutes without inbound traffic and take about a minute to start again; they also have ephemeral storage, so they are unsuitable for this disk-backed setup. See Render's [persistent disk documentation](https://render.com/docs/disks) and [free service limits](https://render.com/docs/free).

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | Required | Private credential used for generation. |
| `SLIDEGEN_DATA_DIR` | `./data` | Directory for SQLite and deck files; Render uses `/var/data`. |
| `SLIDEGEN_MAX_JOBS` | `1` | Concurrent generation workers. |
| `SLIDEGEN_MAX_ACTIVE_JOBS` | `20` | Maximum queued plus running jobs. |
| `SLIDEGEN_RETENTION_HOURS` | `24` | Age after which job records and deck files are deleted. |
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
- The API accepts at most 20 queued or running jobs overall and 10 submissions per client IP per minute by default.
- Render disks are available on paid web services and attach to one instance. Redeploys with a disk can briefly take the service offline.
- The default retention window is 24 hours. Increase it only if the persistent disk has enough room for the expected deck volume.
- On a free web service, idle spin-down adds a cold start and local files are lost on restart. The configured persistent disk requires a paid plan.

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
4. **Review and repair** checks for missing facts, duplicates, unsupported claims, likely overflow, and coherence. It regenerates flagged slides and reviews the deck again up to `max_repair_rounds`.
5. **Validate and render** validates the final deck against the shared schema and creates the `.pptx`.

Structured output is validated at each LLM stage. Invalid structured responses receive bounded retries; provider transport and rate-limit retries are handled by the provider SDK to avoid duplicating failed requests. If the final review still fails, `generate_deck()` raises `DeckReviewError` instead of returning a deck that failed QA.

The deck schema is the shared contract between generation and rendering. It supports ten layouts and enforces content limits and layout-specific requirements in [app/core/deck_schema.py](app/core/deck_schema.py).

## Tests

Run the test suite from the project root:

```powershell
python -m pytest
```

The tests cover configuration, schema validation, structured-output retries, pipeline stages, targeted repairs, orchestration, PowerPoint rendering, job persistence, restart recovery, and the FastAPI flow, including queue and rate limits. They use fake LLM responses and do not require a live API key. The API has no access-code/authentication tests because that feature was removed.

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
render.yaml                  Render Blueprint with persistent disk and health check
tests/                       Automated test suite and deck fixture
evals/qa_render.py            Optional PDF and PNG visual QA
```
