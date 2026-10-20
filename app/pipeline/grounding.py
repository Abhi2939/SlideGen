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
    "unsupported company scope or operations": re.compile(
        r"\b(?:core competencies|product portfolio|product range|product line|"
        r"product offerings|portfolio of|R\s*&\s*D (?:capabilities|focus|investment)|"
        r"production capacity|global supply chain|supply chain reach|global presence|"
        r"global reach|international operations|supply chain|industrial solutions|consumer electronics|"
        r"smartphones|IoT devices|specializ(?:e[sd]?|ing)\s+in|product focus|"
        r"market presence|market expansion|strategic initiatives|new product development|"
        r"innovation roadmap|mission statement|company vision|company values|"
        r"leadership team|customer base|customer support|customer service|strengths?|"
        r"manufacturing capabilities|quality standards|innovative|"
        r"R\s*&\s*D|contact us|partnership)\b",
        re.IGNORECASE,
    ),
}

_TIER3_MARKET_PATTERN = re.compile(
    r"\bmarket\s+(?:size|share|demand|growth)\b|"
    r"\bresearch\s+(?:shows|suggests|finds)\b",
    re.IGNORECASE,
)

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


def unsupported_claim_categories(
    text: str,
    source_text: str,
    *,
    allow_structural_inference: bool = False,
    allow_problem_framing: bool = False,
) -> list[str]:
    """Return common unsupported claim categories found in generated text.

    Workflow slides may use qualitative conceptual steps, so assessment and
    selection wording is allowed there. Specific technology, operations,
    shipping, and market claims remain guarded.
    """
    source = source_text.casefold()
    allowed_categories = (
        {"assessment or diagnostic processes", "shade selection processes"}
        if allow_structural_inference
        else set()
    )
    if allow_problem_framing and not _TIER3_MARKET_PATTERN.search(text):
        allowed_categories.add("unsupported market or consumer behavior")
    return [
        category
        for category, pattern in UNSUPPORTED_CLAIM_PATTERNS.items()
        if category not in allowed_categories
        and pattern.search(text)
        and not pattern.search(source)
    ]
