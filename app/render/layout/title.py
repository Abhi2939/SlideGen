from pptx.util import Inches

from app.render import fit
from app.render.assets import add_asset
from app.render.common import add_rect, add_text, new_slide
from app.render.theme import CONTENT_W, GAP_M, MARGIN_X, SLIDE_H

TITLE_SIZES = (48, 44, 40, 36, 32)
SUBTITLE_SIZE = 22


def render(prs, data, theme):
    slide = new_slide(prs, theme, bg=theme.primary)
    is_blush = theme.name == "blush"
    w = int(CONTENT_W * (0.9 if is_blush else 0.8))
    if add_asset(slide, "hero", 8.5, 1.35, 4.3, 5.5, crop=True):
        w = Inches(7.1)
    x = Inches(0.9) if is_blush else MARGIN_X

    size = fit.choose_size(TITLE_SIZES, lambda s: fit.text_height(data.title, s, w) <= Inches(2.6))
    th = fit.text_height(data.title, size, w)
    sh = fit.text_height(data.subtitle, SUBTITLE_SIZE, w) if data.subtitle else 0
    block_h = th + ((GAP_M + sh) if data.subtitle else 0)
    top = (SLIDE_H - block_h) // 2

    add_rect(slide, x, top - Inches(0.4), Inches(1.0), Inches(0.08), theme.accent)
    add_text(
        slide,
        x,
        top,
        w,
        th,
        data.title,
        size_pt=size,
        color=theme.on_primary,
        font=theme.font_heading,
        bold=True,
    )
    if data.subtitle:
        add_text(
            slide,
            x,
            top + th + GAP_M,
            w,
            sh,
            data.subtitle,
            size_pt=SUBTITLE_SIZE,
            color=theme.surface,
            font=theme.font_body,
        )
    return slide
