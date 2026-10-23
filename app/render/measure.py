"""Pillow font-metric wrapping shared by rendering and exported-PPTX lint."""

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


@lru_cache(maxsize=128)
def font_metrics(name, size, bold=False):
    serif = name.casefold() in {"georgia", "liberation serif"}
    win = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts"
    names = (
        [win / ("georgiab.ttf" if bold else "georgia.ttf")]
        if serif
        else [win / ("calibrib.ttf" if bold else "calibri.ttf")]
    )
    names += [
        Path("/usr/share/fonts/truetype/crosextra")
        / ("Carlito-Bold.ttf" if bold else "Carlito-Regular.ttf")
    ]
    names += [
        Path("/usr/share/fonts/truetype/liberation2")
        / (f"Liberation{'Serif' if serif else 'Sans'}-{'Bold' if bold else 'Regular'}.ttf")
    ]
    names += [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for path in names:
        if path.is_file():
            return ImageFont.truetype(str(path), round(size * 96 / 72))
    raise RuntimeError("Install Calibri/Carlito or Liberation fonts for accurate text QA")


def measure_text(text, width, size, font="Calibri", bold=False):
    face = font_metrics(font, size, bold)
    lines = 0
    widest = 0
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = (line + " " + word).strip()
            if face.getlength(candidate) > width * 96 and line:
                widest = max(widest, face.getlength(line) / 96)
                lines += 1
                line = word
            else:
                line = candidate
        widest = max(widest, face.getlength(line) / 96)
        lines += 1
    return widest, lines * size * 1.22 / 72, lines


class TextNeedsSplit(ValueError):
    pass


def fitted_size(text, width, height, size, font, bold=False, max_lines=None):
    for factor in (1, 0.95, 0.9):
        point = max(14, round(size * factor, 1))
        measured_w, measured_h, lines = measure_text(text, width, point, font, bold)
        if (
            measured_w <= width + 0.01
            and measured_h <= height + 0.01
            and (max_lines is None or lines <= max_lines)
        ):
            return point
    raise TextNeedsSplit("text requires a larger region or another slide")
