"""Rebuild a bounded source-only draft after rejecting invalid AI content."""

import re

from app.core.visuals import content_kind, source_units
from app.pipeline.briefs import beauty_brand


def source_only_content(prompt, output_model):
    """Use original declarative company copy; never reuse a rejected completion.

    General-topic requests and briefs that cannot fit the original contract still
    fail. Every rebuilt result must pass the same source and numeric validators.
    """
    from app.pipeline.draft import _source_points, requested_slide_count

    if re.match(r"(?:create|make|prepare|design|generate|build|what|how|explain)\b", prompt, re.I):
        return None
    named = re.match(
        r"([\w'’&-]+(?:[ \t]+[\w'’&-]+){0,3})\s+(?:is|offers|provides|makes|builds|sells)\s+",
        prompt,
        re.I,
    )
    if not named or named[1].casefold() in {
        "my company",
        "our company",
        "our startup",
        "my startup",
        "this brand",
        "we",
        "it",
    }:
        return None
    facts = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", prompt):
        sentence = re.sub(r"^[-*]\s+", "", sentence.strip())
        if not sentence:
            continue
        if re.match(r"(?:audience|tone|theme|style|references?)\s*:", sentence, re.I):
            continue
        if re.match(
            r"(?:create|make|generate|prepare|design|build|please|use|keep)\b", sentence, re.I
        ) and re.search(r"\b(?:deck|slides?|presentation)\b", sentence, re.I):
            continue
        facts.extend(_source_points(sentence, max_chars=380, max_words=70))
    if not facts or len(facts) > 12:
        return None
    count = requested_slide_count(prompt) - 2
    sections = [
        {"title": "Planning priorities", "points": [], "source_fact_ids": []} for _ in range(count)
    ]
    titles = {
        "collection": "Product range",
        "personalization": "Personalization",
        "launch": "Launch plan",
        "growth": "Growth plan",
    }
    for fact_id, fact in enumerate(facts):
        for unit in source_units(fact):
            points = _source_points(unit)
            available = [
                i for i, section in enumerate(sections) if len(section["points"]) + len(points) <= 4
            ]
            if not available:
                return None
            target = min(
                available,
                key=lambda i: (bool(sections[i]["points"]), len(sections[i]["points"]), i),
            )
            section = sections[target]
            if not section["points"]:
                section["title"] = titles.get(content_kind(unit), "Overview")
            else:
                section["title"] = "Overview"
            section["points"].extend(points)
            section["source_fact_ids"].append(fact_id)
    for section in sections:
        if not section["points"]:
            section["points"] = [
                "Proposed next step: review the supplied details and agree on priorities."
            ]
    data = {
        "title": beauty_brand(prompt) or named[1],
        "subtitle": _source_points(facts[0], max_chars=100, max_words=18)[0],
        "facts": facts,
        "sections": sections,
        "closing_title": "Next steps",
        "closing_text": "Review the supplied plan and agree on the next steps.",
    }
    try:
        return output_model.model_validate(data)
    except ValueError:
        return None
