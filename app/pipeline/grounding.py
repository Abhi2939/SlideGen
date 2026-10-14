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
        r"\b(?:in[- ]house|manufactur(?:e|ed|ing)|produced for you|production process|"
        r"product creation|packag(?:e|ed|es|ing)|made in our)\b",
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
    "availability or purchase claims": re.compile(
        r"\b(?:available\s+(?:now|today)|"
        r"(?:try|shop|buy|purchase|order)\s+(?:our|the|your)\b.{0,35}\b(?:today|now)|"
        r"(?:order|buy)\s+now)\b",
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
        r"\bresearch\s+(?:shows|suggests|finds)\b|"
        r"\b(?:standard|traditional|conventional|off[- ]the[- ]shelf)\s+"
        r"(?:options?|products?|makeup|shades?)\s+(?:often|frequently|typically|usually)\s+"
        r"(?:fail|fails|struggle|struggles)\b|"
        r"\b(?:often|frequently|typically|usually)\s+fail\s+to\s+(?:align|match|fit)\b|"
        r"\b(?:finding|getting|matching)\b.{0,60}\b(?:difficult|hard|challenging)\b",
        re.IGNORECASE,
    ),
}

_PARAPHRASE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "your",
}
_PARAPHRASE_ALIASES = {
    "complement": {"match", "matched", "suit", "suits"},
    "formula": {"blend", "blended", "formulation"},
    "personalized": {"custom", "tailored"},
}


def _singularize_claim_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss") and len(token) > 4:
        return token[:-1]
    return token


def is_supported_paraphrase(claim: str, source_text: str) -> bool:
    """Check a quoted claim against source wording using conservative aliases.

    This is only a backstop for reviewer false positives. It requires every
    meaningful claim token to appear in the source or have a narrow wording
    equivalent; it does not replace the unsupported-claim checks.
    """
    claim_tokens = {
        _singularize_claim_token(token)
        for token in re.findall(r"[a-z0-9]+", claim.casefold())
        if token not in _PARAPHRASE_STOP_WORDS
    }
    source_tokens = set(re.findall(r"[a-z0-9]+", source_text.casefold()))
    if not claim_tokens:
        return False

    if re.search(r"\bonly\s+for\s+you\b|\bmade\s+only\s+for\s+you\b", source_text, re.I):
        source_tokens.update({"personalized", "unique"})
    if "formula" in claim_tokens and re.search(
        r"\bcustom[- ]blend(?:ed|ing)?\b", source_text, re.I
    ):
        source_tokens.add("formula")

    return all(
        token in source_tokens or bool(_PARAPHRASE_ALIASES.get(token, set()) & source_tokens)
        for token in claim_tokens
    )


def unsupported_claim_categories(text: str, source_text: str) -> list[str]:
    """Return common factual claim categories found only in generated text."""
    source = source_text.casefold()
    return [
        category
        for category, pattern in UNSUPPORTED_CLAIM_PATTERNS.items()
        if pattern.search(text) and not pattern.search(source)
    ]
