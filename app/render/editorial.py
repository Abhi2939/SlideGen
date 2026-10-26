"""Grid-owned editable archetypes. No provider, network or image generation."""

from lxml import etree
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.xmlchemy import OxmlElement
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.render.assets import add_asset, find_asset
from app.render.common import add_rect, add_text
from app.render.grid import GRID as G
from app.render.measure import fitted_size, measure_text


def configure_master(prs, theme):
    master = prs.slide_master
    master.background.fill.solid()
    master.background.fill.fore_color.rgb = theme.bg
    for owner in (master, prs.slide_layouts[0], prs.slide_layouts[1]):
        for placeholder in owner.placeholders:
            kind = placeholder.placeholder_format.type
            if kind in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}:
                box = (G.margin, G.margin, G.span(12), 1.3)
            elif kind in {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.SUBTITLE, PP_PLACEHOLDER.OBJECT}:
                box = (G.margin, 2.4, G.span(12), 3.9)
            else:
                continue
            placeholder.left, placeholder.top, placeholder.width, placeholder.height = map(
                Inches, box
            )
    prs.slide_layouts[0].name = "SlideGen Cover"
    prs.slide_layouts[1].name = "SlideGen Title and Content"
    for style, size in (
        ("titleStyle", theme.type.title.pt),
        ("bodyStyle", theme.type.body.pt),
        ("otherStyle", theme.type.caption.pt),
    ):
        for props in master._element.findall(
            f".//p:{style}//a:defRPr",
            {
                "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            },
        ):
            props.set("sz", str(round(size * 100)))
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


def _text(slide, theme, text, x, y, w, h, size=None, *, title=False, color=None, name="Text"):
    size = theme.type.body.pt if size is None else size
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
    # Layout placeholders can carry centered paragraph/anchor overrides.
    shape.text_frame.vertical_anchor = 1
    for paragraph in shape.text_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.LEFT
        paragraph.line_spacing = 1.1
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
    text_top = G.y(y + 0.2)
    if icon and find_asset(icon):
        add_asset(slide, icon, x + pad, text_top, 0.45, 0.45)
        text_top = G.y(text_top + 0.6)
    title_h = (
        measure_text(item.title, w - 2 * pad, theme.type.subtitle.pt, theme.font_heading, True)[1]
        + 0.05
    )
    _text(
        slide,
        theme,
        item.title,
        x + pad,
        text_top,
        w - 2 * pad,
        title_h,
        theme.type.subtitle.pt,
        title=True,
    )
    body_top = G.y(text_top + title_h + 0.05)
    body_h = h - (body_top - G.y(y)) - 0.1
    _text(
        slide, theme, item.description, x + pad, body_top, w - 2 * pad, body_h, theme.type.body.pt
    )


def render(prs, data, theme, *, brand, number, revision=0):
    cover = data.archetype in {"cover", "closing_cta"}
    slide = prs.slides.add_slide(prs.slide_layouts[0 if cover else 1])
    slide._element.cSld.set("name", data.archetype)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = theme.bg
    width = G.span(12)
    if cover:
        slide.background.fill.fore_color.rgb = theme.primary
        cover_split = data.archetype == "cover" and data.layout_variant > 0
        hero_x = G.x(5) if cover_split else 0
        if add_asset(
            slide,
            "hero",
            hero_x,
            0,
            G.width - hero_x,
            G.height,
            crop=True,
            focus=getattr(data, "image_focus", "center"),
        ):
            slide.shapes[-1].name = "Layer:hero:photo"
        else:
            layer = add_rect(slide, 0, 0, Inches(G.width), Inches(G.height), theme.primary)
            layer.name = "Layer:background:gradient-fallback"
            fill = layer.fill
            fill.gradient()
            fill.gradient_stops[0].color.rgb = theme.primary
            fill.gradient_stops[1].color.rgb = theme.accent
        overlay = add_rect(slide, 0, 0, Inches(G.width), Inches(G.height), theme.primary)
        overlay.name = "Layer:overlay:cover"
        overlay.fill.gradient()
        overlay.fill.gradient_angle = 0
        stops = overlay.fill._xPr.gradFill.gsLst
        middle = OxmlElement("a:gs")
        middle.set("pos", "50000")
        middle_color = OxmlElement("a:srgbClr")
        middle_color.set("val", str(theme.primary))
        middle_alpha = OxmlElement("a:alpha")
        middle_alpha.set("val", "92000")
        middle_color.append(middle_alpha)
        middle.append(middle_color)
        for stop, opacity in zip(overlay.fill.gradient_stops, (97000, 20000)):
            stop.color.rgb = theme.primary
            color = stop._element.find(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr"
            )
            alpha = OxmlElement("a:alpha")
            alpha.set("val", str(opacity))
            color.append(alpha)
        stops.insert(1, middle)
        if cover_split:
            slide.shapes._spTree.remove(overlay._element)
            overlay = add_rect(slide, 0, 0, Inches(hero_x), Inches(G.height), theme.primary)
            overlay.name = "Layer:overlay:cover-panel"
        cover_text_w = G.span(5) if cover_split else G.span(6)
        cover_title_h = min(
            1.9,
            measure_text(data.title, cover_text_w, theme.type.display.pt, theme.font_heading, True)[
                1
            ]
            + 0.12,
        )
        cover_title_y = 2.6 if cover_title_h < 1.4 else 2.0
        _text(
            slide,
            theme,
            data.title,
            G.x(0),
            cover_title_y,
            cover_text_w,
            cover_title_h,
            theme.type.display.pt,
            title=True,
            color=theme.on_primary,
            name="Slide title",
        )
        _text(
            slide,
            theme,
            data.subtitle,
            G.x(0),
            cover_title_y + cover_title_h + 0.2,
            cover_text_w,
            1.1,
            theme.type.subtitle.pt,
            color=theme.on_primary,
        )
        footer_color = theme.on_primary
    else:
        title_width = G.span(6) if data.archetype == "split_image" else width
        title_h = max(
            0.7,
            measure_text(data.title, title_width, theme.type.title.pt, theme.font_heading, True)[1]
            + 0.08,
        )
        _text(
            slide,
            theme,
            data.title,
            G.x(0),
            0.6,
            title_width,
            title_h,
            theme.type.title.pt,
            title=True,
            color=theme.primary,
            name="Slide title",
        )
        subtitle_y = max(1.7, G.y(0.6 + title_h + 0.15))
        subtitle_h = 0
        if data.subtitle:
            subtitle_h = (
                measure_text(data.subtitle, title_width, theme.type.body.pt, theme.font_body)[1]
                + 0.08
            )
            _text(
                slide,
                theme,
                data.subtitle,
                G.x(0),
                subtitle_y,
                title_width,
                subtitle_h,
                theme.type.body.pt,
                color=theme.muted,
            )
        content_top = max(2.4, G.y(subtitle_y + subtitle_h + 0.25))
        kind = data.archetype
        if kind == "split_image":
            if add_asset(
                slide,
                "hero",
                G.x(7),
                0,
                G.width - G.x(7),
                G.height,
                crop=True,
                focus=getattr(data, "image_focus", "center"),
            ):
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
            row_h = (6.3 - content_top) / max(1, len(data.items))
            for i, item in enumerate(data.items):
                y = G.y(content_top + i * row_h)
                heading_h = (
                    measure_text(item.title, G.span(6), theme.type.subtitle.pt, theme.font_body)[1]
                    + 0.03
                )
                _text(
                    slide,
                    theme,
                    item.title,
                    G.x(0),
                    y,
                    G.span(6),
                    heading_h,
                    theme.type.subtitle.pt,
                    title=False,
                    color=theme.primary,
                )
                _text(
                    slide,
                    theme,
                    item.description,
                    G.x(0),
                    y + heading_h + 0.1,
                    G.span(6),
                    row_h - heading_h - 0.18,
                    theme.type.body.pt,
                )
        elif kind in {"three_cards", "four_cards", "grid_2x2", "comparison", "process_steps"}:
            n = len(data.items)
            columns = 2 if kind == "grid_2x2" or n == 2 else min(n, 4)
            span = 12 // columns
            rows = (n + columns - 1) // columns
            complete_icons = all(find_asset(f"item:{i}") for i in range(n))
            # Cards derive their height from title+description metrics and resolved icons.
            heights = []
            for i, item in enumerate(data.items):
                text_w = G.span(span) - 0.5
                content_h = (
                    measure_text(
                        item.title, text_w, theme.type.subtitle.pt, theme.font_heading, True
                    )[1]
                    + measure_text(item.description, text_w, theme.type.body.pt, theme.font_body)[1]
                )
                heights.append(content_h + 0.5 + (0.6 if complete_icons else 0))
            h = max(heights)
            group_h = rows * h + (rows - 1) * 0.25
            strip = rows == 1 and group_h < 3.0 and find_asset("hero")
            available_bottom = 4.8 if strip else 6.3
            y = G.y(content_top + max(0, (available_bottom - content_top - group_h) / 2))
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
                    f"item:{i}" if complete_icons else None,
                )
            if strip:
                strip_y = G.y(y + group_h + 0.2)
                if add_asset(slide, "hero", G.x(0), strip_y, width, 6.3 - strip_y, crop=True):
                    slide.shapes[-1].name = "Layer:hero:card-support"
        elif kind == "timeline_horizontal":
            n = len(data.items)
            start = len(slide.shapes)
            step = width / n
            # One connector, no segmented arc or intersecting number/icon badges.
            line = slide.shapes.add_connector(
                MSO_CONNECTOR.STRAIGHT,
                Inches(G.x(0) + step / 2),
                Inches(4.2),
                Inches(G.x(0) + width - step / 2),
                Inches(4.2),
            )
            line.name = "Timeline connector"
            line.line.color.rgb = theme.accent
            line.line.width = Pt(2)
            line.shadow.inherit = False
            for effect in line._element.iter(qn("a:effectRef")):
                effect.set("idx", "0")
            for i, item in enumerate(data.items):
                x = G.x(0) + i * step
                node = add_rect(
                    slide,
                    Inches(x + step / 2 - 0.1),
                    Inches(4.1),
                    Inches(0.2),
                    Inches(0.2),
                    theme.accent,
                    MSO_SHAPE.OVAL,
                )
                node.name = "Timeline node"
                y = content_top if i % 2 == 0 else 4.5
                label = f"{item.quarter}  {item.title}" if item.quarter else item.title
                _text(
                    slide,
                    theme,
                    label,
                    x,
                    y,
                    step - 0.15,
                    0.75,
                    theme.type.subtitle.pt,
                    color=theme.primary,
                )
                body_y = G.y(y + 0.8)
                body_h = (4.0 if i % 2 == 0 else 6.3) - body_y
                _text(
                    slide,
                    theme,
                    item.description,
                    x,
                    body_y,
                    step - 0.15,
                    body_h,
                    theme.type.small.pt,
                )
            diagram = slide.shapes.add_group_shape(list(slide.shapes)[start:])
            diagram.name = "Diagram:timeline"
        elif kind in {"big_stat", "quote"}:
            item = data.items[0]
            statement_y = max(2.5, content_top)
            statement_size = (
                theme.type.stat.pt
                if kind == "big_stat" and len(item.title) <= 14
                else theme.type.statement.pt
            )
            statement_h = min(
                2.1,
                measure_text(item.title, G.span(6), statement_size, theme.font_body)[1] + 0.1,
            )
            description_y = G.y(statement_y + statement_h + 0.25)
            # A large supporting image gives the statement an editorial composition.
            if add_asset(
                slide,
                "hero",
                G.x(7),
                2.3,
                G.span(5),
                3.9,
                crop=True,
                focus=getattr(data, "image_focus", "center"),
            ):
                slide.shapes[-1].name = "Layer:hero:statement"
            _text(
                slide,
                theme,
                item.title,
                G.x(0),
                statement_y,
                G.span(6),
                statement_h,
                statement_size,
                title=False,
                color=theme.primary,
            )
            _text(
                slide,
                theme,
                item.description,
                G.x(0),
                description_y,
                G.span(6),
                6.3 - description_y,
                theme.type.subtitle.pt,
            )
        else:
            raise ValueError("unsupported editorial archetype")
        footer_color = theme.muted
    # No corner symbols. Credits and licences stay in speaker notes.
    band = add_rect(
        slide, 0, Inches(6.5), Inches(G.width), Inches(1.0), theme.primary if cover else theme.bg
    )
    band.name = "Layer:background:footer"
    if cover:
        alpha = OxmlElement("a:alpha")
        alpha.set("val", "80000")
        band.fill._xPr.solidFill.srgbClr.append(alpha)
    _text(
        slide,
        theme,
        brand[:45],
        G.x(0),
        6.6,
        G.span(3),
        0.3,
        theme.type.caption.pt,
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
        theme.type.caption.pt,
        color=footer_color,
        name="Footer:number",
    )
    hero = find_asset("hero")
    if hero and "attribution required" in hero.licence:
        credit = _text(
            slide,
            theme,
            f"{(hero.photographer or 'Photographer')[:28]} / {'Unsplash' if 'unsplash.com' in hero.source_url else 'Pexels'}",
            G.x(4),
            6.6,
            G.span(7),
            0.3,
            theme.type.caption.pt,
            color=footer_color,
            name="Footer:credit",
        )
        credit.click_action.hyperlink.address = hero.source_url
    for shape in list(slide.placeholders):
        if not shape.text.strip():
            slide.shapes._spTree.remove(shape._element)
        else:
            slide.shapes._spTree.remove(shape._element)
            slide.shapes._spTree.append(shape._element)
    return slide
