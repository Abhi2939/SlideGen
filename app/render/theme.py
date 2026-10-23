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

BLUSH = replace(DEFAULT, name="blush")

THEMES = {"default": DEFAULT, "blush": BLUSH}


def get_theme(name: str | None) -> Theme:
    """Unknown theme names fall back to the default instead of failing."""
    return THEMES.get(name or "default", DEFAULT)
