import math

from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, add_title, new_slide
from app.render.theme import BODY_H, BODY_TOP, CONTENT_W, GAP_M, GAP_S, MARGIN_X


def render(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)
    subtitle_h = Inches(0.42) if data.subtitle else 0
    if data.subtitle:
        add_text(
            slide, MARGIN_X, BODY_TOP, CONTENT_W, subtitle_h, data.subtitle,
            size_pt=16, color=theme.muted, font=theme.font_body,
        )

    n = len(data.steps)
    cols = min(n, 4)
    rows = math.ceil(n / cols)
    top = BODY_TOP + subtitle_h + (GAP_S if data.subtitle else 0)
    available_h = BODY_H - (top - BODY_TOP)
    gap_x = Inches(0.2)
    gap_y = Inches(0.22)
    card_w = int((CONTENT_W - gap_x * (cols - 1)) / cols)
    card_h = int((available_h - gap_y * (rows - 1)) / rows)

    for index, step in enumerate(data.steps):
        row, col = divmod(index, cols)
        x = MARGIN_X + col * (card_w + gap_x)
        y = top + row * (card_h + gap_y)
        add_rect(slide, x, y, card_w, card_h, theme.surface, MSO_SHAPE.ROUNDED_RECTANGLE)
        badge = Inches(0.36)
        badge_x = x + Inches(0.18)
        badge_y = y + Inches(0.18)
        add_rect(slide, badge_x, badge_y, badge, badge, theme.accent, MSO_SHAPE.OVAL)
        add_text(
            slide, badge_x, badge_y + Inches(0.015), badge, Inches(0.3), str(index + 1),
            size_pt=12, color=theme.on_primary, font=theme.font_body,
            bold=True, align=PP_ALIGN.CENTER,
        )
        inner_x = x + Inches(0.18)
        inner_w = card_w - Inches(0.36)
        title_size = 17 if cols <= 3 else 14
        title_y = badge_y + badge + Inches(0.12)
        title_h = Inches(0.56)
        add_text(
            slide, inner_x, title_y, inner_w, title_h, step.title,
            size_pt=title_size, color=theme.primary,
            font=theme.font_heading, bold=True,
        )
        desc_y = title_y + title_h + Inches(0.04)
        desc_h = max(Inches(0.35), y + card_h - desc_y - Inches(0.18))
        desc_size = 14 if cols <= 3 else 12
        add_text(
            slide, inner_x, desc_y, inner_w, desc_h, step.description,
            size_pt=desc_size, color=theme.text, font=theme.font_body,
        )
    return slide
