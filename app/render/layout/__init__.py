from . import bullets, chart, closing, comparison, process, quote, section, stat, title, two_column

LAYOUT_RENDERERS = {
    "title": title.render,
    "bullets": bullets.render,
    "section": section.render,
    "process": process.render,
    "two_column": two_column.render,
    "stat": stat.render,
    "chart": chart.render,
    "comparison": comparison.render,
    "quote": quote.render,
    "closing": closing.render,
}
