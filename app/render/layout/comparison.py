from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, add_title, new_slide
from app.render.theme import BODY_H, BODY_TOP, CONTENT_W, GAP_M, GAP_S, MARGIN_X

POINT_SIZES = (17, 15, 13, 11)


def render(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)
    n = len(data.items)
    gap = Inches(0.22)
    card_w = int((CONTENT_W - gap * (n - 1)) / n)
    card_h = min(BODY_H, Inches(5.15))
    top = BODY_TOP + max(0, (BODY_H - card_h) // 2)

    def point_height(size, item):
        return sum(fit.text_height(point, size, card_w - Inches(0.7)) for point in item.points)

    def fits(size):
        for item in data.items:
            head_h = fit.text_height(item.name, 19, card_w - Inches(0.48))
            required = head_h + Inches(0.55) + point_height(size, item) + GAP_S * (len(item.points) - 1)
            if required > card_h - Inches(0.45):
                return False
        return True

    size = fit.choose_size(POINT_SIZES, fits)
    for idx, item in enumerate(data.items):
        x = MARGIN_X + idx * (card_w + gap)
        add_rect(slide, x, top, card_w, card_h, theme.surface, MSO_SHAPE.ROUNDED_RECTANGLE)
        pad = Inches(0.24)
        text_x = x + pad
        text_w = card_w - 2 * pad
        add_rect(slide, text_x, top + Inches(0.22), Inches(0.48), Inches(0.06), theme.accent)
        head_h = fit.text_height(item.name, 19, text_w)
        points_h = sum(fit.text_height(point, size, text_w - Inches(0.22)) for point in item.points)
        content_h = Inches(0.55) + head_h + Inches(0.2) + points_h + GAP_S * len(item.points)
        content_top = top + max(Inches(0.2), (card_h - content_h) // 2)
        add_text(
            slide, text_x, content_top, text_w, max(head_h, Inches(0.32)), item.name,
            size_pt=19, color=theme.primary, font=theme.font_heading, bold=True,
        )
        y = content_top + head_h + Inches(0.2)
        for point in item.points:
            point_h = fit.text_height(point, size, text_w - Inches(0.22))
            marker = Inches(0.1)
            add_rect(slide, text_x, y + Inches(0.08), marker, marker, theme.accent, MSO_SHAPE.OVAL)
            add_text(
                slide, text_x + Inches(0.2), y, text_w - Inches(0.2), point_h, point,
                size_pt=size, color=theme.text, font=theme.font_body,
            )
            y += point_h + GAP_S
    return slide
