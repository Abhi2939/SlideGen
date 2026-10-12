import logging
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from app.core.deck_schema import Deck
from app.render.common import add_text, add_title, new_slide
from app.render.layout import LAYOUT_RENDERERS
from app.render.theme import BODY_TOP, CONTENT_W, MARGIN_X, SLIDE_H, SLIDE_W, get_theme

log = logging.getLogger(__name__)


def _fallback(prs, data, theme):
    slide = new_slide(prs, theme)
    add_title(slide, theme, data.title)
    add_text(
        slide, MARGIN_X, BODY_TOP, CONTENT_W, Inches(0.5),
        f"[{data.layout} layout not implemented yet]",
        size_pt=16, color=theme.muted, font=theme.font_body,
    )
    return slide


def _drop_slides_after(prs, n):
    """Remove any slides beyond the first n (cleans up a half-drawn slide)."""
    ids = prs.slides._sldIdLst
    for sld in list(ids)[n:]:
        prs.part.drop_rel(sld.rId)
        ids.remove(sld)


def build_deck(deck: Deck, output_path, strict: bool = False) -> Path:
    theme = get_theme(deck.theme)
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    for data in deck.slides:
        renderer = LAYOUT_RENDERERS.get(data.layout, _fallback)
        count = len(prs.slides)
        try:
            slide = renderer(prs, data, theme)
        except Exception:
            if strict:
                raise
            log.exception("layout %r failed, using fallback", data.layout)
            _drop_slides_after(prs, count)
            slide = _fallback(prs, data, theme)

        if data.speaker_notes:
            slide.notes_slide.notes_text_frame.text = data.speaker_notes

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return output_path