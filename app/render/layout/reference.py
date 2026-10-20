"""Editable renderers for the four layouts based on the supplied references."""

import math

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.render.common import add_text, new_slide
from app.render.theme import Theme


NAVY = RGBColor.from_string("1B365D")
BLUE = RGBColor.from_string("263FB8")
GREEN = RGBColor.from_string("7DBE1E")
TEXT = RGBColor.from_string("333333")
MUTED = RGBColor.from_string("6B7280")
LINE = RGBColor.from_string("D1D5DB")
PALE = RGBColor.from_string("F3F4F6")
WHITE = RGBColor.from_string("FFFFFF")
PALETTE = [
    "1B365D",
    "B8BCC2",
    "F5B800",
    "F26B1D",
    "2563EB",
    "9CA3AF",
    "7DBE1E",
]
PLACEHOLDER = "[Add value]"


def _shape(slide, x, y, w, h, fill, *, kind=MSO_SHAPE.RECTANGLE, line=None):
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    if line:
        shape.line.color.rgb = line
        shape.line.width = Pt(0.75)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _text(
    slide,
    x,
    y,
    w,
    h,
    value,
    *,
    size=14,
    color=TEXT,
    bold=False,
    align=PP_ALIGN.LEFT,
    valign=MSO_ANCHOR.TOP,
    font="Calibri",
):
    box = add_text(
        slide,
        Inches(x),
        Inches(y),
        Inches(w),
        Inches(h),
        str(value),
        size_pt=size,
        color=color,
        font=font,
        bold=bold,
        align=align,
        anchor=valign,
    )
    return box


def _line(slide, x1, y1, x2, y2, *, color=LINE, width=1, dashed=False):
    connector = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2)
    )
    connector.line.color.rgb = color
    connector.line.width = Pt(width)
    if dashed:
        connector.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    return connector


def _title(slide, text, *, subtitle=None, tagline=None, bg=WHITE):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = bg
    _text(slide, 0.55, 0.28, 12.2, 0.58, text, size=27, color=TEXT, bold=True)
    if tagline:
        _text(slide, 0.58, 0.91, 12.1, 0.37, tagline, size=17, color=BLUE, bold=True)
    if subtitle:
        y = 1.28 if tagline else 0.9
        _text(slide, 0.58, y, 12.1, 0.32, subtitle, size=12, color=MUTED)


def render_ask_allocation(prs, data, theme: Theme):
    slide = new_slide(prs, theme)
    _title(slide, data.title)
    panel = _shape(slide, 0, 1.72, 3.65, 3.85, NAVY)
    tf = panel.text_frame
    tf.clear()
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Inches(0.28)
    tf.margin_right = Inches(0.2)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = data.ask or PLACEHOLDER
    run.font.name = "Calibri"
    run.font.size = Pt(36)
    run.font.bold = True
    run.font.color.rgb = WHITE

    items = data.items
    complete = all(item.pct is not None for item in items)
    if complete:
        values = [item.pct for item in items]
    else:
        known_total = sum(item.pct or 0 for item in items)
        unknown_share = (100 - known_total) / sum(item.pct is None for item in items)
        values = [item.pct if item.pct is not None else unknown_share for item in items]
    cx, cy, size = 7.65, 3.76, 2.9
    chart_data = CategoryChartData()
    chart_data.categories = [item.label for item in items]
    chart_data.add_series("Allocation", values)
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.DOUGHNUT,
        Inches(cx - size / 2),
        Inches(cy - size / 2),
        Inches(size),
        Inches(size),
        chart_data,
    ).chart
    chart.has_legend = False
    chart.has_title = False
    plot = chart.plots[0]
    for index, point in enumerate(plot.series[0].points):
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = RGBColor.from_string(PALETTE[index % len(PALETTE)])
    hole = plot._element.find(qn("c:holeSize"))
    if hole is not None:
        hole.set("val", "66")
    _text(
        slide,
        cx - 0.77,
        cy - 0.35,
        1.54,
        0.7,
        data.center_label,
        size=12,
        color=NAVY,
        bold=True,
        align=PP_ALIGN.CENTER,
        valign=MSO_ANCHOR.MIDDLE,
    )

    total = sum(values) or 1
    accumulated = 0.0
    label_nodes = []
    for item, value in zip(items, values):
        midpoint = accumulated + value / total / 2
        accumulated += value / total
        theta = 2 * math.pi * midpoint
        edge_x = cx + (size / 2) * math.sin(theta)
        edge_y = cy - (size / 2) * math.cos(theta)
        label_x = cx + (size / 2 + 0.55) * math.sin(theta)
        label_y = cy - (size / 2 + 0.42) * math.cos(theta)
        right = math.sin(theta) >= 0
        label_nodes.append((item, edge_x, edge_y, label_x, label_y, right))

    for side in (False, True):
        nodes = sorted((node for node in label_nodes if node[5] is side), key=lambda node: node[4])
        ys = [max(1.9, min(5.55, node[4])) for node in nodes]
        gap = min(0.88, 3.55 / max(1, len(ys) - 1))
        for index in range(1, len(ys)):
            ys[index] = max(ys[index], ys[index - 1] + gap)
        if ys and ys[-1] > 5.55:
            shift = ys[-1] - 5.55
            ys = [y - shift for y in ys]
        for node, adjusted_y in zip(nodes, ys):
            item, edge_x, edge_y, label_x, _, right = node
            end_x = label_x + (0.12 if right else -0.12)
            _line(slide, edge_x, edge_y, end_x, adjusted_y, color=MUTED, dashed=True)
            box_x = min(10.05, label_x + 0.12) if right else max(3.82, label_x - 2.08)
            _text(
                slide,
                box_x,
                adjusted_y - 0.31,
                2.05,
                0.28,
                f"{item.pct:.2f}%" if item.pct is not None else "[ % ]",
                size=20,
                color=NAVY,
                bold=True,
                align=PP_ALIGN.LEFT if right else PP_ALIGN.RIGHT,
            )
            _text(
                slide,
                box_x,
                adjusted_y - 0.015,
                2.05,
                0.28,
                item.label,
                size=10,
                color=TEXT,
                align=PP_ALIGN.LEFT if right else PP_ALIGN.RIGHT,
            )
    if not complete:
        _text(
            slide,
            4.0,
            6.72,
            7.5,
            0.25,
            "Unprovided shares are evenly sized for illustration.",
            size=9,
            color=MUTED,
            align=PP_ALIGN.CENTER,
        )
    return slide


def _market_table(slide, data):
    if not data.table_headers or not data.table_rows:
        return
    rows = len(data.table_rows) + 1
    cols = len(data.table_headers)
    table = slide.shapes.add_table(
        rows, cols, Inches(0.48), Inches(4.46), Inches(12.35), Inches(0.34 * rows)
    ).table
    table.first_row = False
    table.horz_banding = False
    for column in table.columns:
        column.width = Inches(12.35 / cols)

    def style(cell, text, *, header=False, fill=WHITE, color=TEXT):
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill
        cell.margin_left = Inches(0.08)
        cell.margin_right = Inches(0.06)
        cell.margin_top = Inches(0.025)
        cell.margin_bottom = Inches(0.015)
        tf = cell.text_frame
        tf.clear()
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = text
        run.font.name = "Calibri"
        run.font.size = Pt(9 if not header else 10)
        run.font.bold = header
        run.font.color.rgb = color

    for col, header in enumerate(data.table_headers):
        style(table.cell(0, col), header, header=True, fill=GREEN, color=WHITE)
    for row_index, row in enumerate(data.table_rows, start=1):
        for col, value in enumerate(row):
            fill = WHITE if row_index % 2 else PALE
            if col == cols - 1:
                lower = value.casefold()
                if "short" in lower:
                    fill = RGBColor.from_string("FBE4E4")
                elif "medium" in lower:
                    fill = RGBColor.from_string("FDE9B8")
                elif "long" in lower:
                    fill = RGBColor.from_string("DDEFC4")
            style(table.cell(row_index, col), value, fill=fill)


def render_market_snapshot(prs, data, theme: Theme):
    slide = new_slide(prs, theme)
    _title(slide, data.title, subtitle=data.subtitle, bg=PALE)

    _shape(slide, 0.5, 1.48, 4.18, 2.55, BLUE, kind=MSO_SHAPE.ROUNDED_RECTANGLE)
    _text(slide, 0.78, 1.7, 3.55, 0.34, data.end_label, size=12, color=WHITE)
    _text(
        slide,
        0.78,
        2.03,
        3.55,
        0.74,
        data.end_value or PLACEHOLDER,
        size=25,
        color=WHITE,
        bold=True,
    )
    _shape(
        slide,
        0.76,
        2.86,
        3.25,
        1.03,
        WHITE,
        kind=MSO_SHAPE.ROUNDED_RECTANGLE,
        line=LINE,
    )
    _text(slide, 1.02, 2.97, 2.75, 0.26, data.start_label, size=11, color=MUTED)
    _text(
        slide,
        1.02,
        3.25,
        2.75,
        0.52,
        data.start_value or PLACEHOLDER,
        size=19,
        color=TEXT,
        bold=True,
    )

    _shape(slide, 4.93, 1.48, 7.88, 2.55, WHITE, kind=MSO_SHAPE.ROUNDED_RECTANGLE)
    segments = data.segments
    if segments:
        column_width = 7.45 / len(segments)
        for index, segment in enumerate(segments):
            x = 5.14 + index * column_width
            if index:
                _line(slide, x - 0.12, 1.7, x - 0.12, 3.82)
            _text(slide, x, 1.68, column_width - 0.22, 0.34, segment.category, size=10, color=MUTED)
            _text(
                slide,
                x,
                2.08,
                column_width - 0.22,
                0.65,
                " ".join(filter(None, [segment.lead_text, segment.lead_value])),
                size=12,
                color=BLUE,
                bold=True,
            )
            _text(slide, x, 2.78, column_width - 0.22, 0.28, "↓", size=17, color=GREEN, bold=True)
            _text(slide, x, 3.08, column_width - 0.22, 0.55, segment.next_text, size=10, color=TEXT)
            if segment.next_value:
                _text(
                    slide,
                    x,
                    3.55,
                    column_width - 0.22,
                    0.3,
                    segment.next_value,
                    size=12,
                    color=BLUE,
                    bold=True,
                )
    else:
        _text(slide, 5.25, 2.45, 7.2, 0.55, "Add up to four market segments", size=14, color=MUTED)

    _market_table(slide, data)
    return slide


def render_flow_steps(prs, data, theme: Theme):
    slide = new_slide(prs, theme)
    _title(slide, data.title, tagline=data.tagline, subtitle=data.subtitle, bg=PALE)
    steps = data.steps
    left, right = 0.85, 12.48
    gap = (right - left) / (len(steps) - 1)
    centers = []
    for index in range(len(steps)):
        t = index / (len(steps) - 1)
        centers.append((left + index * gap, 2.8 + 0.48 * (1 - math.sin(math.pi * t))))
    for (x1, y1), (x2, y2) in zip(centers, centers[1:]):
        _line(slide, x1, y1, x2, y2, color=GREEN, width=2)
    card_w = min(1.52, gap * 0.9)
    for index, (step, (cx, cy)) in enumerate(zip(steps, centers), start=1):
        _shape(slide, cx - 0.3, cy - 0.3, 0.6, 0.6, GREEN, kind=MSO_SHAPE.OVAL)
        _text(
            slide,
            cx - 0.3,
            cy - 0.27,
            0.6,
            0.52,
            index,
            size=15,
            color=WHITE,
            bold=True,
            align=PP_ALIGN.CENTER,
            valign=MSO_ANCHOR.MIDDLE,
        )
        card_y = 4.1 + (0.7 if index % 2 == 0 else 0)
        _line(slide, cx, cy + 0.32, cx, card_y, color=LINE, dashed=True)
        card = _shape(
            slide,
            cx - card_w / 2,
            card_y,
            card_w,
            1.34,
            WHITE,
            kind=MSO_SHAPE.ROUNDED_RECTANGLE,
            line=LINE,
        )
        tf = card.text_frame
        tf.clear()
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.margin_left = tf.margin_right = Inches(0.12)
        tf.margin_top = tf.margin_bottom = Inches(0.08)
        title_p = tf.paragraphs[0]
        title_p.alignment = PP_ALIGN.CENTER
        title_run = title_p.add_run()
        title_run.text = step.title
        title_run.font.name = "Calibri"
        title_run.font.size = Pt(10)
        title_run.font.bold = True
        title_run.font.color.rgb = TEXT
        if step.detail:
            detail_p = tf.add_paragraph()
            detail_p.alignment = PP_ALIGN.CENTER
            detail_run = detail_p.add_run()
            detail_run.text = step.detail
            detail_run.font.name = "Calibri"
            detail_run.font.size = Pt(8)
            detail_run.font.color.rgb = MUTED
    return slide


def render_quadrants(prs, data, theme: Theme):
    slide = new_slide(prs, theme)
    _title(slide, data.title)
    _line(slide, 6.67, 1.4, 6.67, 7.12, color=LINE, dashed=True)
    _line(slide, 0.48, 4.22, 12.85, 4.22, color=LINE, dashed=True)
    for index, quadrant in enumerate(data.quadrants):
        col, row = index % 2, index // 2
        x = 0.56 + col * 6.3
        heading_y = 1.5 if row == 0 else 6.62
        _shape(
            slide,
            x + 0.48,
            heading_y,
            5.05,
            0.48,
            GREEN,
            kind=MSO_SHAPE.ROUNDED_RECTANGLE,
        )
        _text(
            slide,
            x + 0.58,
            heading_y + 0.03,
            4.85,
            0.4,
            quadrant.heading,
            size=11,
            color=WHITE,
            bold=True,
            align=PP_ALIGN.CENTER,
            valign=MSO_ANCHOR.MIDDLE,
        )
        chip_top = 2.18 if row == 0 else 4.58
        for item_index, name in enumerate(quadrant.names):
            chip_x = x + 0.34 + (item_index % 3) * 1.88
            chip_y = chip_top + (item_index // 3) * 0.69
            chip = _shape(
                slide,
                chip_x,
                chip_y,
                1.72,
                0.48,
                WHITE,
                kind=MSO_SHAPE.ROUNDED_RECTANGLE,
                line=LINE,
            )
            tf = chip.text_frame
            tf.clear()
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf.margin_left = tf.margin_right = Inches(0.06)
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            run = p.add_run()
            run.text = name
            run.font.name = "Calibri"
            run.font.size = Pt(9)
            run.font.bold = True
            run.font.color.rgb = TEXT
    return slide


RENDERERS = {
    "ask_allocation": render_ask_allocation,
    "market_snapshot": render_market_snapshot,
    "flow_steps": render_flow_steps,
    "quadrants": render_quadrants,
}
