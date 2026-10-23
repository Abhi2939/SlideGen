"""Adversarial fake completions through the real offline pipeline."""

import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.structured import StructuredGenerator
from app.pipeline.analyze import DEFAULT_SLIDE_COUNT
from app.pipeline.briefs import beauty_brand
from app.pipeline.orchestrator import generate_deck
from app.pipeline.write_slides import _slide_text_content
from app.llm.usage import record_usage


BLUSH = "My startup makes custom foundation, lipstick, and skincare products matched to skin tone, undertone, and style. Make a short presentation."
DOGS = "Make a deck about dogs"
INVENTED = (
    "Our market share is 99%.",
    "Competitors include Globex and Initech.",
    "Plans start at $29 per month.",
    "We have paying customers.",
    "Our experienced founders previously led Google.",
    "Our valuation is $2M with an equity stake.",
)


class SparseProvider:
    def __init__(self, prompt, claim=None, invalid_bullet_count=None):
        self.prompt = prompt
        self.claim = claim
        self.invalid_bullet_count = invalid_bullet_count
        self.bullet_calls = 0
        self.analysis_facts = []

    def complete(self, messages, **kwargs):
        record_usage(SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        schema = json.loads(messages[0]["content"].split("JSON Schema:\n")[1])
        model = schema["title"]
        if model == "AnalysisOutput":
            self.analysis_facts = (
                [
                    {
                        "id": 0,
                        "text": "Custom foundation, lipstick, and skincare products matched to skin tone, undertone, and style.",
                    }
                ]
                if self.prompt == BLUSH
                else []
            )
            if self.claim:
                self.analysis_facts.append({"id": len(self.analysis_facts), "text": self.claim})
            return json.dumps(
                {
                    "deck_title": "Introduction",
                    "deck_type": "introduction",
                    "slide_count": 3,
                    "required_facts": self.analysis_facts,
                }
            )
        if model == "OutlineOutput":
            # Inspect what the real analyzer actually forwarded as source facts.
            brief = json.loads(messages[1]["content"].split("ANALYZED BRIEF:\n")[1])
            assert len(brief["required_facts"]) == (1 if self.prompt == BLUSH else 0)
            assert all(f["text"] != self.claim for f in brief["required_facts"])
            assert brief["slide_count"] == (6 if self.prompt == DOGS else DEFAULT_SLIDE_COUNT)
            return json.dumps(
                {
                    "deck_title": "Introduction",
                    "slides": [
                        {
                            "index": 0,
                            "title": "Introduction",
                            "purpose": "Introduce",
                            "layout": "title",
                        },
                        {
                            "index": 1,
                            "title": "Overview",
                            "purpose": "Explain",
                            "layout": "bullets",
                            "required_fact_ids": [0] if self.prompt == BLUSH else [],
                        },
                        {
                            "index": 2,
                            "title": "Conceptual workflow",
                            "purpose": "Suggest conceptual steps",
                            "layout": "flow_steps",
                        },
                        {
                            "index": 3,
                            "title": "Next steps",
                            "purpose": "Close",
                            "layout": "closing",
                        },
                    ],
                }
            )
        if model == "TitleSlide":
            return json.dumps(
                {"layout": "title", "title": "Introduction", "subtitle": "An introduction"}
            )
        if model == "BulletsSlide":
            self.bullet_calls += 1
            if self.claim:
                bullets = [{"text": self.claim}, {"text": "A qualitative overview"}]
            elif self.invalid_bullet_count is not None and self.bullet_calls == 1:
                bullets = [{"text": "A single incomplete point"}] * self.invalid_bullet_count
            else:
                bullets = (
                    [
                        {"text": "Foundation, lipstick and skincare"},
                        {"text": "Matched to skin tone, undertone and style"},
                    ]
                    if self.prompt == BLUSH
                    else [
                        {"text": "Dogs communicate through body language"},
                        {"text": "Care includes exercise and companionship"},
                    ]
                )
            return json.dumps({"layout": "bullets", "title": "Overview", "bullets": bullets})
        if model == "FlowStepsSlide":
            return json.dumps(
                {
                    "layout": "flow_steps",
                    "title": "Conceptual workflow",
                    "subtitle": "Conceptual steps to tailor",
                    "steps": [
                        {"title": t}
                        for t in (
                            ("Assess preferences", "Select shade options", "Confirm preferences")
                            if self.prompt == BLUSH
                            else (
                                "Observe body language",
                                "Offer companionship",
                                "Plan regular exercise",
                            )
                        )
                    ],
                }
            )
        if model == "ClosingSlide":
            return json.dumps({"layout": "closing", "title": "Next steps"})
        if model == "ReviewOutput":
            # Deliberately permissive fake review: deterministic guards must hold.
            return '{"passed": true}'
        raise AssertionError(model)


def generate(prompt, provider, **callbacks):
    settings = Settings("groq", None, "fake", None, None, None, None, 2)
    return generate_deck(
        prompt, settings=settings, generator=StructuredGenerator(LLMClient(provider)), **callbacks
    )


@pytest.mark.parametrize("prompt", [BLUSH, DOGS])
@pytest.mark.parametrize("invalid_bullet_count", [0, 1])
def test_sparse_prompt_has_complete_body_and_conceptual_workflow(prompt, invalid_bullet_count):
    provider = SparseProvider(prompt, invalid_bullet_count=invalid_bullet_count)
    deck = generate(prompt, provider)
    assert len(deck.slides) >= DEFAULT_SLIDE_COUNT
    assert len(deck.slides[1].bullets) >= 2
    assert provider.bullet_calls == 2
    assert len(deck.slides[2].steps) == 3
    assert all(_slide_text_content(s).strip() for s in deck.slides[1:-1])


@pytest.mark.parametrize("claim", INVENTED)
@pytest.mark.parametrize("prompt", [BLUSH, DOGS])
def test_invented_specifics_are_never_promoted_to_supplied_facts(prompt, claim):
    deck = generate(prompt, SparseProvider(prompt, claim))
    content = _slide_text_content(deck.slides[1])
    assert claim not in content
    assert "verified" in content or "placeholder" in content


def test_unbranded_startup_description_is_not_a_brand_name():
    assert beauty_brand(BLUSH) is None


def test_pipeline_reports_counts_and_review_without_changing_deck():
    counts = {}
    reviews = []
    deck = generate(
        DOGS, SparseProvider(DOGS), usage_callback=counts.update, review_callback=reviews.append
    )
    assert counts["analyze"]["total"] == 15
    assert counts["outline"]["total"] == 15
    assert counts["write"]["total"] == 60
    assert counts["review"]["total"] == 15
    assert counts["repair"]["total"] == 0
    assert reviews == [
        {"passed": True, "issues": [], "missing_fact_ids": [], "regenerate_slide_indices": []}
    ]
    assert "usage" not in deck.model_dump()
