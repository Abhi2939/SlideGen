# SlideGen

SlideGen turns a natural-language prompt into a structured presentation and a PowerPoint (`.pptx`) file. The current runnable entry point is a command-line interface. Generation uses a four-stage LLM pipeline, validates each stage with Pydantic models, renders with `python-pptx`, and reviews generated slides before returning the deck.

## Current project scope

- Generate a deck from a prompt with the Groq provider.
- Choose separate models for analysis, outlining, writing, and review.
- Write slides concurrently with a configurable worker limit.
- Review generated content and selectively regenerate slides with findings.
- Render ten layouts: title, section, bullets, two-column, stat, process, comparison, chart, quote, and closing.
- Save the result as a `.pptx` file.

The FastAPI, job-store, and browser UI modules are not implemented yet. Use the CLI below to generate decks.

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

The application has model defaults, so the key and provider are enough to get started. The defaults are:

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

## Use the pipeline from Python

`generate_deck()` returns a validated `Deck` model. Pass it to the renderer to create a PowerPoint:

```python
from app.pipeline.orchestrator import generate_deck
from app.render.builder import build_deck

deck = generate_deck(
    "Create a 6-slide product launch presentation",
    theme="default",
    max_workers=4,
    max_repair_rounds=2,
)
output_path = build_deck(deck, "outputs/product-launch.pptx", strict=True)
print(output_path)
```

## Pipeline overview

1. **Analyze** extracts the deck title, audience, tone, slide count, constraints, and required facts from the prompt.
2. **Outline** creates a slide-by-slide plan with a purpose, supported layout, key points, and required-fact references.
3. **Write** generates typed slide content. Slide calls run concurrently, subject to `max_workers`, and output order follows the outline.
4. **Review and repair** checks for missing facts, duplicates, unsupported claims, likely overflow, and coherence. It regenerates flagged slides and reviews the deck again up to `max_repair_rounds`.
5. **Validate and render** validates the final deck against the shared schema and creates the `.pptx`.

Structured output is validated at each LLM stage. Invalid structured output receives bounded retries. If the final review still fails, `generate_deck()` raises `DeckReviewError` instead of returning a deck that failed QA.

The deck schema is the shared contract between generation and rendering. It supports ten layouts and enforces content limits and layout-specific requirements in [app/core/deck_schema.py](app/core/deck_schema.py).

## Tests

Run the test suite from the project root:

```powershell
python -m pytest
```

The tests cover configuration, schema validation, structured-output retries, pipeline stages, targeted repairs, orchestration, and PowerPoint rendering. They use fake LLM responses and do not require a live API key.

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
tests/                       Automated test suite and deck fixture
evals/qa_render.py            Optional PDF and PNG visual QA
```
