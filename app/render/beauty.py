"""Editable, native PowerPoint artwork for the Blush brand theme."""

from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from app.render.common import add_rect, add_text
from app.render.theme import Theme


def add_blush_product_art(slide, theme: Theme, x, y, scale: float = 1.0) -> None:
    """Draw a small cosmetic trio with PowerPoint shapes, no external images."""
    unit = Inches(scale)
    add_rect(
        slide,
        x + unit * 0.05,
        y + unit * 0.05,
        unit * 3.8,
        unit * 3.8,
        theme.surface,
        MSO_SHAPE.OVAL,
    )

    # Foundation bottle
    add_rect(
        slide,
        x + unit * 0.42,
        y + unit * 1.25,
        unit * 0.82,
        unit * 2.0,
        theme.on_primary,
        MSO_SHAPE.ROUNDED_RECTANGLE,
    )
    add_rect(
        slide,
        x + unit * 0.56,
        y + unit * 0.84,
        unit * 0.54,
        unit * 0.48,
        theme.accent,
        MSO_SHAPE.RECTANGLE,
    )
    add_text(
        slide,
        x + unit * 0.49,
        y + unit * 2.0,
        unit * 0.68,
        unit * 0.6,
        "B",
        size_pt=20 * scale,
        color=theme.primary,
        font=theme.font_heading,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    add_text(
        slide,
        x + unit * 0.17,
        y + unit * 3.38,
        unit * 1.3,
        unit * 0.28,
        "FOUNDATION",
        size_pt=8 * scale,
        color=theme.primary,
        font=theme.font_body,
        bold=True,
        align=PP_ALIGN.CENTER,
    )

    # Lipstick tube
    add_rect(
        slide,
        x + unit * 1.67,
        y + unit * 1.6,
        unit * 0.62,
        unit * 1.65,
        theme.primary,
        MSO_SHAPE.ROUNDED_RECTANGLE,
    )
    add_rect(
        slide,
        x + unit * 1.77,
        y + unit * 0.94,
        unit * 0.42,
        unit * 0.82,
        theme.chart_colors[1],
        MSO_SHAPE.TRAPEZOID,
    )
    add_rect(
        slide,
        x + unit * 1.59,
        y + unit * 3.1,
        unit * 0.78,
        unit * 0.3,
        theme.chart_colors[3],
        MSO_SHAPE.RECTANGLE,
    )
    add_text(
        slide,
        x + unit * 1.39,
        y + unit * 3.48,
        unit * 1.25,
        unit * 0.28,
        "LIPSTICK",
        size_pt=8 * scale,
        color=theme.primary,
        font=theme.font_body,
        bold=True,
        align=PP_ALIGN.CENTER,
    )

    # Skincare jar
    add_rect(
        slide,
        x + unit * 2.78,
        y + unit * 2.05,
        unit * 0.92,
        unit * 1.2,
        theme.chart_colors[2],
        MSO_SHAPE.ROUNDED_RECTANGLE,
    )
    add_rect(
        slide,
        x + unit * 2.86,
        y + unit * 1.76,
        unit * 0.76,
        unit * 0.36,
        theme.chart_colors[3],
        MSO_SHAPE.ROUNDED_RECTANGLE,
    )
    add_text(
        slide,
        x + unit * 2.66,
        y + unit * 3.38,
        unit * 1.15,
        unit * 0.28,
        "SKINCARE",
        size_pt=8 * scale,
        color=theme.primary,
        font=theme.font_body,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
