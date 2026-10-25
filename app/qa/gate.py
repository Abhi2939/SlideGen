"""At most three exported/rendered quality attempts before any file is delivered."""

import json
import hashlib
import logging
from pathlib import Path
import shutil
import time

from app.assets.resolve import prepare_assets
from app.llm.vision import GroqVisionReviewer
from app.qa.lint import lint_pptx
from app.qa.render import render_slides
from app.render.builder import build_deck
from app.render.measure import TextNeedsSplit
from app.core.deck_schema import Deck, StorySlide

log = logging.getLogger(__name__)


class QualityGateError(RuntimeError):
    def __init__(self, reason="visual_quality_failed"):
        self.reason = reason
        super().__init__(
            "The presentation did not pass visual checks. Please inspect its debug report before retrying."
        )


def repair_layout(deck, report, *, source_prompt="", deadline=None):
    failed = {e["slide"] - 1 for e in report["lint"].get("errors", []) if e["slide"] > 0}
    failed.update(
        i for i, result in enumerate(report.get("vision", [])) if result.get("score", 0) < 8
    )
    slides = list(deck.slides)
    changed = False
    for index in sorted(failed, reverse=True):
        if index >= len(slides):
            continue
        slide = slides[index]
        kind = slide.archetype
        fixes = (
            report.get("vision", [])[index].get("fixes", [])
            if index < len(report.get("vision", []))
            else []
        )
        codes = {e["code"] for e in report["lint"].get("errors", []) if e["slide"] == index + 1}
        if kind == "cover" and any(
            f.get("action") in {"switch_archetype", "rebalance_spacing"} for f in fixes
        ):
            slide = slide.model_copy(update={"layout_variant": min(2, slide.layout_variant + 1)})
            changed = True
        if "text_requires_split" in codes and len(slide.items) >= 3 and len(slides) < 10:
            first, second = slide.items[:2], slide.items[2:]
            if len(second) in {1, 2}:
                pieces = [
                    slide.model_copy(
                        update={
                            "archetype": "comparison",
                            "items": first,
                            "icon_names": slide.icon_names[:2],
                        }
                    ),
                    slide.model_copy(
                        update={
                            "archetype": "big_stat" if len(second) == 1 else "comparison",
                            "items": second,
                            "title": slide.title,
                            "icon_names": slide.icon_names[2:],
                        }
                    ),
                ]
                slides[index : index + 1] = pieces
                changed = True
                continue
        if any(f.get("action") == "recrop_image" for f in fixes):
            slide = slide.model_copy(
                update={
                    "image_focus": "top" if slide.image_focus == "center" else "center",
                    "layout_variant": min(2, slide.layout_variant + 1),
                }
            )
            changed = True
        replacement = (
            {
                "three_cards": "split_image",
                "process_steps": "split_image",
                "split_image": "three_cards",
                "grid_2x2": "four_cards",
                "four_cards": "grid_2x2",
            }.get(kind)
            if (
                codes - {"missing_hero_picture"}
                or any(f.get("action") in {"switch_archetype", "split_slide"} for f in fixes)
            )
            else None
        )
        if replacement:
            slide = slide.model_copy(update={"archetype": replacement})
            changed = True
        elif kind == "timeline_horizontal":
            aliases = {
                "user-generated content": "UGC",
                "user generated content": "UGC",
                "new product lines": "New lines",
                "subscription launch": "Subscriptions",
            }
            items = [
                i.model_copy(update={"title": aliases.get(i.title.casefold(), i.title)})
                for i in slide.items
            ]
            if items != slide.items:
                slide = slide.model_copy(update={"items": items})
                changed = True
        slides[index] = slide
    if any(e["code"] == "missing_hero_picture" for e in report["lint"].get("errors", [])):
        changed = True
    if not changed:
        return None
    corrected = Deck.model_validate(
        {
            **deck.model_dump(),
            "slides": [StorySlide.model_validate(s.model_dump()).model_dump() for s in slides],
        }
    )
    return prepare_assets(corrected, source_prompt, deadline=deadline)


def build_checked(
    deck,
    output_path,
    *,
    source_prompt="",
    deadline=None,
    reviewer=None,
    render=render_slides,
    repair=repair_layout,
    progress_callback=None,
):
    output_path = Path(output_path)
    folder = output_path.parent / (output_path.stem + ".qa")
    folder.mkdir(parents=True, exist_ok=True)
    history = {"passed": False, "attempts": [], "vision_model": getattr(reviewer, "model", None)}
    approved_pixels = {}

    def save():
        usage = getattr(getattr(reviewer, "generator", None), "usage", None)
        if usage is not None:
            history["vision_tokens_reported_calls"] = usage.snapshot()
        (folder / "report.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

    for iteration in range(1, 4):
        if deadline is not None and time.monotonic() >= deadline:
            history["failure_type"] = "deadline"
            save()
            raise TimeoutError("visual quality deadline exceeded")
        if progress_callback:
            progress_callback(f"Checking slide design ({iteration}/3)")
        attempt_dir = folder / str(iteration)
        attempt_dir.mkdir(exist_ok=True)
        record = {
            "iteration": iteration,
            "lint": {"passed": False, "errors": []},
            "vision": [],
            "images": [],
            "vision_evidence": [],
        }
        history["attempts"].append(record)
        (attempt_dir / "deck.json").write_text(deck.model_dump_json(indent=2), encoding="utf-8")
        try:
            path = build_deck(deck, attempt_dir / "deck.pptx", strict=True)
            record["lint"] = lint_pptx(path)
            kinds = [s.archetype for s in deck.slides]
            structural = []
            if not 8 <= len(kinds) <= 10:
                structural.append("deck_requires_8_to_10_slides")
            if len(set(kinds)) < 5:
                structural.append("deck_requires_5_archetypes")
            if any(len(set(kinds[i : i + 3])) == 1 for i in range(len(kinds) - 2)):
                structural.append("three_consecutive_same_archetype")
            record["lint"]["errors"].extend({"slide": 0, "code": code} for code in structural)
            record["lint"]["passed"] = not record["lint"]["errors"]
            images = render(path, attempt_dir, deadline=deadline)
            if len(images) != len(deck.slides):
                raise QualityGateError("rendered_slide_count_mismatch")
            record["images"] = [f"{iteration}/{p.name}" for p in images]
            reviewer = reviewer or GroqVisionReviewer(
                deadline=deadline, progress_callback=progress_callback
            )
            history["vision_model"] = getattr(reviewer, "model", "injected reviewer")
            for index, image in enumerate(images):
                if progress_callback:
                    progress_callback(f"Reviewing slide {index + 1}/{len(images)} ({iteration}/3)")
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("visual quality deadline exceeded")
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
                key = (index, digest, deck.theme)
                cached = approved_pixels.get(key)
                grade = cached or reviewer(image, theme=deck.theme, slide_index=index)
                grade = grade.model_dump() if hasattr(grade, "model_dump") else grade
                if grade["score"] >= 8:
                    approved_pixels[key] = grade
                record["vision"].append(grade)
                record["vision_evidence"].append(
                    {"sha256": digest, "reused_identical_approved_pixels": cached is not None}
                )
                save()
            vision_passed = all(r["score"] >= 8 for r in record["vision"])
            if record["lint"]["passed"] and vision_passed:
                history["passed"] = True
                history["final_iteration"] = iteration
                history["final_lint"] = record["lint"]
                history["final_archetypes"] = kinds
                history["final_pptx_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                save()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, output_path)
                log.info(
                    "visual gate passed: slides=%d iterations=%d lint_errors=0",
                    len(images),
                    iteration,
                )
                return output_path
        except TextNeedsSplit as exc:
            record["lint"]["errors"].append(
                {"slide": getattr(exc, "slide_index", 0) + 1, "code": "text_requires_split"}
            )
        except Exception as exc:
            record["failure_type"] = type(exc).__name__
            save()
            # Auth/quota/render availability are not stylistic retry opportunities.
            raise
        save()
        if iteration < 3:
            corrected = repair(deck, record, source_prompt=source_prompt, deadline=deadline)
            if corrected is not None:
                deck = corrected
        log.info(
            "visual gate attempt failed: iteration=%d lint_errors=%d low_scores=%d",
            iteration,
            len(record["lint"]["errors"]),
            sum(r["score"] < 8 for r in record["vision"]),
        )
    history["failure_type"] = "visual_quality_failed"
    save()
    raise QualityGateError()
