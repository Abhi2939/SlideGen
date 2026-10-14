"""Small deterministic helpers for concise, explicitly branded briefs."""

from dataclasses import dataclass
import re


_BRAND_LABEL = re.compile(r"\b(?:startup|brand)(?:\s+name)?\s*[:=]?\s*([^,\n.;]+)", re.IGNORECASE)
_EXPLICIT_SLIDE_COUNT = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]?"
    r"(?:slides?|pages?)\b",
    re.IGNORECASE,
)
_BEAUTY_PRODUCTS = ("foundation", "lipstick", "skincare")


@dataclass(frozen=True)
class ShortBeautyBrief:
    brand: str
    facts: tuple[str, ...]


def short_beauty_brief(prompt: str) -> ShortBeautyBrief | None:
    """Recognize a compact cosmetics brief with a brand label and source facts."""
    if len(prompt) > 500 or _EXPLICIT_SLIDE_COUNT.search(prompt):
        return None
    brand_match = _BRAND_LABEL.search(prompt)
    if not brand_match:
        return None
    if sum(product in prompt.casefold() for product in _BEAUTY_PRODUCTS) < 2:
        return None

    raw_brand = brand_match.group(1).strip(" \t:-")
    if not raw_brand:
        return None
    brand = raw_brand if any(char.isupper() for char in raw_brand) else raw_brand.title()
    remaining = (prompt[: brand_match.start()] + " " + prompt[brand_match.end() :]).strip(" \t,:;-")
    facts = tuple(
        sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", remaining) if sentence.strip()
    )
    return ShortBeautyBrief(brand=brand, facts=facts) if facts else None
