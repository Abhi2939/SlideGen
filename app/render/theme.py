from dataclasses import dataclass, replace

from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

# 16:9 slide
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

# Spacing grid
MARGIN_X = Inches(0.55)
MARGIN_TOP = Inches(0.28)
MARGIN_BOTTOM = Inches(0.6)
GAP_S = Inches(0.15)
GAP_M = Inches(0.3)
GAP_L = Inches(0.6)

CONTENT_W = SLIDE_W - 2 * MARGIN_X
TITLE_H = Inches(0.72)
BODY_TOP = Inches(1.6)
BODY_H = SLIDE_H - BODY_TOP - MARGIN_BOTTOM


def rgb(hex_str: str) -> RGBColor:
    return RGBColor.from_string(hex_str.lstrip("#").upper())


@dataclass(frozen=True)
class TypeScale:
    display: Pt = Pt(44)
    title: Pt = Pt(32)
    subtitle: Pt = Pt(20)
    body: Pt = Pt(20)
    small: Pt = Pt(16)
    caption: Pt = Pt(12)
    stat: Pt = Pt(54)


@dataclass(frozen=True)
class Theme:
    name: str
    bg: RGBColor
    surface: RGBColor
    text: RGBColor
    muted: RGBColor
    primary: RGBColor
    accent: RGBColor
    on_primary: RGBColor
    chart_colors: tuple
    font_heading: str = "Calibri"
    font_body: str = "Calibri"
    type: TypeScale = TypeScale()


DEFAULT = Theme(
    name="default",
    bg=rgb("F3F4F6"),
    surface=rgb("FFFFFF"),
    text=rgb("333333"),
    muted=rgb("6B7280"),
    primary=rgb("1B365D"),
    accent=rgb("7DBE1E"),
    on_primary=rgb("FFFFFF"),
    chart_colors=(
        rgb("1B365D"),
        rgb("7DBE1E"),
        rgb("F59E0B"),
        rgb("263FB8"),
        rgb("EF4444"),
        rgb("94A3B8"),
    ),
)

EDITORIAL_TYPE = TypeScale(
    display=Pt(54),
    title=Pt(36),
    subtitle=Pt(22),
    body=Pt(18),
    small=Pt(16),
    caption=Pt(14),
    stat=Pt(72),
)


def contrast(first, second):
    def luminance(color):
        channels = [value / 255 for value in color]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    a, b = sorted((luminance(first), luminance(second)))
    return (b + 0.05) / (a + 0.05)


def _curated(name, bg, surface, primary, accent, text, muted, heading="Calibri"):
    theme = replace(
        DEFAULT,
        name=name,
        bg=rgb(bg),
        surface=rgb(surface),
        primary=rgb(primary),
        accent=rgb(accent),
        text=rgb(text),
        muted=rgb(muted),
        font_heading=heading,
        type=EDITORIAL_TYPE,
        chart_colors=tuple(map(rgb, (primary, accent, muted))),
    )
    for foreground in (theme.text, theme.muted, theme.primary, theme.accent):
        if min(contrast(foreground, theme.bg), contrast(foreground, theme.surface)) < 4.5:
            raise ValueError(f"theme {name} fails body contrast")
    if contrast(theme.on_primary, theme.primary) < 4.5:
        raise ValueError(f"theme {name} fails inverse contrast")
    return theme


BLUSH = _curated("beauty", "FBF3EB", "F5DED9", "552B40", "9B4935", "39252F", "70545D", "Georgia")
FINTECH = _curated("fintech", "F0F5F7", "DFEAF0", "153C59", "17665C", "19303F", "4B606B")
HEALTH = _curated("health", "F0F6F1", "DDEBE2", "1C5149", "7D4932", "203C36", "486258")
EDTECH = _curated("edtech", "F5F2FC", "E8E0F5", "473080", "8B3E3B", "302640", "625271")
CLIMATE = _curated("climate", "F4F3E8", "E4E6D1", "2D5138", "805220", "293A2C", "536044")
BOLD_DARK = replace(
    FINTECH,
    name="bold-dark",
    bg=rgb("171B29"),
    surface=rgb("252B3E"),
    text=rgb("FFF8EF"),
    muted=rgb("CBC8D4"),
    primary=rgb("FFD1B0"),
    accent=rgb("E7BDF3"),
    on_primary=rgb("171B29"),
)

THEMES = {t.name: t for t in (FINTECH, BLUSH, HEALTH, EDTECH, CLIMATE, BOLD_DARK)}
THEMES.update({"default": DEFAULT, "blush": BLUSH, "soft": BLUSH})


def theme_for_topic(topic: str) -> str:
    import re

    for pattern, name in (
        (r"beauty|blush|makeup|cosmetic|skincare|lipstick|soft|romantic", "beauty"),
        (r"health|medical|wellness|clinic", "health"),
        (r"edtech|education|learning|school|student|tutor", "edtech"),
        (r"climate|solar|sustainab|renewable|environment", "climate"),
        (r"bold|gaming|night|dark", "bold-dark"),
    ):
        if re.search(pattern, topic, re.I):
            return name
    return "fintech"


def get_theme(name: str | None) -> Theme:
    """Unknown theme names fall back to the default instead of failing."""
    return THEMES.get(name or "default", DEFAULT)
