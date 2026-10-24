import json
from pathlib import Path

import pytest
from pptx import Presentation

from app.core.deck_schema import Deck, StorySlide
from app.pipeline.editorial import clean_copy, validate_copy, StoryPlan
from app.pipeline.orchestrator import generate_deck

PROMPT = "Blush is a custom beauty brand offering foundation, lipstick, and skincare blended exclusively for your skin tone, undertone, and style, so you never get the wrong shade. Our launch will focus on a shade-matching quiz, waitlist, and creator-led demos, followed by quarterly growth through UGC, subscriptions, new product lines, pop-ups, and festive scaling."
FIXTURE = Path("tests/fixtures/decks/blush_story.json")


class StoryGenerator:
    def __init__(self, deck=None):
        self.deck = deck or Deck.model_validate_json(FIXTURE.read_text())
        self.index = 0
        self.stages = []

    def generate(self, output_model, *, stage, **kwargs):
        self.stages.append(stage)
        if stage == "outline":
            return output_model.model_validate(
                {
                    "brand": self.deck.title,
                    "audience": "Customers",
                    "tone": "Personal",
                    "theme": self.deck.theme,
                    "facts": ["foundation", "lipstick", "skincare", "UGC", "festive scaling"],
                    "slides": [
                        {
                            "archetype": s.archetype,
                            "title": s.title,
                            "purpose": s.title,
                            "source_fact_ids": list(range(5)),
                        }
                        for s in self.deck.slides
                    ],
                }
            )
        if stage == "write":
            slide = self.deck.slides[self.index]
            self.index += 1
            plan = json.loads(kwargs["user_prompt"])["plan"]
            return slide.model_copy(update={"archetype": plan["archetype"], "title": plan["title"]})
        if stage == "review":
            return output_model(passed=True, issues=[], missing_fact_ids=[])
        raise AssertionError(stage)


def test_new_default_uses_two_pass_story_and_preserves_editable_contract(monkeypatch, tmp_path):
    monkeypatch.setattr("app.pipeline.editorial.prepare_assets", lambda deck, *a, **k: deck)
    fake = StoryGenerator()
    deck = generate_deck(PROMPT, generator=fake)
    assert deck.design_version == "gamma-v1" and deck.theme == "beauty"
    assert fake.stages == ["outline"] + ["write"] * 9 + ["review"]
    assert len({s.archetype for s in deck.slides}) >= 5
    assert [
        i.quarter for s in deck.slides if s.archetype == "timeline_horizontal" for i in s.items
    ] == ["Q1", "Q2", "Q3", "Q4", "Q4"]
    from app.render.builder import build_deck

    path = build_deck(deck, tmp_path / "story.pptx", strict=True)
    prs = Presentation(path)
    assert len(prs.slides) == 9
    assert all(slide.slide_layout.name != "Blank" for slide in prs.slides)
    assert all(slide.shapes.title is not None for slide in prs.slides)


def test_internal_copy_filter_moves_notes_off_canvas():
    slide = StorySlide(
        archetype="three_cards",
        title="Collection [internal note]",
        subtitle="Conceptual workflow details; confirm before use",
        items=[{"title": "Item", "description": "A useful description with enough detail."}] * 3,
    )
    result = clean_copy(slide)
    assert result.title == "Collection"
    assert result.subtitle is None
    assert "confirm before use" in result.notes


def test_illustrative_numbers_require_visible_and_notes_disclosure():
    plan = StoryPlan(
        archetype="three_cards", title="Metrics", purpose="Illustrative planning targets"
    )
    slide = StorySlide(
        archetype="three_cards",
        title="Targets",
        illustrative=True,
        items=[{"title": "70%", "description": "A proposed quiz completion planning target."}] * 3,
    )
    with pytest.raises(ValueError, match="disclosure"):
        validate_copy(slide, plan, PROMPT)


def test_unprovided_company_values_are_rejected():
    plan = StoryPlan(archetype="big_stat", title="Traction", purpose="Results")
    slide = StorySlide(
        archetype="big_stat",
        title="Traction",
        items=[{"title": "99% conversion", "description": "A fabricated commercial result."}],
    )
    with pytest.raises(ValueError, match="numeric"):
        validate_copy(slide, plan, PROMPT)


def test_growth_requires_every_source_activity_separately():
    deck = Deck.model_validate_json(FIXTURE.read_text())
    timeline = next(s for s in deck.slides if s.archetype == "timeline_horizontal")
    plan = StoryPlan(
        archetype="timeline_horizontal", title="Growth roadmap", purpose="Quarterly growth"
    )
    validate_copy(timeline, plan, PROMPT)
    timeline.items[2].title = "Creator demos"
    with pytest.raises(ValueError, match="five separate activities"):
        validate_copy(timeline, plan, PROMPT)


def test_inferred_mechanisms_are_labelled_as_proposals():
    slide = StorySlide(
        archetype="three_cards",
        title="Product priorities",
        items=[
            {
                "title": "Dashboard",
                "description": "A dashboard bringing invoice information into a shared view.",
            }
        ]
        * 3,
    )
    result = clean_copy(slide, "Acme Pay helps SMEs organize invoice payments.")
    assert result.subtitle.startswith("Proposed concept")
    assert "not supplied current product facts" in result.notes


def test_placeholder_outline_entries_never_become_source_evidence():
    from app.pipeline.editorial import source_outline, StoryOutline

    deck = Deck.model_validate_json(FIXTURE.read_text())
    outline = StoryOutline(
        brand="Blush",
        audience="Customers",
        tone="Warm",
        theme="beauty",
        facts=["foundation", "Placeholder: market size"],
        slides=[
            {
                "archetype": s.archetype,
                "title": s.title,
                "purpose": s.title,
                "source_fact_ids": [0, 1],
            }
            for s in deck.slides
        ],
    )
    cleaned = source_outline(outline, PROMPT)
    assert cleaned.facts == ["foundation"]
    assert all(p.source_fact_ids == [0] for p in cleaned.slides)


def test_same_content_changes_theme_for_other_topics(monkeypatch):
    monkeypatch.setattr("app.pipeline.editorial.prepare_assets", lambda deck, *a, **k: deck)
    for topic, expected in [
        ("edtech learning platform", "edtech"),
        ("fintech payments pitch", "fintech"),
    ]:
        fixture = Deck.model_validate_json(FIXTURE.read_text()).model_copy(
            update={"theme": expected}
        )
        fake = StoryGenerator(fixture)
        # The fake preserves source evidence deliberately; no live provider calls.
        deck = generate_deck(PROMPT + " " + topic, generator=fake)
        assert deck.design_version == "gamma-v1"
        assert json.loads(deck.model_dump_json())["slides"][0]["archetype"] == "cover"
