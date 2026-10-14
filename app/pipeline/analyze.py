"""Turn the user's prompt into a validated deck brief."""

from app.pipeline.briefs import short_beauty_brief
from app.pipeline.models import AnalysisOutput, RequiredFact
from app.llm.structured import StructuredGenerator


MAX_PROMPT_CHARS = 12_000
ANALYSIS_SYSTEM_PROMPT = """You are the analysis stage of a presentation generator.
Extract a concise presentation brief. Separate factual source material from
presentation instructions. Put requested slide count, layouts, audience,
format, and emphasis in constraints; do not treat those instructions as facts
to print on slides. Put concrete user-provided names, dates, quantities,
product details, and claims in required_facts. Mark a fact verbatim only
when its exact wording must be retained. Do not invent facts, statistics,
citations, or audience details. Never infer an investor pitch, fundraising
request, market analysis, or business plan unless the user asks for one. When
the user provides only a brand or product description, treat it as a general
product introduction for potential customers; leave the audience unspecified
if that is safer than guessing. Treat labels such as 'Startup name' or 'Brand
name' as metadata: extract and use the value as the brand name, and do not
copy the label into presentation text. Choose a slide count from 3 to 30 that
suits the request. For a concise branded product brief with no requested slide
count, choose four slides and do not repeat facts just to fill pages. Return all user facts and product claims that must appear
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

    analysis = generator.generate(
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
    brief = short_beauty_brief(clean_prompt)
    if brief is None:
        return analysis

    facts = [RequiredFact(id=0, text=brief.brand, verbatim=True)]
    facts.extend(
        RequiredFact(id=index, text=text, verbatim=True)
        for index, text in enumerate(brief.facts, start=1)
    )
    return analysis.model_copy(
        update={
            "deck_title": brief.brand,
            "slide_count": 4,
            "required_facts": facts,
            "deck_type": "branded beauty product introduction",
        }
    )
