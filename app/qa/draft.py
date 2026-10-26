"""Local file checks deliver drafts; optional visual review never replaces them."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from app.core.deck_schema import Deck, DraftSlide
from app.qa.gate import QualityGateError
from app.qa.lint import lint_pptx
from app.render.builder import build_deck
from app.render.measure import TextNeedsSplit


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def build_draft(deck, output_path, *, deadline=None, progress_callback=None):
    output_path = Path(output_path)
    folder = output_path.parent / (output_path.stem + ".qa")
    folder.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("draft export deadline exceeded")
        try:
            if progress_callback:
                progress_callback("Exporting editable PowerPoint draft")
            path = build_deck(deck, folder / "draft.pptx", strict=True)
            if progress_callback:
                progress_callback("Checking text fit and PowerPoint file")
            report = lint_pptx(path, whitespace=False)
            if report["errors"]:
                write_json(folder / "report.json", {"delivery": "draft", "file_checks": report})
                raise QualityGateError("draft_file_validation_failed")
            write_json(folder / "deck.json", deck.model_dump(mode="json"))
            write_json(
                folder / "report.json",
                {
                    "delivery": "draft",
                    "file_checks": report,
                    "visual_review": {"status": "not_requested"},
                    "pptx_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "slide_count": len(deck.slides),
                },
            )
            os.replace(path, output_path)
            return output_path
        except TextNeedsSplit as exc:
            index = getattr(exc, "slide_index", 0)
            slide = deck.slides[index]
            if not isinstance(slide, DraftSlide) or len(slide.points) < 2:
                raise QualityGateError("draft_text_does_not_fit") from None
            middle = len(slide.points) // 2
            pieces = []
            for offset, points in enumerate((slide.points[:middle], slide.points[middle:])):
                title = slide.title if not offset else slide.title[:65] + " (continued)"
                pieces.append(
                    DraftSlide(title=title, points=points, speaker_notes=slide.speaker_notes)
                )
            deck = Deck.model_validate(
                {
                    **deck.model_dump(),
                    "slides": [
                        s.model_dump()
                        for s in [*deck.slides[:index], *pieces, *deck.slides[index + 1 :]]
                    ],
                }
            )
    raise QualityGateError("draft_text_does_not_fit")


def review_draft(output_path, *, deadline=None, reviewer=None, render=None):
    from app.llm.vision import GroqVisionReviewer
    from app.qa.render import render_slides

    output_path = Path(output_path)
    folder = output_path.parent / (output_path.stem + ".qa")
    report_path = folder / "review.json"
    deck = Deck.model_validate_json((folder / "deck.json").read_text(encoding="utf-8"))
    report = {
        "status": "running",
        "completed_slides": 0,
        "total_slides": len(deck.slides),
        "grades": [],
        "images": [],
        "message": "Rendering draft for optional design review.",
    }
    write_json(report_path, report)
    try:
        review_folder = folder / "1"
        review_folder.mkdir(exist_ok=True)
        # Review a copy; never change the downloadable draft.
        review_copy = review_folder / "draft.pptx"
        shutil.copyfile(output_path, review_copy)
        images = (render or render_slides)(review_copy, review_folder, deadline=deadline)
        if len(images) != len(deck.slides):
            raise ValueError("rendered slide count mismatch")
        reviewer = reviewer or GroqVisionReviewer(deadline=deadline)
        report["images"] = [f"1/{image.name}" for image in images]
        for index, image in enumerate(images):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("optional design review deadline exceeded")
            report["message"] = (
                f"Reviewing slide {index + 1}/{len(images)}. Your draft remains available."
            )
            write_json(report_path, report)
            grade = reviewer(image, theme=deck.theme, slide_index=index)
            report["grades"].append(grade.model_dump() if hasattr(grade, "model_dump") else grade)
            report["completed_slides"] = index + 1
            write_json(report_path, report)
        report.update(
            status="completed",
            message="Design review finished. Review the suggestions before sharing.",
        )
    except Exception as exc:
        report.update(
            status="failed",
            failure_type=type(exc).__name__,
            message="Design review could not finish. Your PowerPoint draft is still available.",
        )
    write_json(report_path, report)
    return report
