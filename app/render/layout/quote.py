from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, add_title, new_slide
from app.render.theme import BODY_H, BODY_TOP, CONTENT_W, SLIDE_W

QUOTE_SIZES = (34, 30, 26, 22, 18)


def render(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)
    w = int(CONTENT_W * 0.82)
    x = (SLIDE_W - w) // 2
    quote_h = Inches(2.5)
    size = fit.choose_size(
        QUOTE_SIZES,
        lambda candidate: fit.text_height(data.quote, candidate, w) <= quote_h,
    )
    qh = fit.text_height(data.quote, size, w)
    attribution = "AI-generated" if data.provenance == "generated" else data.attribution
    attribution_h = Inches(0.36) if attribution else 0
    tag_h = Inches(0.3)
    total_h = qh + Inches(0.34) + attribution_h + Inches(0.28) + tag_h
    top = BODY_TOP + max(0, (BODY_H - total_h) // 2)

    add_rect(slide, x, top, Inches(0.65), Inches(0.06), theme.accent)
    add_text(
        slide, x, top + Inches(0.2), w, qh, data.quote,
        size_pt=size, color=theme.primary, font=theme.font_heading,
        bold=True, align=PP_ALIGN.CENTER,
    )
    y = top + Inches(0.2) + qh + Inches(0.14)
    if attribution:
        add_text(
            slide, x, y, w, attribution_h, attribution,
            size_pt=14, color=theme.muted, font=theme.font_body,
            align=PP_ALIGN.CENTER,
        )
        y += attribution_h + Inches(0.18)
    tag_w = Inches(1.35)
    tag_x = x + (w - tag_w) // 2
    add_rect(slide, tag_x, y, tag_w, tag_h, theme.surface)
    add_text(
        slide, tag_x, y + Inches(0.015), tag_w, Inches(0.25), data.provenance.upper(),
        size_pt=9, color=theme.muted, font=theme.font_body,
        bold=True, align=PP_ALIGN.CENTER,
    )
    return slide
