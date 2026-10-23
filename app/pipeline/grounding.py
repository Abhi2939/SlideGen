"""Deterministic checks for common unsupported factual additions."""

import re


UNSUPPORTED_CLAIM_PATTERNS = {
    "unsupported product specifications": re.compile(
        r"\b(?:easy integration|affordable pricing|24/7 support|round[- ]the[- ]clock support|"
        r"join our beta|subscribe to updates|user[- ]friendly interface|"
        r"automated risk scoring|instant fund disbursement|instant funding|"
        r"questionnaire|visual guide|undertone identification)\b",
        re.I,
    ),
    "unsupported calls to action": re.compile(
        r"\b(?:follow us|follow (?:our|us on)|join (?:our|the) community|"
        r"book (?:a|your) (?:demo|call|consultation)|schedule (?:a|your) (?:demo|call)|"
        r"download (?:our|the) app|visit our|shop now|buy now|order now|"
        r"join (?:our|the) waitlist|stay tuned|ready to launch|"
        r"sign up for early access|contact sales|sales demo)\b",
        re.IGNORECASE,
    ),
    "unsupported product benefit or guarantee": re.compile(
        r"\b(?:confidence in every purchase|boosts? confidence|unmatched quality|"
        r"guarantee(?:s|d)?|risk[- ]free|save time|time savings|cost savings|"
        r"instant(?:ly)?(?:\s+\w+){0,2}\s+(?:recommendations?|results?|matching)|"
        r"priority access|exclusive (?:launch )?updates)\b",
        re.IGNORECASE,
    ),
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
        r"\b(?:ship(?:s|ped|ping)?|deliver(?:s|ed|y|ing)?|fulfill(?:s|ed|ment|ing)?)\b",
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
        r"\b(?:large addressable market|significant SME base|growing demand|rising demand|"
        r"increasing adoption)\b|"
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
    r"\b(?:large addressable market|significant SME base|growing demand|rising demand|"
    r"increasing adoption)\b|"
    r"\bmarket\s+(?:size|share|demand|growth)\b|"
    r"\bresearch\s+(?:shows|suggests|finds)\b",
    re.IGNORECASE,
)
_INVESTOR_PROMPT_PATTERN = re.compile(
    r"\b(?:investor|fundraising|seed round|pitch deck)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_INVESTOR_PATTERNS = {
    "unsupported product integrations or technical mechanisms": re.compile(
        r"\b(?:integrat(?:e|es|ed|ing|ion|ions)|connect(?:s|ed|ing)?\s+with|"
        r"sync(?:s|ed|ing)?\s+with|API|configurable rules|matching logic|"
        r"data ingestion|exception handling|audit[- ]ready|multiple sources)\b",
        re.IGNORECASE,
    ),
    "unsupported product outcomes or performance claims": re.compile(
        r"\b(?:time[- ]consuming|time savings?|cost savings?|error rates?|"
        r"accuracy|efficiency|scalability issues|significant improvements?|"
        r"accurate|efficient|error risk|error reduction|"
        r"(?:hours?|minutes?|days?)\s+(?:required|per|each|after|before|saved)\b|"
        r"reduce(?:s|d)?\s+(?:time|cost|errors?)|save(?:s|d)?\s+time)\b",
        re.IGNORECASE,
    ),
    "unsupported pricing or go-to-market details": re.compile(
        r"\b(?:freemium|free trial|pilot program|channel partners?|direct sales|"
        r"revenue model|revenue breakdown|subscription(?:[- ]based)? pricing|per[- ]seat|"
        r"tiered plans|upsell|add[- ]on services|audit support|"
        r"transaction fee|pricing tier|go[- ]to[- ]market strategy)\b",
        re.IGNORECASE,
    ),
    "unsupported funding allocation": re.compile(r"\b(?:funds for|use of funds)\b", re.I),
    "unsupported market evidence or traction": re.compile(
        r"\b(?:growing demand|rising demand|large (?:accounting firm )?base|"
        r"large number of accounting firms|market opportunity|rapidly growing market|"
        r"accounting firms of all sizes|firms of all sizes|all company sizes|"
        r"customer traction|strong adoption|high demand)\b",
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
        and (
            any(match.group().casefold() not in source for match in pattern.finditer(text))
            if category
            in {"unsupported calls to action", "unsupported product benefit or guarantee"}
            else not pattern.search(source)
        )
    ]


def unsupported_investor_claim_categories(text: str, source_text: str) -> list[str]:
    """Flag investor-pitch specifics that require source evidence or placeholders."""
    if not _INVESTOR_PROMPT_PATTERN.search(source_text):
        return []
    return [
        category
        for category, pattern in _UNSUPPORTED_INVESTOR_PATTERNS.items()
        if pattern.search(text) and not pattern.search(source_text)
    ]


_SPECIFIC_PATTERNS = {
    "pricing": re.compile(
        r"\b(?:freemium|subscription(?:[- ]based)? pricing|per[- ]seat|pricing tiers?|plans start at|"
        r"tiered plans|upsell|add[- ]on services|audit support)\b",
        re.I,
    ),
    "traction": re.compile(
        r"\b(?:paying customers|enterprise customers|strong adoption|customer traction|profitable|monthly revenue)\b",
        re.I,
    ),
    "team credentials": re.compile(
        r"\b(?:ex[- ]\w+|award[- ]winning|experienced founders?|serial entrepreneurs?|PhD|previously (?:led|worked)|former \w+)\b",
        re.I,
    ),
    "funding terms": re.compile(
        r"\b(?:pre[- ]money|post[- ]money|valuation|equity stake|convertible note|SAFE agreement)\b",
        re.I,
    ),
    "named competitors": re.compile(
        r"\b(?:competes? with|competitors? (?:include|are)|rivals? (?:include|are))\s+[^.;\n]+",
        re.I,
    ),
}


def unsupported_specific_claim_categories(text: str, source_text: str) -> list[str]:
    """Specific commercial evidence needs the original request in any deck type.

    Placeholder instructions are scaffolding, not evidence. Check individual
    matches so one supplied pricing/traction phrase does not license another.
    """
    source = source_text.casefold()
    return [
        category
        for category, pattern in _SPECIFIC_PATTERNS.items()
        if any(match.group().casefold() not in source for match in pattern.finditer(text))
    ]
