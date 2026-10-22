"""Conservative source-name extraction for cosmetics briefs."""

import re


_BRAND_LABEL = re.compile(
    r"^\s*(?:(?:startup|brand)\s+name\s*[:=]?\s*|"
    r"(?:startup|brand)\s*[:=]\s*)([^,\n.;:]+)",
    re.IGNORECASE,
)
_BRAND_SUBJECT = re.compile(
    r"^\s*([\w'’&-]+(?:[ \t]+[\w'’&-]+){0,3})\s+is\s+(?:a|an)\s+"
    r"[^.!?\n]{0,80}\b(?:brand|startup|company)\b",
    re.IGNORECASE,
)
_BEAUTY_PRODUCTS = ("foundation", "lipstick", "skincare")
_GENERIC_SUBJECTS = {"my startup", "our startup", "my company", "our company", "this brand"}


def beauty_brand(prompt: str) -> str | None:
    """Recognize an explicit name without treating descriptive copy as metadata.

    This selects a source name and theme only. It never removes source text,
    replaces the analyzed fact inventory, or forces a slide count or outline.
    """
    if sum(bool(re.search(rf"\b{product}\b", prompt, re.I)) for product in _BEAUTY_PRODUCTS) < 2:
        return None
    match = _BRAND_LABEL.search(prompt) or _BRAND_SUBJECT.search(prompt)
    if match is None:
        return None
    name = match.group(1).strip(" \t:-")
    if not name or len(name) > 60 or name.casefold() in _GENERIC_SUBJECTS:
        return None
    return name if any(char.isupper() for char in name) else name.title()
