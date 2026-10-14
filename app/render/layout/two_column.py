from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

from app.render import fit
from app.render.common import add_rect, add_text, add_title, new_slide
from app.render.theme import BODY_H, BODY_TOP, CONTENT_W, GAP_M, GAP_S, MARGIN_X

BODY_SIZES = (18, 16, 14, 12)


def render(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)
    gap = GAP_M
    card_w = int((CONTENT_W - gap) / 2)
    card_h = BODY_H
    columns = (data.left, data.right)

    def block_heights(size, column):
        return [
            fit.text_height(b.text, size, card_w - Inches(0.62))
            + (
                GAP_S // 2 + fit.text_height(b.detail, size - 2, card_w - Inches(0.62))
                if b.detail
                else 0
            )
            for b in column.bullets
        ]

    def fits(size):
        for column in columns:
            title_h = fit.text_height(column.heading, 20, card_w - Inches(0.56))
            total = (
                title_h
                + Inches(0.34)
                + sum(block_heights(size, column))
                + GAP_S * max(0, len(column.bullets) - 1)
            )
            if total > card_h - Inches(0.42):
                return False
        return True

    size = fit.choose_size(BODY_SIZES, fits)
    for idx, column in enumerate(columns):
        x = MARGIN_X + idx * (card_w + gap)
        add_rect(slide, x, BODY_TOP, card_w, card_h, theme.surface, MSO_SHAPE.ROUNDED_RECTANGLE)
        pad = Inches(0.24)
        text_x = x + pad
        text_w = card_w - 2 * pad
        heading_h = fit.text_height(column.heading, 20, text_w)
        add_text(
            slide,
            text_x,
            BODY_TOP + pad,
            text_w,
            max(heading_h, Inches(0.32)),
            column.heading,
            size_pt=20,
            color=theme.primary,
            font=theme.font_heading,
            bold=True,
        )
        y = BODY_TOP + pad + heading_h + Inches(0.22)
        for bullet in column.bullets:
            h = fit.text_height(bullet.text, size, text_w - Inches(0.26))
            marker = Inches(0.12)
            add_rect(slide, text_x, y + Inches(0.08), marker, marker, theme.accent, MSO_SHAPE.OVAL)
            bullet_x = text_x + Inches(0.24)
            bullet_w = text_w - Inches(0.24)
            add_text(
                slide,
                bullet_x,
                y,
                bullet_w,
                h,
                bullet.text,
                size_pt=size,
                color=theme.text,
                font=theme.font_body,
            )
            y += h
            if bullet.detail:
                detail_h = fit.text_height(bullet.detail, size - 2, bullet_w)
                add_text(
                    slide,
                    bullet_x,
                    y + GAP_S // 3,
                    bullet_w,
                    detail_h,
                    bullet.detail,
                    size_pt=size - 2,
                    color=theme.muted,
                    font=theme.font_body,
                )
                y += GAP_S // 3 + detail_h
            y += GAP_S
    return slide
