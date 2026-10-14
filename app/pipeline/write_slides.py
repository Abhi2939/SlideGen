"""Write planned slides as typed deck-schema models with bounded parallelism."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
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
Do not invent statistics, sources, quotations, names, capabilities, guarantees,
contact details, email addresses, phone numbers, URLs, or user-specific details.
Do not add claims about performance, confidence,
time savings, market demand, or technology unless the request supplies them.
For sparse product briefs, expand the supplied details with clear organization
and neutral explanations, not invented evidence or business claims. Do not
repeat metadata labels such as 'Startup name' in slide copy. Make each bullet
add a distinct point; do not restate the title, subtitle, or another bullet in
different words.
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
illustrative only when the schema supports provenance. Keep content concise,
readable, and consistent with the requested audience and tone."""

_CONTACT_PATTERN = re.compile(
    r"(?:\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\bhttps?://\S+|\bwww\.\S+)",
    re.IGNORECASE,
)
_METADATA_LABEL_PATTERN = re.compile(
    r"\b(?:startup\s+name\s*:?[ \t]*[A-Z][\w'-]*|"
    r"brand(?:\s+name)?\s*:\s*[A-Z][\w'-]*)",
    re.IGNORECASE,
)
def _text_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _text_leaves(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _text_leaves(item)]
    return []


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
            text_content = "\n".join(_text_leaves(slide.model_dump(mode="json")))
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
            missing_verbatim = [
                fact.id for fact in assigned_facts
                if fact.verbatim and fact.text not in text_content
            ]
            if missing_verbatim:
                missing_facts = [
                    fact
                    for fact in assigned_facts
                    if fact.verbatim and fact.id in missing_verbatim
                ]
                slide = _restore_verbatim_facts(slide, missing_facts)
                text_content = "\n".join(_text_leaves(slide.model_dump(mode="json")))
                missing_verbatim = [
                    fact.id for fact in missing_facts if fact.text not in text_content
                ]
            if missing_verbatim:
                raise ValueError(
                    f"generated slide omitted verbatim fact IDs: {missing_verbatim}"
                )
            return slide
        except ValueError as exc:
            last_error = exc
            correction = (
                "\n\nCORRECTION REQUIRED: The previous slide failed a contract check: "
                f"{exc}. Regenerate the complete slide and satisfy the plan exactly."
            )

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
