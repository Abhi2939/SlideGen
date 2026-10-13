"""Turn the user's prompt into a validated deck brief."""

from app.pipeline.models import AnalysisOutput
from app.llm.structured import StructuredGenerator


MAX_PROMPT_CHARS = 12_000
ANALYSIS_SYSTEM_PROMPT = """You are the analysis stage of a presentation generator.
Extract a concise brief for a presentation. Preserve concrete user-provided
facts, names, dates, quantities, and constraints. Mark a fact verbatim only
when its exact wording must be retained. Do not invent facts, statistics,
citations, or audience details. Never infer an investor pitch, fundraising
request, market analysis, or business plan unless the user asks for one. When
the user provides only a brand or product description, treat it as a general
product introduction for potential customers; leave the audience unspecified
if that is safer than guessing. Treat labels such as 'Startup name' or 'Brand
name' as metadata: extract and use the value as the brand name, and do not
copy the label into presentation text. Choose a slide count from 3 to 30 that
suits the request. Return all user facts and product claims that must appear
in the deck as uniquely identified required_facts, using IDs starting at 0
with no gaps. Do not turn a supplied product promise into a broader guarantee."""


def analyze_prompt(
    prompt: str,
    generator: StructuredGenerator,
    model: str,
    *,
    max_tokens: int | None = 1800,
) -> AnalysisOutput:
    """Analyze a user prompt into a brief and a complete fact inventory."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("prompt must not be empty")
    if len(clean_prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt must be at most {MAX_PROMPT_CHARS} characters")

    return generator.generate(
        AnalysisOutput,
        stage="analyze",
        model=model,
        system_prompt=ANALYSIS_SYSTEM_PROMPT,
        user_prompt=(
            "Analyze this presentation request. Include its explicit facts and constraints.\n\n"
            f"USER REQUEST:\n{clean_prompt}"
        ),
        temperature=0.1,
        max_tokens=max_tokens,
    )
