from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn

from app.render import fit
from app.render.theme import CONTENT_W, MARGIN_TOP, MARGIN_X, TITLE_H, Theme

BLANK_LAYOUT = 6
TITLE_SIZES = (32, 28, 24)


def new_slide(prs, theme: Theme, bg=None):
    slide = prs.slides.add_slide(prs.slide_layouts[BLANK_LAYOUT])
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = bg or theme.bg
    return slide


def add_rect(slide, x, y, w, h, color, shape=MSO_SHAPE.RECTANGLE):
    s = slide.shapes.add_shape(shape, int(x), int(y), int(w), int(h))
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.shadow.inherit = False
    for effect in s._element.iter(qn("a:effectRef")):
        effect.set("idx", "0")
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = 0.12
    return s


def add_text(
    slide,
    x,
    y,
    w,
    h,
    text,
    *,
    size_pt,
    color,
    font,
    bold=False,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
):
    box = slide.shapes.add_textbox(int(x), int(y), int(w), int(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    f = run.font
    f.size = Pt(size_pt)
    f.bold = bold
    f.name = font
    f.color.rgb = color
    return box


def add_title(slide, theme: Theme, text: str, color=None):
    """Standard slide title: small accent bar plus a title that steps down
    in size until it fits the title area."""
    title_w = CONTENT_W - Inches(0.7)
    size = fit.choose_size(TITLE_SIZES, lambda s: fit.text_height(text, s, title_w) <= TITLE_H)
    add_text(
        slide,
        MARGIN_X,
        MARGIN_TOP,
        title_w,
        TITLE_H,
        text,
        size_pt=size,
        color=color or theme.primary,
        font=theme.font_heading,
        bold=True,
    )
