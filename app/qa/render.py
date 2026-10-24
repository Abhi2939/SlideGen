"""Bounded headless LibreOffice rendering for production and offline QA."""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def render_slides(path, output_dir, *, deadline=None):
    import pymupdf

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    executable = next(
        (
            p
            for p in (
                shutil.which("soffice"),
                shutil.which("libreoffice"),
                r"C:\Program Files\LibreOffice\program\soffice.exe",
            )
            if p and Path(p).is_file()
        ),
        None,
    )
    if executable is None:
        raise RuntimeError("LibreOffice is required for the visual quality gate")
    timeout = min(120, max(1, deadline - time.monotonic())) if deadline else 120
    with tempfile.TemporaryDirectory(prefix="slidegen-qa-") as profile:
        subprocess.run(
            [
                executable,
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir.resolve()),
                str(Path(path).resolve()),
            ],
            check=True,
            timeout=timeout,
            capture_output=True,
        )
    pdf = output_dir / (Path(path).stem + ".pdf")
    paths = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, 1):
            image = output_dir / f"slide-{i:02d}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4)).save(image)
            paths.append(image)
    return paths
