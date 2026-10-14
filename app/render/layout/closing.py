from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, new_slide
from app.render.beauty import add_blush_product_art
from app.render.theme import CONTENT_W, GAP_M, SLIDE_H, SLIDE_W

TITLE_SIZES = (44, 40, 36, 32, 28)
SUBTITLE_SIZES = (22, 20, 18, 16)


def render(prs, data, theme):
    slide = new_slide(prs, theme, bg=theme.primary)
    is_blush = theme.name == "blush"
    w = int(CONTENT_W * (0.53 if is_blush else 0.86))
    x = Inches(0.9) if is_blush else (SLIDE_W - w) // 2
    title_size = fit.choose_size(
        TITLE_SIZES,
        lambda size: fit.text_height(data.title, size, w) <= Inches(1.8),
    )
    title_h = fit.text_height(data.title, title_size, w)
    subtitle_size = fit.choose_size(
        SUBTITLE_SIZES,
        lambda size: not data.subtitle
        or fit.text_height(data.subtitle, size, w) <= Inches(0.9),
    )
    subtitle_h = fit.text_height(data.subtitle, subtitle_size, w) if data.subtitle else 0
    contact_h = Inches(0.35) if data.contact else 0
    gap_sub = GAP_M if data.subtitle else 0
    gap_contact = Inches(0.45) if data.contact else 0
    block_h = title_h + gap_sub + subtitle_h + gap_contact + contact_h
    top = (SLIDE_H - block_h) // 2

    add_rect(slide, x, top - Inches(0.36), Inches(0.9), Inches(0.07), theme.accent)
    add_text(
        slide, x, top, w, title_h, data.title,
        size_pt=title_size, color=theme.on_primary,
        font=theme.font_heading, bold=True, align=PP_ALIGN.CENTER,
    )
    y = top + title_h
    if data.subtitle:
        y += GAP_M
        add_text(
            slide, x, y, w, subtitle_h, data.subtitle,
            size_pt=subtitle_size, color=theme.surface,
            font=theme.font_body, align=PP_ALIGN.CENTER,
        )
        y += subtitle_h
    if data.contact:
        y += Inches(0.45)
        add_text(
            slide, x, y, w, contact_h, data.contact,
            size_pt=14, color=theme.accent, font=theme.font_body,
            align=PP_ALIGN.CENTER,
        )
    if is_blush:
        add_blush_product_art(slide, theme, Inches(8.45), Inches(1.55), 1.0)
    return slide
