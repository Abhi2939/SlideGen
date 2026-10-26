"""Editable, measured templates without network or image dependencies."""

from pptx.util import Inches

from app.render.common import add_rect, add_text, new_slide
from app.render.measure import fitted_size, measure_text, TextNeedsSplit


def render(prs, data, theme, *, brand, number):
    cover = data.layout in {"title", "closing"}
    slide = new_slide(prs, theme, bg=theme.primary if cover else theme.bg)
    slide._element.cSld.set("name", "draft_" + data.layout)
    color = theme.on_primary if cover else theme.text

    def text(value, x, y, w, h, size, *, bold=False, name="Text", muted=False):
        if not value:
            return None
        font = theme.font_heading if bold else theme.font_body
        point = fitted_size(value, w, h, size, font, bold)
        shape = add_text(
            slide,
            Inches(x),
            Inches(y),
            Inches(w),
            Inches(h),
            value,
            size_pt=point,
            color=theme.muted if muted else color,
            font=font,
            bold=bold,
        )
        shape.name = name
        return shape

    bar = add_rect(slide, Inches(0.7), Inches(0.8), Inches(0.85), Inches(0.08), theme.accent)
    bar.name = "Layer:background:accent"
    if cover:
        text(data.title, 0.7, 2.0, 11.8, 2.5, 48, bold=True, name="Slide title")
        text(getattr(data, "subtitle", None), 0.7, 4.8, 11.6, 1.0, 24)
    else:
        text(data.title, 0.7, 1.0, 11.8, 1.1, 34, bold=True, name="Slide title")
        points = data.points
        for size in (24, 22, 20, 18):
            heights = [measure_text(p, 11.0, size, theme.font_body)[1] + 0.3 for p in points]
            if sum(heights) + 0.2 * (len(points) - 1) <= 4.1:
                break
        else:
            raise TextNeedsSplit("draft content needs another slide")
        y = 2.3 + max(0, (4.1 - sum(heights) - 0.2 * (len(points) - 1)) / 2)
        for index, (point, height) in enumerate(zip(points, heights)):
            card = add_rect(
                slide, Inches(0.7), Inches(y), Inches(11.9), Inches(height), theme.surface
            )
            card.name = f"Layer:card:{index}"
            text(point, 1.05, y + 0.15, 11.0, height - 0.3, size)
            y += height + 0.2
    text(brand[:45], 0.7, 6.55, 7.0, 0.3, 14, name="Footer:brand")
    text(f"Draft  |  {number}", 10.3, 6.55, 2.3, 0.3, 14, name="Footer:number")
    return slide
