import logging

from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, add_title, new_slide
from app.render.theme import BODY_H, BODY_TOP, CONTENT_W, GAP_M, GAP_S, MARGIN_X

log = logging.getLogger(__name__)

BODY_SIZES = (22, 20, 18, 16)
DETAIL_DELTA = 4
MARKER = Inches(0.16)
MARKER_COL = Inches(0.4)


def render(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)

    text_w = CONTENT_W - MARKER_COL
    n = len(data.bullets)

    def heights(size):
        out = []
        for b in data.bullets:
            h = fit.text_height(b.text, size, text_w)
            if b.detail:
                h += GAP_S // 2 + fit.text_height(b.detail, size - DETAIL_DELTA, text_w)
            out.append(h)
        return out

    def fits(size):
        return sum(heights(size)) + GAP_M * (n - 1) <= BODY_H

    sizes = (28, 26, 24, *BODY_SIZES) if theme.name == "blush" and n <= 3 else BODY_SIZES
    size = fit.choose_size(sizes, fits)
    hs = heights(size)
    if not fits(size):
        log.warning("bullets slide overflows even at %spt", size)

    spare = BODY_H - sum(hs)
    gap = min(Inches(1.0), max(GAP_M, int(spare * 0.5 / max(n - 1, 1))))

    used_h = sum(hs) + gap * (n - 1)
    y = BODY_TOP + max(0, (BODY_H - used_h) // 2)
    for b, h in zip(data.bullets, hs):
        marker_y = y + (fit.line_height(size) - MARKER) // 2
        add_rect(slide, MARGIN_X, marker_y, MARKER, MARKER, theme.accent, MSO_SHAPE.OVAL)

        tx = MARGIN_X + MARKER_COL
        th = fit.text_height(b.text, size, text_w)
        add_text(
            slide,
            tx,
            y,
            text_w,
            th,
            b.text,
            size_pt=size,
            color=theme.text,
            font=theme.font_body,
        )
        if b.detail:
            dsize = size - DETAIL_DELTA
            dy = y + th + GAP_S // 2
            add_text(
                slide,
                tx,
                dy,
                text_w,
                fit.text_height(b.detail, dsize, text_w),
                b.detail,
                size_pt=dsize,
                color=theme.muted,
                font=theme.font_body,
            )
        y += h + gap
    return slide
