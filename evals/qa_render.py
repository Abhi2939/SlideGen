import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from app.core.deck_schema import Deck
from app.render.builder import build_deck

OUT = Path("outputs/qa")

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
    with tempfile.TemporaryDirectory(prefix="slidegen-lo-") as profile:
        subprocess.run(
            [
                find_soffice(),
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pptx_path.parent.resolve()),
                str(pptx_path.resolve()),
            ],
            check=True,
            timeout=180,
        )
    return pptx_path.with_suffix(".pdf")


def pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int = 110) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc, 1):
            p = out_dir / f"slide-{i:02d}.png"
            page.get_pixmap(dpi=dpi).save(str(p))
            paths.append(p)
    return paths


def render_deck_file(deck_json: Path, out_dir: Path = OUT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pptx = out_dir / f"{deck_json.stem}.pptx"
    if deck_json.suffix.lower() == ".pptx":
        if deck_json.resolve() != pptx.resolve():
            shutil.copy2(deck_json, pptx)
    else:
        deck = Deck.model_validate(json.loads(deck_json.read_text(encoding="utf-8")))
        build_deck(deck, pptx)
    pdf = pptx_to_pdf(pptx)
    return pdf_to_pngs(pdf, out_dir / deck_json.stem)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("deck_json", type=Path)
    ap.add_argument("--output-dir", type=Path, default=OUT)
    args = ap.parse_args()
    for p in render_deck_file(args.deck_json, args.output_dir):
        print(p)
