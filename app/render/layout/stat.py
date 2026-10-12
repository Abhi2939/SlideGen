from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, add_title, new_slide
from app.render.theme import BODY_H, BODY_TOP, CONTENT_W, GAP_M, MARGIN_X

VALUE_SIZES = (52, 46, 40, 34)


def render(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)
    n = len(data.stats)
    gap = Inches(0.2)
    card_w = int((CONTENT_W - gap * (n - 1)) / n)
    card_h = min(BODY_H, Inches(3.7))
    top = BODY_TOP + max(0, (BODY_H - card_h) // 2)

    for idx, stat in enumerate(data.stats):
        x = MARGIN_X + idx * (card_w + gap)
        add_rect(slide, x, top, card_w, card_h, theme.surface, MSO_SHAPE.ROUNDED_RECTANGLE)
        pad = Inches(0.18 if n > 2 else 0.28)
        text_x, text_w = x + pad, card_w - 2 * pad
        add_rect(slide, text_x, top + Inches(0.24), Inches(0.48), Inches(0.06), theme.accent)
        value_size = fit.choose_size(
            VALUE_SIZES,
            lambda size: fit.text_height(stat.value, size, text_w) <= Inches(1.0),
        )
        value_h = fit.text_height(stat.value, value_size, text_w)
        value_y = top + Inches(0.48)
        add_text(
            slide, text_x, value_y, text_w, value_h, stat.value,
            size_pt=value_size, color=theme.primary,
            font=theme.font_heading, bold=True, align=PP_ALIGN.CENTER,
        )
        label_size = 16 if n < 3 else 14
        label_y = value_y + value_h + Inches(0.18)
        label_h = min(Inches(0.8), fit.text_height(stat.label, label_size, text_w))
        add_text(
            slide, text_x, label_y, text_w, label_h, stat.label,
            size_pt=label_size, color=theme.text,
            font=theme.font_body, bold=True, align=PP_ALIGN.CENTER,
        )
        y = label_y + label_h
        if stat.context:
            context_h = min(Inches(0.65), fit.text_height(stat.context, 12, text_w))
            add_text(
                slide, text_x, y + Inches(0.1), text_w, context_h, stat.context,
                size_pt=12, color=theme.muted, font=theme.font_body,
                align=PP_ALIGN.CENTER,
            )
        tag_y = top + card_h - Inches(0.48)
        tag_w = min(text_w, Inches(1.45))
        add_rect(slide, text_x + (text_w - tag_w) // 2, tag_y, tag_w, Inches(0.28), theme.bg, MSO_SHAPE.ROUNDED_RECTANGLE)
        add_text(
            slide, text_x + (text_w - tag_w) // 2, tag_y + Inches(0.015), tag_w, Inches(0.24),
            stat.provenance.upper(), size_pt=9, color=theme.muted,
            font=theme.font_body, bold=True, align=PP_ALIGN.CENTER,
        )
    return slide
