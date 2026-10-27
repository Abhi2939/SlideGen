"""Source-based content roles shared by draft planning and visual composition."""

import re


def content_kind(text):
    for kind, pattern in (
        ("growth", r"\bgrowth\b|\broadmap\b|\bUGC\b|\bsubscriptions?\b"),
        ("launch", r"\blaunch\b|\bquiz\b|\bwaitlist\b|\bcreator[- ]led\b"),
        (
            "personalization",
            r"\bpersonalization\b|\bundertone\b|\bskin tone\b|\bblended exclusively\b",
        ),
        (
            "collection",
            r"\bproducts?\b|\bcollection\b|\boffering\b|\bproduct range\b|\bfoundation\b",
        ),
    ):
        if re.search(pattern, text, re.I):
            return kind
    return None


def source_units(fact):
    """Separate an explicit product clause from its personalization clause."""
    if re.search(r"\boffering\b", fact, re.I):
        match = re.search(r"\bblended\s+exclusively\b", fact, re.I)
        if match and re.search(r"\bfor\s+your\b", fact[match.start() :], re.I):
            return [fact[: match.start()].strip(), fact[match.start() :].strip()]
    return [fact]
