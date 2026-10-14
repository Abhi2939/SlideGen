from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, new_slide
from app.render.theme import CONTENT_W, GAP_M, SLIDE_H, SLIDE_W

TITLE_SIZES = (42, 38, 34, 30, 26)
SUBTITLE_SIZES = (22, 20, 18, 16)


def render(prs, data, theme):
    slide = new_slide(prs, theme, bg=theme.primary)
    title_w = int(CONTENT_W * 0.88)
    title_x = (SLIDE_W - title_w) // 2
    title_size = fit.choose_size(
        TITLE_SIZES,
        lambda size: fit.text_height(data.title, size, title_w) <= Inches(2.2),
    )
    title_h = fit.text_height(data.title, title_size, title_w)
    subtitle_size = fit.choose_size(
        SUBTITLE_SIZES,
        lambda size: (
            not data.subtitle or fit.text_height(data.subtitle, size, title_w) <= Inches(1.0)
        ),
    )
    subtitle_h = fit.text_height(data.subtitle, subtitle_size, title_w) if data.subtitle else 0
    block_h = title_h + (GAP_M + subtitle_h if data.subtitle else 0)
    top = (SLIDE_H - block_h) // 2

    add_rect(slide, title_x, top - Inches(0.34), Inches(0.85), Inches(0.07), theme.accent)
    add_text(
        slide,
        title_x,
        top,
        title_w,
        title_h,
        data.title,
        size_pt=title_size,
        color=theme.on_primary,
        font=theme.font_heading,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    if data.subtitle:
        add_text(
            slide,
            title_x,
            top + title_h + GAP_M,
            title_w,
            subtitle_h,
            data.subtitle,
            size_pt=subtitle_size,
            color=theme.surface,
            font=theme.font_body,
            align=PP_ALIGN.CENTER,
        )
    return slide
