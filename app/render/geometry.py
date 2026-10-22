"""Offline shape bounds and text-region checks; no rasterizer or provider needed."""

from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True)
class Region:
    label: str
    x: int
    y: int
    w: int
    h: int


def text_regions(slide):
    for shape in slide.shapes:
        if shape.has_text_frame and shape.text.strip():
            yield Region(shape.name, shape.left, shape.top, shape.width, shape.height)
        if shape.has_table:
            y = shape.top
            for row in shape.table.rows:
                x = shape.left
                for col, cell in zip(shape.table.columns, row.cells):
                    if cell.text.strip():
                        yield Region(cell.text[:30], x, y, col.width, row.height)
                    x += col.width
                y += row.height


def check_slide_geometry(slide, width, height):
    """Return all bounds and intersecting nonempty text-region errors.

    Backgrounds, charts and diagram containers may intentionally contain text.
    Only text-bearing regions are compared for overlap, with 1 EMU tolerance
    for conversion rounding. This cannot prove glyph fit or visual similarity.
    """
    errors = []
    for shape in slide.shapes:
        if (
            shape.left < 0
            or shape.top < 0
            or shape.left + shape.width > width + 1
            or shape.top + shape.height > height + 1
        ):
            errors.append(f"out of bounds: {shape.name}")
    for a, b in combinations(text_regions(slide), 2):
        if (
            min(a.x + a.w, b.x + b.w) - max(a.x, b.x) > 1
            and min(a.y + a.h, b.y + b.h) - max(a.y, b.y) > 1
        ):
            errors.append(f"text overlap: {a.label} / {b.label}")
    return errors
