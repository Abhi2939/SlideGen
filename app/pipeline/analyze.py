"""Turn the user's prompt into a validated deck brief."""

import re

from app.pipeline.briefs import short_beauty_brief
from app.pipeline.models import AnalysisOutput, RequiredFact
from app.llm.structured import StructuredGenerator


MAX_PROMPT_CHARS = 12_000
_FUNDING_AMOUNT_PATTERN = re.compile(
    r"(?<![\w])(?:Rs\.?|INR|₹|USD|US\$|\$|EUR|€|GBP|£)\s*"
    r"\d[\d,]*(?:\.\d+)?(?:\s*(?:crores?|cr|lakhs?|lakh|million|billion|[kmb]))?",
    re.IGNORECASE,
)
_FUNDING_CONTEXT_PATTERN = re.compile(
    r"\b(?:rais(?:e|es|ed|ing)|fundrais(?:e|es|ed|ing)|funding|investment|capital)\b",
    re.IGNORECASE,
)
_EXPLICIT_SLIDE_COUNT_PATTERN = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|twenty[- ]five|thirty)[-\s]+slides?\b",
    re.IGNORECASE,
)
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
    minimum_count = _minimum_reference_slide_count(clean_prompt)
    if minimum_count > analysis.slide_count and not _EXPLICIT_SLIDE_COUNT_PATTERN.search(
        clean_prompt
    ):
        analysis = analysis.model_copy(update={"slide_count": minimum_count})
    funding_amount = _extract_funding_amount(clean_prompt)
    if funding_amount and not any(
        fact.verbatim and fact.text.casefold() == funding_amount.casefold()
        for fact in analysis.required_facts
    ):
        next_id = max((fact.id for fact in analysis.required_facts), default=-1) + 1
        analysis = analysis.model_copy(
            update={
                "required_facts": [
                    *analysis.required_facts,
                    RequiredFact(id=next_id, text=funding_amount, verbatim=True),
                ]
            }
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


def _extract_funding_amount(prompt: str) -> str | None:
    """Keep an explicitly stated raise amount in the fact inventory verbatim."""
    for match in _FUNDING_AMOUNT_PATTERN.finditer(prompt):
        context = prompt[max(0, match.start() - 48) : min(len(prompt), match.end() + 32)]
        if _FUNDING_CONTEXT_PATTERN.search(context):
            return " ".join(match.group(0).split()).rstrip(".,;:")
    return None


def _minimum_reference_slide_count(prompt: str) -> int:
    """Reserve one slide for each explicitly requested reference layout plus a title."""
    requested = prompt.casefold()
    layouts = set()
    if re.search(
        r"\b(?:fund(?:ing)? ask|fund allocation|use of funds|budget split|fundraising ask)\b",
        requested,
    ):
        layouts.add("ask_allocation")
    if re.search(
        r"\bmarket(?: snapshot| size| share| growth| drivers?| analysis| CAGR)?\b",
        requested,
    ):
        layouts.add("market_snapshot")
    if re.search(r"\b(?:workflow|user journey|how it works)\b", requested):
        layouts.add("flow_steps")
    if re.search(r"\b(?:competitive landscape|competitor landscape)\b", requested):
        layouts.add("quadrants")
    if re.search(r"\bclosing slide\b", requested):
        layouts.add("closing")
    return 1 + len(layouts)
