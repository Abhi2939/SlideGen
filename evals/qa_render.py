import argparse
import json
import shutil
import subprocess
from pathlib import Path

import fitz  # PyMuPDF

from app.core.deck_schema import Deck
from app.render.builder import build_deck

OUT = Path(__file__).parent / "outputs"

WINDOWS_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


def find_soffice() -> str:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for p in WINDOWS_PATHS:
        if Path(p).exists():
            return p
    raise RuntimeError("LibreOffice not found. Install it and/or add it to PATH.")


def pptx_to_pdf(pptx_path: Path) -> Path:
    subprocess.run(
        [
            find_soffice(),
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pptx_path.parent),
            str(pptx_path),
        ],
        check=True,
        timeout=180,
    )
    return pptx_path.with_suffix(".pdf")


def pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int = 110) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, 1):
            p = out_dir / f"slide-{i:02d}.png"
            page.get_pixmap(dpi=dpi).save(str(p))
            paths.append(p)
    return paths


def render_deck_file(deck_json: Path) -> list[Path]:
    deck = Deck.model_validate(json.loads(deck_json.read_text(encoding="utf-8")))
    OUT.mkdir(parents=True, exist_ok=True)
    pptx = build_deck(deck, OUT / f"{deck_json.stem}.pptx")
    pdf = pptx_to_pdf(pptx)
    return pdf_to_pngs(pdf, OUT / deck_json.stem)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("deck_json", type=Path)
    args = ap.parse_args()
    for p in render_deck_file(args.deck_json):
        print(p)
