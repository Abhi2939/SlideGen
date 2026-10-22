from dataclasses import dataclass

from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

# 16:9 slide
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

# Spacing grid
MARGIN_X = Inches(0.8)
MARGIN_TOP = Inches(0.7)
MARGIN_BOTTOM = Inches(0.6)
GAP_S = Inches(0.15)
GAP_M = Inches(0.3)
GAP_L = Inches(0.6)

CONTENT_W = SLIDE_W - 2 * MARGIN_X
TITLE_H = Inches(1.0)
BODY_TOP = MARGIN_TOP + TITLE_H + GAP_M
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
    bg=rgb("FFFFFF"),
    surface=rgb("F3F6FA"),
    text=rgb("1F2937"),
    muted=rgb("6B7280"),
    primary=rgb("0F2A43"),
    accent=rgb("14A39A"),
    on_primary=rgb("FFFFFF"),
    chart_colors=(
        rgb("0F2A43"),
        rgb("14A39A"),
        rgb("F59E0B"),
        rgb("6366F1"),
        rgb("EF4444"),
        rgb("94A3B8"),
    ),
)

BLUSH = Theme(
    name="blush",
    bg=rgb("FFF9FA"),
    surface=rgb("F7E8EC"),
    text=rgb("3E2B32"),
    muted=rgb("8B6B75"),
    primary=rgb("542E3B"),
    accent=rgb("D47B98"),
    on_primary=rgb("FFFFFF"),
    chart_colors=(
        rgb("542E3B"),
        rgb("D47B98"),
        rgb("EBC7D1"),
        rgb("B78A66"),
        rgb("A9B1A3"),
        rgb("F1D9C4"),
    ),
)

THEMES = {"default": DEFAULT, "blush": BLUSH}


def get_theme(name: str | None) -> Theme:
    """Unknown theme names fall back to the default instead of failing."""
    return THEMES.get(name or "default", DEFAULT)
