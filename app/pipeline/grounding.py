"""Deterministic checks for common unsupported factual additions."""

import re


UNSUPPORTED_CLAIM_PATTERNS = {
    "AI or algorithm claims": re.compile(
        r"\b(?:AI|artificial intelligence|algorithm(?:ic)?)\b", re.IGNORECASE
    ),
    "assessment or diagnostic processes": re.compile(
        r"\b(?:skin\s+(?:analysis|assessment)|we\s+assess|assessment|diagnos(?:e|is)|consultation)\b",
        re.IGNORECASE,
    ),
    "manufacturing or production processes": re.compile(
        r"\b(?:in[- ]house|manufactur(?:e|ed|ing)|produced for you|production process|made in our)\b",
        re.IGNORECASE,
    ),
    "shipping or delivery claims": re.compile(
        r"\b(?:ship(?:s|ped|ping)?|deliver(?:s|ed|y)?|fulfill(?:s|ed|ment)?)\b",
        re.IGNORECASE,
    ),
    "website or sample offers": re.compile(
        r"\b(?:website|online store|free sample(?: kit)?|sample kit|free trial|visit our|order now)\b",
        re.IGNORECASE,
    ),
    "shade selection processes": re.compile(
        r"\b(?:choose|chooses|chosen|select|selects|selection)\b.{0,35}\bshade\b|"
        r"\bshade\b.{0,20}\b(?:analysis|assessment|selection)\b",
        re.IGNORECASE,
    ),
    "unsupported market or consumer behavior": re.compile(
        r"\b(?:consumers?|customers?)\s+(?:often|frequently|typically|usually)\b|"
        r"\bmarket\s+(?:size|share|demand|growth)\b|"
        r"\bresearch\s+(?:shows|suggests|finds)\b",
        re.IGNORECASE,
    ),
}


def unsupported_claim_categories(text: str, source_text: str) -> list[str]:
    """Return common factual claim categories found only in generated text."""
    source = source_text.casefold()
    return [
        category
        for category, pattern in UNSUPPORTED_CLAIM_PATTERNS.items()
        if pattern.search(text) and not pattern.search(source)
    ]
