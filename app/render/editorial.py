"""Grid-owned editable archetypes. No provider, network or image generation."""

from lxml import etree
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from app.render.assets import add_asset, find_asset
from app.render.common import add_rect, add_text
from app.render.grid import GRID as G
from app.render.measure import fitted_size, measure_text


def configure_master(prs, theme):
    master = prs.slide_master
    master.background.fill.solid()
    master.background.fill.fore_color.rgb = theme.bg
    part = master.part.part_related_by(RT.THEME)
    xml = etree.fromstring(part.blob)
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    for key, font in (("majorFont", theme.font_heading), ("minorFont", theme.font_body)):
        for latin in xml.findall(f".//a:{key}/a:latin", ns):
            latin.set("typeface", font)
    for key, value in (
        ("dk1", theme.text),
        ("lt1", theme.bg),
        ("accent1", theme.primary),
        ("accent2", theme.accent),
    ):
        element = xml.find(f".//a:clrScheme/a:{key}", ns)
        for child in list(element):
            element.remove(child)
        child = OxmlElement("a:srgbClr")
        child.set("val", str(value))
        element.append(child)
    part._blob = etree.tostring(xml)


def _text(slide, theme, text, x, y, w, h, size=18, *, title=False, color=None, name="Text"):
    font = theme.font_heading if title else theme.font_body
    size = fitted_size(text, w, h, size, font, title, 2 if title else None)
    placeholder = (
        slide.shapes.title
        if name == "Slide title"
        else next(
            (p for p in slide.placeholders if p != slide.shapes.title and not p.text.strip()), None
        )
    )
    if placeholder is None:
        shape = add_text(
            slide,
            Inches(x),
            Inches(G.y(y)),
            Inches(w),
            Inches(h),
            text,
            size_pt=size,
            color=color or theme.text,
            font=font,
            bold=title,
        )
    else:
        shape = placeholder
        shape.left, shape.top, shape.width, shape.height = map(Inches, (x, G.y(y), w, h))
        tf = shape.text_frame
        tf.clear()
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        p._p.get_or_add_pPr().append(OxmlElement("a:buNone"))
        p.space_before = p.space_after = Pt(0)
        p.line_spacing = 1.1
        r = p.add_run()
        r.text = text
        r.font.name, r.font.size, r.font.bold = font, Pt(size), title
        r.font.color.rgb = color or theme.text
    shape.name = name
    return shape


def _card(slide, theme, item, x, y, w, h, key, icon=None):
    if w < 2.4:
        raise ValueError("cards must be at least 2.4 inches wide")
    card = add_rect(
        slide,
        Inches(x),
        Inches(G.y(y)),
        Inches(w),
        Inches(h),
        theme.surface,
        MSO_SHAPE.ROUNDED_RECTANGLE,
    )
    card.name = f"Layer:card:{key}"
    pad = G.gutter
    text_top = G.y(y + pad)
    if icon and find_asset(icon):
        add_asset(slide, icon, x + pad, text_top, 0.45, 0.45)
        text_top = G.y(text_top + 0.6)
    title_h = measure_text(item.title, w - 2 * pad, 22, theme.font_heading, True)[1] + 0.05
    _text(slide, theme, item.title, x + pad, text_top, w - 2 * pad, title_h, 22, title=True)
    body_top = G.y(text_top + title_h + 0.1)
    body_h = h - (body_top - G.y(y)) - 0.15
    _text(slide, theme, item.description, x + pad, body_top, w - 2 * pad, body_h, 18)


def render(prs, data, theme, *, brand, number, revision=0):
    cover = data.archetype in {"cover", "closing_cta"}
    slide = prs.slides.add_slide(prs.slide_layouts[0 if cover else 1])
    slide._element.cSld.set("name", data.archetype)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = theme.bg
    width = G.span(12)
    if cover:
        slide.background.fill.fore_color.rgb = theme.primary
        if add_asset(slide, "hero", G.x(6), 0, G.width - G.x(6), G.height, crop=True):
            slide.shapes[-1].name = "Layer:hero:photo"
        else:
            layer = add_rect(slide, 0, 0, Inches(G.width), Inches(G.height), theme.primary)
            layer.name = "Layer:background:gradient-fallback"
            fill = layer.fill
            fill.gradient()
            fill.gradient_stops[0].color.rgb = theme.primary
            fill.gradient_stops[1].color.rgb = theme.accent
        overlay = add_rect(slide, 0, 0, Inches(G.x(6)), Inches(G.height), theme.primary)
        overlay.name = "Layer:overlay:cover"
        # Strong opaque scrim keeps actual body contrast predictable.
        _text(
            slide,
            theme,
            data.title,
            G.x(0),
            2.1,
            G.span(6),
            1.8,
            54,
            title=True,
            color=theme.on_primary,
            name="Slide title",
        )
        _text(slide, theme, data.subtitle, G.x(0), 4.1, G.span(6), 1.1, 22, color=theme.on_primary)
        footer_color = theme.on_primary
    else:
        title_width = G.span(6) if data.archetype == "split_image" else width
        _text(
            slide,
            theme,
            data.title,
            G.x(0),
            0.6,
            title_width,
            1.0,
            36,
            title=True,
            color=theme.primary,
            name="Slide title",
        )
        if data.subtitle:
            _text(slide, theme, data.subtitle, G.x(0), 1.7, title_width, 0.6, 18, color=theme.muted)
        kind = data.archetype
        if kind == "split_image":
            if add_asset(slide, "hero", G.x(7), 0, G.width - G.x(7), G.height, crop=True):
                slide.shapes[-1].name = "Layer:hero:split"
            else:
                # Missing asset is explicit fallback; QA requires a real picture for this archetype.
                panel = add_rect(
                    slide,
                    Inches(G.x(7)),
                    0,
                    Inches(G.width - G.x(7)),
                    Inches(G.height),
                    theme.surface,
                )
                panel.name = "Layer:background:missing-hero"
            # Keep title/subtitle clear of the edge-to-edge image.
            slide.shapes.title.width = Inches(G.span(6))
            for shape in slide.shapes:
                if shape.is_placeholder and shape != slide.shapes.title:
                    shape.width = Inches(G.span(6))
            row_h = 3.9 / max(1, len(data.items))
            for i, item in enumerate(data.items):
                y = 2.4 + i * row_h
                _text(
                    slide,
                    theme,
                    item.title,
                    G.x(0),
                    y,
                    G.span(6),
                    0.55,
                    22,
                    title=False,
                    color=theme.primary,
                )
                _text(
                    slide,
                    theme,
                    item.description,
                    G.x(0),
                    y + 0.6,
                    G.span(6),
                    max(0.7, row_h - 0.65),
                    18,
                )
        elif kind in {"three_cards", "four_cards", "grid_2x2", "comparison", "process_steps"}:
            n = len(data.items)
            columns = 2 if kind == "grid_2x2" or n == 2 else min(n, 4)
            span = 12 // columns
            rows = (n + columns - 1) // columns
            # Cards derive their height from title+description metrics and resolved icons.
            heights = []
            for i, item in enumerate(data.items):
                text_w = G.span(span) - 0.5
                content_h = (
                    measure_text(item.title, text_w, 22, theme.font_heading, True)[1]
                    + measure_text(item.description, text_w, 18, theme.font_body)[1]
                )
                heights.append(content_h + 0.75 + (0.6 if find_asset(f"item:{i}") else 0))
            h = max(heights)
            group_h = rows * h + (rows - 1) * 0.25
            y = G.y(2.3 + max(0, (4.0 - group_h) / 2))
            for i, item in enumerate(data.items):
                _card(
                    slide,
                    theme,
                    item,
                    G.x((i % columns) * span),
                    y + (i // columns) * (h + 0.25),
                    G.span(span),
                    h,
                    str(i),
                    f"item:{i}",
                )
        elif kind == "timeline_horizontal":
            n = len(data.items)
            start = len(slide.shapes)
            # One connector, no segmented arc or intersecting number/icon badges.
            line = slide.shapes.add_connector(
                MSO_CONNECTOR.STRAIGHT,
                Inches(G.x(0) + 0.12),
                Inches(4.2),
                Inches(G.x(0) + width - 0.12),
                Inches(4.2),
            )
            line.name = "Timeline connector"
            line.line.color.rgb = theme.accent
            line.line.width = Pt(2)
            line.shadow.inherit = False
            step = (width - 0.3) / n
            for i, item in enumerate(data.items):
                x = G.x(0) + i * step
                node = add_rect(
                    slide,
                    Inches(x + 0.05),
                    Inches(4.1),
                    Inches(0.2),
                    Inches(0.2),
                    theme.accent,
                    MSO_SHAPE.OVAL,
                )
                node.name = "Timeline node"
                y = 2.3 if i % 2 == 0 else 4.5
                label = f"{item.quarter}  {item.title}" if item.quarter else item.title
                _text(slide, theme, label, x, y, step - 0.15, 0.8, 22, color=theme.primary)
                _text(slide, theme, item.description, x, y + 0.9, step - 0.15, 0.9, 16)
            diagram = slide.shapes.add_group_shape(list(slide.shapes)[start:])
            diagram.name = "Diagram:timeline"
        elif kind in {"big_stat", "quote"}:
            item = data.items[0]
            # A large supporting image gives the statement an editorial composition.
            if add_asset(slide, "hero", G.x(7), 2.3, G.span(5), 3.9, crop=True):
                slide.shapes[-1].name = "Layer:hero:statement"
            _text(
                slide,
                theme,
                item.title,
                G.x(0),
                2.5,
                G.span(6),
                2.1,
                42,
                title=False,
                color=theme.primary,
            )
            _text(slide, theme, item.description, G.x(0), 5.0, G.span(6), 1.2, 22)
        else:
            raise ValueError("unsupported editorial archetype")
        footer_color = theme.muted
    # No corner symbols. Credits and licences stay in speaker notes.
    band = add_rect(
        slide, 0, Inches(6.5), Inches(G.width), Inches(1.0), theme.primary if cover else theme.bg
    )
    band.name = "Layer:background:footer"
    _text(
        slide,
        theme,
        brand[:45],
        G.x(0),
        6.6,
        G.span(10),
        0.3,
        14,
        color=footer_color,
        name="Footer:brand",
    )
    _text(
        slide,
        theme,
        str(number),
        G.x(11),
        6.6,
        G.span(1),
        0.3,
        14,
        color=footer_color,
        name="Footer:number",
    )
    for shape in list(slide.placeholders):
        if not shape.text.strip():
            slide.shapes._spTree.remove(shape._element)
        else:
            slide.shapes._spTree.remove(shape._element)
            slide.shapes._spTree.append(shape._element)
    return slide
