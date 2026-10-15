"""Write planned slides as typed deck-schema models with bounded parallelism."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from decimal import Decimal
from typing import Any, Mapping

from app.core.deck_schema import (
    Bullet,
    ChartSlide,
    ClosingSlide,
    ComparisonSlide,
    ProcessSlide,
    QuoteSlide,
    SectionSlide,
    Slide,
    StatSlide,
    TitleSlide,
    TwoColumnSlide,
    BulletsSlide,
)
from app.core.errors import SlideWriteError
from app.llm.structured import StructuredGenerator
from app.pipeline.grounding import unsupported_claim_categories
from app.pipeline.models import AnalysisOutput, OutlineOutput, RequiredFact, validate_outline_facts


SLIDE_MODELS = {
    "title": TitleSlide,
    "section": SectionSlide,
    "bullets": BulletsSlide,
    "two_column": TwoColumnSlide,
    "stat": StatSlide,
    "process": ProcessSlide,
    "comparison": ComparisonSlide,
    "chart": ChartSlide,
    "quote": QuoteSlide,
    "closing": ClosingSlide,
}

WRITE_SYSTEM_PROMPT = """You are the slide-writing stage of a presentation generator.
Write only the requested slide and return JSON matching its exact schema.
Keep the planned title and layout unchanged. Use the supplied key points and
include every fact assigned to this slide. Preserve verbatim facts exactly.
Treat requests such as slide count, layout, "include charts", and "cover these
topics" as instructions for your work, never as slide copy. Do not repeat or
paraphrase those instructions as presentation content.
Use only the facts assigned to this slide; do not repeat another slide's
assigned facts, even if they appear in the original request. Do not repeat the
deck subtitle or slogan on this slide unless it is assigned here.
Do not invent statistics, sources, quotations, names, capabilities, guarantees,
contact details, email addresses, phone numbers, URLs, or user-specific details.
Do not add claims about performance, confidence,
time savings, market demand, or technology unless the request supplies them.
For investor or fundraising slides, do not invent a raise amount, valuation,
ownership, runway, revenue, customer or pilot counts, forecasts, or use of funds.
If the request does not supply a funding amount, use a non-numeric ask or say
the amount is to be determined.
Do not turn a broad business type into specific products, product lines,
capabilities, capacity, facilities, locations, geographic reach, customers, or
operational scale unless the request supplies those details. Do not invent
company history, market position, strategy, initiatives, roadmaps, mission,
vision, business goals, strengths, R&D, supply-chain operations, customer support,
partnership availability, or a contact invitation.
For sparse product briefs, expand the supplied details with clear organization
and neutral explanations, not invented evidence or business claims. Do not
repeat metadata labels such as 'Startup name' in slide copy. Make each bullet
add a distinct point; do not restate the title, subtitle, or another bullet in
different words.
When no supported key points are available, write a brief general explanation
of the planned topic without inventing company-specific details, statistics,
capabilities, or outcomes.
Treat the original request and assigned facts as the only evidence. The slide
plan is organizational guidance, not a source of facts; ignore any key point
that adds details absent from the request and assigned facts. Do not infer
product mechanisms, technology, manufacturing methods, fulfillment or
shipping, customer behavior, market facts, or outcomes. For example, custom
blending does not establish in-house blending, AI analysis, algorithmic
selection, or shipping. When source material is sparse, organize and clearly
restate its supplied facts across the requested slides without adding claims.
For a product range and a shared set of matching dimensions, group the products
and dimensions into separate lists. Do not generate a product-by-dimension
matrix or imply an individual pairing unless the request states one.
For numeric visualizations use only supplied values; mark illustrative data as
illustrative only when the schema supports provenance. If the user requests a
chart but supplies no numeric facts, use clearly generic example data, set its
provenance to illustrative, and label the caption as an example to replace with
company data. Do not use the deck's slide or chart counts as chart data. Pie and
doughnut charts must use non-negative values with a positive total. Keep content
concise, readable, and consistent with the requested audience and tone."""

_CONTACT_PATTERN = re.compile(
    r"(?:\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\bhttps?://\S+|\bwww\.\S+)",
    re.IGNORECASE,
)
_NUMERIC_VALUE_PATTERN = re.compile(
    r"(?<![\w])(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?P<suffix>\s*(?:%|k|m|b|thousand|million|billion))?(?![\w])",
    re.IGNORECASE,
)
_METADATA_LABEL_PATTERN = re.compile(
    r"\b(?:startup\s+name\s*:?[ \t]*[A-Z][\w'-]*|"
    r"brand(?:\s+name)?\s*:\s*[A-Z][\w'-]*)",
    re.IGNORECASE,
)
_DIRECTIVE_PATTERN = re.compile(
    r"\b(?:create|make|generate|build|prepare|write|produce|include|add|"
    r"cover|show|compare|explain|turn|convert|avoid|ensure)\b[^.!?;]*",
    re.IGNORECASE,
)
_COPY_DIRECTIVE_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:create|make|generate|build|prepare|write|produce|"
    r"include|add|cover|show|compare|explain|turn|convert|avoid|ensure)\b",
    re.IGNORECASE,
)
_COUNT_WORDS = (
    r"\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty"
)
_DECK_STRUCTURE_ECHO_PATTERN = re.compile(
    rf"\b(?:this|the|our)\s+(?:deck|presentation)\b[^.!?]{{0,80}}"
    rf"\b(?:{_COUNT_WORDS})\s+(?:slides?|charts?|figures?|visuals?)\b|"
    rf"\bthere\s+(?:are|is)\b[^.!?]{{0,50}}"
    rf"\b(?:{_COUNT_WORDS})\s+(?:slides?|charts?|figures?|visuals?)\b|"
    rf"\b(?:{_COUNT_WORDS})\s+slides?\b[^.!?]{{0,40}}"
    rf"\b(?:{_COUNT_WORDS})\s+(?:charts?|figures?|visuals?)\b",
    re.IGNORECASE,
)
_INSTRUCTION_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "for",
    "from",
    "in",
    "it",
    "my",
    "of",
    "on",
    "our",
    "the",
    "this",
    "to",
    "with",
    "your",
}
_INSTRUCTION_ALIASES = {
    "make": "create",
    "generate": "create",
    "build": "create",
    "prepare": "create",
    "write": "create",
    "produce": "create",
    "presentation": "deck",
    "ppt": "deck",
    "pptx": "deck",
    "slides": "slide",
}
_GENERIC_BUSINESS_TOKENS = {
    "a",
    "an",
    "brand",
    "business",
    "company",
    "electronics",
    "firm",
    "my",
    "organization",
    "our",
    "startup",
    "tech",
    "technology",
    "the",
}


def _instruction_tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if token not in _INSTRUCTION_STOP_WORDS
    }
    return {_INSTRUCTION_ALIASES.get(token, token) for token in tokens}


def _contains_instruction_echo(slide_copy: str, prompt: str) -> bool:
    """Catch slide copy that repeats a user's imperative deck instructions."""
    for copy_line in slide_copy.splitlines():
        if not _COPY_DIRECTIVE_PREFIX.search(copy_line):
            continue
        copy_tokens = _instruction_tokens(copy_line)
        if len(copy_tokens) < 3:
            continue
        for match in _DIRECTIVE_PATTERN.finditer(prompt):
            instruction_tokens = _instruction_tokens(match.group(0))
            if len(instruction_tokens) < 3:
                continue
            shared = copy_tokens & instruction_tokens
            if len(shared) >= 3 and len(shared) / len(copy_tokens) >= 0.75:
                return True
    return False


def _contains_deck_structure_echo(slide_copy: str) -> bool:
    """Catch copy that describes the generated deck's own slide or visual counts."""
    return bool(_DECK_STRUCTURE_ECHO_PATTERN.search(slide_copy))


def _can_use_sparse_company_placeholder(prompt: str, analysis: AnalysisOutput) -> bool:
    """Use placeholders only when a company prompt gives no specific source facts."""
    if not re.search(r"\b(?:company|business|startup|brand|firm|organization)\b", prompt, re.I):
        return False
    return all(_is_generic_business_fact(fact) for fact in analysis.required_facts)


def _is_generic_business_fact(fact: RequiredFact) -> bool:
    return set(_normalized_phrase(fact.text).split()) <= _GENERIC_BUSINESS_TOKENS


def _placeholder_slide(plan, facts: list[RequiredFact] | None = None) -> Slide:
    """Build an explicit, non-factual placeholder matching the planned layout."""
    fact_texts = [fact.text for fact in facts or []]
    fallback_titles = {
        "section": "Section details",
        "bullets": "Details to supply",
        "two_column": "Company information",
        "stat": "Example metric",
        "process": "Process details",
        "comparison": "Comparison details",
        "chart": "Illustrative chart",
        "quote": "Supplied quotation",
    }
    title = plan.title
    if plan.layout == "closing":
        title = "Thank you"
    elif plan.layout in fallback_titles:
        title = f"{fallback_titles[plan.layout]} {plan.index + 1}"
    payload: dict[str, Any] = {"layout": plan.layout, "title": title}
    if plan.layout == "title":
        payload["subtitle"] = " ".join([*fact_texts, "Add supplied company details here."])
    elif plan.layout == "section":
        payload["subtitle"] = " ".join([*fact_texts, "Add verified details for this section."])
    elif plan.layout == "bullets":
        bullets = [{"text": text} for text in fact_texts if len(text) <= 160]
        bullets.extend(
            [
                {"text": "Add a verified company detail from the source material."},
                {"text": "Replace this placeholder with information you provide."},
            ]
        )
        payload["bullets"] = bullets[:6]
    elif plan.layout == "two_column":
        payload["left"] = {
            "heading": "Company details",
            "bullets": [
                *[{"text": text} for text in fact_texts[:4] if len(text) <= 160],
                {"text": "Add a verified detail from the source material."},
            ],
        }
        payload["right"] = {
            "heading": "Supporting information",
            "bullets": [{"text": "Replace this placeholder with supplied evidence."}],
        }
    elif plan.layout == "stat":
        payload["stats"] = [
            {
                "value": "TBD",
                "label": "Replace with a verified company metric",
                "provenance": "illustrative",
            }
        ]
    elif plan.layout == "process":
        payload["steps"] = [
            {"title": "Add supplied step", "description": "Replace with a verified detail."},
            {"title": "Add next step", "description": "Replace with a verified detail."},
        ]
    elif plan.layout == "comparison":
        payload["items"] = [
            {
                "name": "Option A",
                "points": ["Add a supplied comparison point.", "Replace this placeholder."],
            },
            {
                "name": "Option B",
                "points": ["Add a supplied comparison point.", "Replace this placeholder."],
            },
        ]
    elif plan.layout == "chart":
        context = " ".join(fact_texts) or "the supplied company context"
        payload.update(
            {
                "chart_type": "bar",
                "categories": ["Example A", "Example B"],
                "series": [{"name": "Example values", "values": [60, 40]}],
                "caption": f"Illustrative example for {context}; replace with supplied company data.",
                "provenance": "illustrative",
            }
        )
    elif plan.layout == "quote":
        payload.update(
            {
                "quote": "Add a supplied quotation here.",
                "provenance": "generated",
            }
        )
    elif plan.layout == "closing":
        payload["subtitle"] = None
    return SLIDE_MODELS[plan.layout].model_validate(payload)


def _text_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _text_leaves(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _text_leaves(item)]
    return []


def _normalized_phrase(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _duplicate_bullet_texts(slide: Slide) -> list[str]:
    if not isinstance(slide, BulletsSlide):
        return []
    seen: set[str] = set()
    duplicates: list[str] = []
    for bullet in slide.bullets:
        normalized = _normalized_phrase(bullet.text)
        if normalized in seen:
            duplicates.append(bullet.text)
        seen.add(normalized)
    return duplicates


def _numeric_values(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    multipliers = {
        "k": Decimal(1_000),
        "thousand": Decimal(1_000),
        "m": Decimal(1_000_000),
        "million": Decimal(1_000_000),
        "b": Decimal(1_000_000_000),
        "billion": Decimal(1_000_000_000),
    }
    for match in _NUMERIC_VALUE_PATTERN.finditer(text):
        number = Decimal(match.group("number").replace(",", ""))
        suffix = (match.group("suffix") or "").strip().casefold()
        values.add(number * multipliers.get(suffix, Decimal(1)))
    return values


def _numeric_data_issue(
    slide: Slide, analysis: AnalysisOutput, slide_text: str | None = None
) -> str | None:
    """Require user-sourced numeric slide data to match an analyzed source fact."""
    supplied_values = {
        number for fact in analysis.required_facts for number in _numeric_values(fact.text)
    }
    if isinstance(slide, StatSlide):
        data = [(stat.provenance, stat.value) for stat in slide.stats]
    elif isinstance(slide, ChartSlide):
        data = [
            (slide.provenance, str(value)) for series in slide.series for value in series.values
        ]
    else:
        supplied_text_numbers = {
            number for fact in analysis.required_facts for number in _numeric_values(fact.text)
        }
        unsupported_numbers = _numeric_values(slide_text or "") - supplied_text_numbers
        if unsupported_numbers:
            return "generated numeric details were not present in the analyzed required facts"
        return None

    for provenance, value in data:
        if provenance == "generated":
            return "numeric statistics and charts must be user-sourced or marked illustrative"
        if provenance == "user" and not _numeric_values(value) <= supplied_values:
            return "user-sourced numeric data was not present in the analyzed required facts"
    return None


def _restore_verbatim_facts(slide: Slide, missing_facts: list[RequiredFact]) -> Slide:
    """Place omitted short verbatim facts into visible slide copy when possible."""
    facts = [fact.text for fact in missing_facts]
    if isinstance(slide, (TitleSlide, SectionSlide, ClosingSlide)):
        if all(len(text) <= 140 for text in facts):
            current = getattr(slide, "subtitle", None)
            combined = " ".join(part for part in [current, *facts] if part)
            subtitle = combined if len(combined) <= 140 else " ".join(facts)
            if len(subtitle) <= 140:
                return slide.model_copy(update={"subtitle": subtitle})

    if isinstance(slide, BulletsSlide):
        bullets = list(slide.bullets)
        for fact in missing_facts:
            if len(fact.text) <= 160:
                new_bullet = Bullet(text=fact.text)
            elif len(fact.text) <= 200:
                new_bullet = Bullet(text="Required detail", detail=fact.text)
            else:
                return slide
            if len(bullets) < 6:
                bullets.append(new_bullet)
            else:
                bullets[-1] = new_bullet
        return slide.model_copy(update={"bullets": bullets})

    if isinstance(slide, QuoteSlide) and len(facts) == 1 and len(facts[0]) <= 240:
        return slide.model_copy(update={"quote": facts[0]})

    return slide


def _write_one(
    prompt: str,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    slide_index: int,
    generator: StructuredGenerator,
    model: str,
    *,
    max_tokens: int | None,
    semantic_attempts: int,
    repair_instruction: str | None = None,
) -> Slide:
    plan = outline.slides[slide_index]
    output_model = SLIDE_MODELS[plan.layout]
    facts_by_id = {fact.id: fact for fact in analysis.required_facts}
    assigned_facts: list[RequiredFact] = [
        facts_by_id[fact_id] for fact_id in plan.required_fact_ids
    ]
    if _can_use_sparse_company_placeholder(prompt, analysis) and all(
        _is_generic_business_fact(fact) for fact in assigned_facts
    ):
        return _placeholder_slide(plan, assigned_facts)

    request = (
        f"Write slide {plan.index + 1} of {len(outline.slides)}.\n"
        f"ORIGINAL USER REQUEST:\n{prompt}\n\n"
        f"DECK BRIEF:\n{analysis.model_dump_json(indent=2)}\n\n"
        f"SLIDE PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
        f"FACTS ASSIGNED TO THIS SLIDE:\n"
        f"{json.dumps([fact.model_dump(mode='json') for fact in assigned_facts], ensure_ascii=False, indent=2)}"
    )
    if repair_instruction:
        request += f"\n\nREVIEW REPAIR INSTRUCTIONS:\n{repair_instruction}"
    correction = ""
    last_error: ValueError | None = None

    for _ in range(semantic_attempts):
        slide = generator.generate(
            output_model,
            stage="write",
            model=model,
            system_prompt=WRITE_SYSTEM_PROMPT,
            user_prompt=request + correction,
            temperature=0.3,
            max_tokens=max_tokens,
        )
        try:
            if slide.layout != plan.layout:
                raise ValueError(f"generated layout {slide.layout!r} does not match the plan")
            if slide.title != plan.title:
                raise ValueError("generated title does not match the planned title")
            duplicate_bullets = _duplicate_bullet_texts(slide)
            if duplicate_bullets:
                raise ValueError(
                    f"generated slide contains duplicate bullet points: {duplicate_bullets}"
                )
            text_content = "\n".join(_text_leaves(slide.model_dump(mode="json")))
            slide_data = slide.model_dump(mode="json")
            slide_copy = "\n".join(
                _text_leaves(
                    {
                        key: value
                        for key, value in slide_data.items()
                        if key not in {"title", "speaker_notes"}
                    }
                )
            )
            if _contains_instruction_echo(slide_copy, prompt):
                raise ValueError(
                    "generated slide repeated the user's presentation instructions as slide copy"
                )
            if _contains_deck_structure_echo(slide_copy):
                raise ValueError(
                    "generated slide described the deck's own slide or visual counts as content"
                )
            normalized_content = _normalized_phrase(text_content)
            repeated_unassigned = [
                fact.text
                for fact in analysis.required_facts
                if fact.verbatim
                and fact.id not in plan.required_fact_ids
                and len(re.findall(r"[a-z0-9]+", fact.text.casefold())) >= 3
                and f" {_normalized_phrase(fact.text)} " in f" {normalized_content} "
            ]
            if repeated_unassigned:
                raise ValueError(
                    "generated slide repeated verbatim facts assigned to other slides: "
                    f"{repeated_unassigned}"
                )
            supplied_text = prompt.casefold()
            invented_contacts = [
                match.group(0).rstrip(".,;:!?")
                for match in _CONTACT_PATTERN.finditer(text_content)
                if match.group(0).rstrip(".,;:!?").casefold() not in supplied_text
            ]
            if invented_contacts:
                raise ValueError(
                    "generated slide included contact information not supplied in the request: "
                    f"{invented_contacts}"
                )
            if _METADATA_LABEL_PATTERN.search(text_content):
                raise ValueError(
                    "generated slide copied a brand metadata label into presentation text"
                )
            unsupported_claims = unsupported_claim_categories(text_content, prompt)
            if unsupported_claims:
                raise ValueError(
                    "generated slide added unsupported details in these categories: "
                    f"{', '.join(unsupported_claims)}"
                )
            numeric_data_issue = _numeric_data_issue(slide, analysis, text_content)
            if numeric_data_issue:
                raise ValueError(numeric_data_issue)
            missing_verbatim = [
                fact.id
                for fact in assigned_facts
                if fact.verbatim and fact.text not in text_content
            ]
            if missing_verbatim:
                missing_facts = [
                    fact for fact in assigned_facts if fact.verbatim and fact.id in missing_verbatim
                ]
                slide = _restore_verbatim_facts(slide, missing_facts)
                text_content = "\n".join(_text_leaves(slide.model_dump(mode="json")))
                missing_verbatim = [
                    fact.id for fact in missing_facts if fact.text not in text_content
                ]
            if missing_verbatim:
                raise ValueError(f"generated slide omitted verbatim fact IDs: {missing_verbatim}")
            return slide
        except ValueError as exc:
            last_error = exc
            if str(exc) == "generated numeric details were not present in the analyzed required facts":
                correction = (
                    "\n\nCORRECTION REQUIRED: The previous slide included numeric details "
                    "that were not supplied in the request or analyzed facts. Remove every "
                    "unsupported number, percentage, date, amount, count, and time period. "
                    "State the point in non-numeric language; for an unspecified funding ask, "
                    "say the amount is to be determined. Regenerate the complete slide."
                )
                continue
            correction = (
                "\n\nCORRECTION REQUIRED: The previous slide failed a contract check: "
                f"{exc}. Regenerate the complete slide and satisfy the plan exactly."
            )

    if (
        last_error is not None
        and all(_is_generic_business_fact(fact) for fact in assigned_facts)
        and _can_use_sparse_company_placeholder(prompt, analysis)
        and any(
            marker in str(last_error)
            for marker in (
                "unsupported details",
                "numeric details",
                "numeric data",
                "presentation instructions",
                "deck's own slide or visual counts",
            )
        )
    ):
        return _placeholder_slide(plan)

    assert last_error is not None
    raise ValueError(
        f"slide failed semantic validation after {semantic_attempts} attempt(s): {last_error}"
    ) from last_error


def write_slides(
    prompt: str,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    generator: StructuredGenerator,
    model: str,
    *,
    max_workers: int = 1,
    max_tokens: int | None = 1600,
    semantic_attempts: int = 2,
) -> list[Slide]:
    """Write all slides concurrently up to max_workers, preserving outline order."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("prompt must not be empty")
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if semantic_attempts < 1:
        raise ValueError("semantic_attempts must be at least 1")
    if outline.deck_title != analysis.deck_title:
        raise ValueError("outline deck title does not match the analyzed deck title")
    validate_outline_facts(analysis, outline)

    written = _write_indices(
        clean_prompt,
        analysis,
        outline,
        range(len(outline.slides)),
        generator,
        model,
        max_workers=max_workers,
        max_tokens=max_tokens,
        semantic_attempts=semantic_attempts,
    )
    return [written[index] for index in range(len(outline.slides))]


def rewrite_slides(
    prompt: str,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    current_slides: list[Slide],
    slide_indices: list[int],
    generator: StructuredGenerator,
    model: str,
    *,
    max_workers: int = 1,
    max_tokens: int | None = 1600,
    semantic_attempts: int = 2,
    repair_instructions: Mapping[int, str] | None = None,
) -> list[Slide]:
    """Regenerate only selected slides and preserve all other slide objects."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("prompt must not be empty")
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if semantic_attempts < 1:
        raise ValueError("semantic_attempts must be at least 1")
    if len(current_slides) != len(outline.slides):
        raise ValueError("current slide count does not match the outline")
    if not slide_indices:
        raise ValueError("at least one slide index is required for rewriting")
    if len(slide_indices) != len(set(slide_indices)):
        raise ValueError("slide indices to rewrite must be unique")
    repair_instructions = repair_instructions or {}
    unexpected_instructions = set(repair_instructions) - set(slide_indices)
    if unexpected_instructions:
        raise ValueError(
            f"repair instructions reference unselected slides: {sorted(unexpected_instructions)}"
        )
    invalid_indices = [
        index for index in slide_indices if index < 0 or index >= len(outline.slides)
    ]
    if invalid_indices:
        raise ValueError(f"slide indices to rewrite are out of range: {invalid_indices}")
    if outline.deck_title != analysis.deck_title:
        raise ValueError("outline deck title does not match the analyzed deck title")
    validate_outline_facts(analysis, outline)

    written = _write_indices(
        clean_prompt,
        analysis,
        outline,
        slide_indices,
        generator,
        model,
        max_workers=max_workers,
        max_tokens=max_tokens,
        semantic_attempts=semantic_attempts,
        repair_instructions=repair_instructions,
    )
    result = list(current_slides)
    for index, slide in written.items():
        result[index] = slide
    return result


def _write_indices(
    prompt: str,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    slide_indices,
    generator: StructuredGenerator,
    model: str,
    *,
    max_workers: int,
    max_tokens: int | None,
    semantic_attempts: int,
    repair_instructions: Mapping[int, str] | None = None,
) -> dict[int, Slide]:
    results: dict[int, Slide] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(slide_indices))) as executor:
        futures = {
            executor.submit(
                _write_one,
                prompt,
                analysis,
                outline,
                index,
                generator,
                model,
                max_tokens=max_tokens,
                semantic_attempts=semantic_attempts,
                repair_instruction=(repair_instructions or {}).get(index),
            ): index
            for index in slide_indices
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                for pending in futures:
                    pending.cancel()
                plan = outline.slides[index]
                raise SlideWriteError(index, plan.title, str(exc)) from exc
    return results
