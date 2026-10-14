from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Inches, Pt

from app.render.common import add_text, add_title, new_slide
from app.render.theme import BODY_H, BODY_TOP, CONTENT_W, MARGIN_X

CHART_TYPES = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
}


def render(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)
    caption_h = Inches(0.42) if data.caption or data.provenance else 0
    chart_h = BODY_H - caption_h - Inches(0.1)
    chart_data = CategoryChartData()
    chart_data.categories = data.categories
    for series in data.series:
        chart_data.add_series(series.name, series.values)

    chart = slide.shapes.add_chart(
        CHART_TYPES[data.chart_type],
        MARGIN_X + Inches(0.1), BODY_TOP,
        CONTENT_W - Inches(0.2), chart_h,
        chart_data,
    ).chart
    chart.has_title = False
    chart.has_legend = data.chart_type in ("pie", "doughnut") or len(data.series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.name = theme.font_body
        chart.legend.font.size = Pt(10)

    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.show_value = True
    plot.data_labels.font.name = theme.font_body
    plot.data_labels.font.size = Pt(9)
    if data.unit:
        plot.data_labels.number_format = f'0.##"{data.unit}"'
    if data.chart_type in ("pie", "doughnut"):
        series = chart.series[0]
        for index, point in enumerate(series.points):
            color = theme.chart_colors[index % len(theme.chart_colors)]
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = color
    else:
        for index, series in enumerate(chart.series):
            color = theme.chart_colors[index % len(theme.chart_colors)]
            if data.chart_type == "line":
                series.format.line.color.rgb = color
                series.format.line.width = Pt(2)
            else:
                series.format.fill.solid()
                series.format.fill.fore_color.rgb = color
        chart.category_axis.tick_labels.font.name = theme.font_body
        chart.category_axis.tick_labels.font.size = Pt(10)
        chart.value_axis.tick_labels.font.name = theme.font_body
        chart.value_axis.tick_labels.font.size = Pt(10)
        chart.value_axis.has_major_gridlines = True

    if data.unit:
        try:
            chart.value_axis.tick_labels.number_format = f'0.##"{data.unit}"'
        except (AttributeError, ValueError):
            pass

    caption = data.caption or ""
    provenance = f"{data.provenance.upper()} DATA"
    label = "  ·  ".join(part for part in (caption, provenance) if part)
    if label:
        add_text(
            slide, MARGIN_X, BODY_TOP + chart_h + Inches(0.04), CONTENT_W, caption_h,
            label, size_pt=11, color=theme.muted, font=theme.font_body,
        )
    return slide
