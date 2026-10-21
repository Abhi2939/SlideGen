from threading import Lock
from time import sleep

import pytest

from app.core.deck_schema import (
    AskAllocationSlide,
    BulletsSlide,
    ChartSlide,
    ClosingSlide,
    FlowStepsSlide,
    MarketSnapshotSlide,
    QuadrantsSlide,
    StatSlide,
    TitleSlide,
)
from app.pipeline.grounding import unsupported_claim_categories
from app.pipeline.models import AnalysisOutput, OutlineOutput, RequiredFact
from app.pipeline.write_slides import (
    rewrite_slides,
    _numeric_data_issue,
    _placeholder_slide,
    _remove_funding_amount_from_title,
    _remove_deck_brand_from_quadrants,
    _restore_verbatim_facts,
    _slide_text_content,
    _source_contains_name,
    write_slides,
)


class FakeWriter:
    def __init__(
        self,
        *,
        invent_contact_once=False,
        invent_offer_once=False,
        repeat_unassigned_fact_once=False,
        duplicate_bullet_once=False,
        echo_instruction_once=False,
        echo_deck_structure_once=False,
    ):
        self.calls = []
        self.lock = Lock()
        self.active = 0
        self.max_active = 0
        self.bullet_calls = 0
        self.closing_calls = 0
        self.invent_contact_once = invent_contact_once
        self.invent_offer_once = invent_offer_once
        self.repeat_unassigned_fact_once = repeat_unassigned_fact_once
        self.duplicate_bullet_once = duplicate_bullet_once
        self.echo_instruction_once = echo_instruction_once
        self.echo_deck_structure_once = echo_deck_structure_once

    def generate(self, output_model, **kwargs):
        with self.lock:
            self.calls.append((output_model, kwargs))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if output_model is BulletsSlide:
                self.bullet_calls += 1
                bullet_call = self.bullet_calls
                invented_offer = self.invent_offer_once and bullet_call == 1
                repeated_fact = self.repeat_unassigned_fact_once and bullet_call == 1
                duplicate_bullet = self.duplicate_bullet_once and bullet_call == 1
                echo_instruction = self.echo_instruction_once and bullet_call == 1
                echo_deck_structure = self.echo_deck_structure_once and bullet_call == 1
            else:
                bullet_call = 0
            if output_model is ClosingSlide:
                self.closing_calls += 1
                contact = (
                    "info@example.com"
                    if self.invent_contact_once and self.closing_calls == 1
                    else None
                )
        sleep(0.03)
        try:
            if output_model is TitleSlide:
                return TitleSlide(layout="title", title="Launch plan", subtitle="")
            if output_model is BulletsSlide:
                text = "Launch is June 1" if bullet_call > 1 else "The launch is soon"
                second_bullet = "Prepare the team"
                if echo_instruction:
                    text = "Create a 6-slide deck for an electronics company"
                    second_bullet = "Include charts and figures"
                elif echo_deck_structure:
                    text = "This deck has 3 slides and 1 chart."
                    second_bullet = "Prepare the team"
                elif invented_offer:
                    second_bullet = "Visit our website for a free sample kit"
                elif repeated_fact:
                    second_bullet = "Makeup made only for YOU."
                elif duplicate_bullet:
                    second_bullet = text
                return BulletsSlide(
                    layout="bullets",
                    title="Launch date",
                    bullets=[{"text": text}, {"text": second_bullet}],
                )
            if output_model is ClosingSlide:
                return ClosingSlide(layout="closing", title="Next steps", contact=contact)
            raise AssertionError(f"unexpected output model: {output_model}")
        finally:
            with self.lock:
                self.active -= 1


def _inputs():
    analysis = AnalysisOutput(
        deck_title="Launch plan",
        deck_type="product launch",
        slide_count=3,
        required_facts=[{"id": 0, "text": "Launch is June 1", "verbatim": True}],
    )
    outline = OutlineOutput(
        deck_title="Launch plan",
        slides=[
            {"index": 0, "title": "Launch plan", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "Launch date",
                "purpose": "Explain timing",
                "layout": "bullets",
                "required_fact_ids": [0],
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )
    return analysis, outline


def test_write_slides_preserves_order_and_retries_missing_verbatim_fact():
    analysis, outline = _inputs()
    generator = FakeWriter()

    result = write_slides(
        "Make a launch deck", analysis, outline, generator, "test-model", max_workers=2
    )

    assert [slide.title for slide in result] == ["Launch plan", "Launch date", "Next steps"]
    assert [slide.layout for slide in result] == ["title", "bullets", "closing"]
    assert generator.bullet_calls == 1
    assert any(bullet.text == "Launch is June 1" for bullet in result[1].bullets)
    assert all(call[1]["stage"] == "write" for call in generator.calls)
    assert all(
        "Do not add claims about performance" in call[1]["system_prompt"]
        for call in generator.calls
    )
    assert all(
        "repeat metadata labels such as 'Startup name'" in call[1]["system_prompt"]
        for call in generator.calls
    )
    assert all("Make each bullet" in call[1]["system_prompt"] for call in generator.calls)
    assert all(
        "organizational guidance, not a source of facts" in call[1]["system_prompt"]
        for call in generator.calls
    )
    assert all(
        "does not establish in-house blending" in call[1]["system_prompt"]
        for call in generator.calls
    )
    assert all(call[1]["max_tokens"] == 1600 for call in generator.calls)
    assert generator.max_active <= 2


def test_write_slides_defaults_to_single_worker():
    analysis, outline = _inputs()
    generator = FakeWriter()

    write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert generator.max_active == 1


def test_title_slide_includes_assigned_verbatim_fact_in_subtitle():
    analysis, outline = _inputs()
    plans = list(outline.slides)
    plans[0] = plans[0].model_copy(update={"required_fact_ids": [0]})
    outline = outline.model_copy(update={"slides": plans})
    generator = FakeWriter()

    result = write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert result[0].subtitle == "Launch is June 1"


def test_write_slides_retries_when_verbatim_fact_is_repeated_on_wrong_slide():
    analysis, outline = _inputs()
    analysis = analysis.model_copy(
        update={
            "required_facts": [
                *analysis.required_facts,
                RequiredFact(id=1, text="Makeup made only for YOU.", verbatim=True),
            ]
        }
    )
    plans = list(outline.slides)
    plans[0] = plans[0].model_copy(update={"required_fact_ids": [1]})
    outline = outline.model_copy(update={"slides": plans})
    generator = FakeWriter(repeat_unassigned_fact_once=True)

    result = write_slides(
        "Make a launch deck. Makeup made only for YOU.",
        analysis,
        outline,
        generator,
        "test-model",
    )

    assert generator.bullet_calls == 2
    assert all("Makeup made only for YOU." not in bullet.text for bullet in result[1].bullets)
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert "repeated verbatim facts assigned to other slides" in bullet_calls[1]["user_prompt"]


def test_write_slides_retries_duplicate_bullet_points():
    analysis, outline = _inputs()
    generator = FakeWriter(duplicate_bullet_once=True)

    result = write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert generator.bullet_calls == 2
    assert len({bullet.text for bullet in result[1].bullets}) == 2
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert "contains duplicate bullet points" in bullet_calls[1]["user_prompt"]


def test_write_slides_retries_when_copy_echoes_presentation_instructions():
    analysis, outline = _inputs()
    generator = FakeWriter(echo_instruction_once=True)
    prompt = (
        "Generate a 6-slide PowerPoint for my electronics company and include charts and figures."
    )

    result = write_slides(prompt, analysis, outline, generator, "test-model")

    assert generator.bullet_calls == 2
    assert result[1].bullets[1].text == "Prepare the team"
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert "repeated the user's presentation instructions" in bullet_calls[1]["user_prompt"]


def test_write_slides_retries_when_copy_describes_deck_structure_counts():
    analysis, outline = _inputs()
    generator = FakeWriter(echo_deck_structure_once=True)

    result = write_slides(
        "Generate a 3-slide deck and include a chart.",
        analysis,
        outline,
        generator,
        "test-model",
    )

    assert generator.bullet_calls == 2
    assert result[1].bullets[0].text == "Launch is June 1"
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert "described the deck's own slide or visual counts" in bullet_calls[1]["user_prompt"]


def test_sparse_company_prompt_uses_placeholders_without_model_written_claims():
    analysis = AnalysisOutput(
        deck_title="Launch plan",
        deck_type="company presentation",
        slide_count=3,
        required_facts=[{"id": 0, "text": "Electronics company", "verbatim": True}],
    )
    outline = OutlineOutput(
        deck_title="Launch plan",
        slides=[
            {
                "index": 0,
                "title": "Launch plan",
                "purpose": "Introduce",
                "layout": "title",
                "required_fact_ids": [0],
            },
            {
                "index": 1,
                "title": "Launch date",
                "purpose": "Present details",
                "layout": "bullets",
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )
    generator = FakeWriter()

    result = write_slides(
        "Create a presentation for my electronics company.",
        analysis,
        outline,
        generator,
        "test-model",
    )

    assert generator.calls == []
    assert [bullet.text for bullet in result[1].bullets] == [
        "Add a verified company detail from the source material.",
        "Replace this placeholder with information you provide.",
    ]


def test_chart_placeholder_has_unique_title_and_positive_illustrative_values():
    _, outline = _inputs()
    chart_plan = outline.slides[1].model_copy(
        update={"index": 3, "title": "Supplied Details", "layout": "chart"}
    )

    result = _placeholder_slide(chart_plan)

    assert isinstance(result, ChartSlide)
    assert result.title == "Illustrative chart 4"
    assert result.provenance == "illustrative"
    assert result.caption and "replace with supplied company data" in result.caption
    assert all(value > 0 for series in result.series for value in series.values)


@pytest.mark.parametrize(
    "layout,expected_type",
    [
        ("ask_allocation", AskAllocationSlide),
        ("market_snapshot", MarketSnapshotSlide),
        ("flow_steps", FlowStepsSlide),
        ("quadrants", QuadrantsSlide),
    ],
)
def test_reference_layout_placeholders_validate(layout, expected_type):
    _, outline = _inputs()
    plan = outline.slides[1].model_copy(update={"layout": layout})

    result = _placeholder_slide(plan)

    assert isinstance(result, expected_type)
    if isinstance(result, AskAllocationSlide):
        assert result.ask is None
        assert all(item.pct is None for item in result.items)
    if isinstance(result, FlowStepsSlide):
        assert result.tagline == "Suggested workflow"
        assert len(result.steps) >= 3
    if isinstance(result, QuadrantsSlide):
        assert len(result.quadrants) == 4


def test_allocation_percentage_fact_is_in_visible_slide_text():
    slide = AskAllocationSlide(
        layout="ask_allocation",
        title="Funding Ask & Use of Funds",
        ask="Rs. 14 Cr",
        items=[
            {"label": "Contingency / Buffer", "pct": 85.3},
            {"label": "Other indirect expenses", "pct": 6.94},
            {"label": "Team", "pct": 3.02},
            {"label": "Marketing", "pct": 2.57},
            {"label": "Direct expenses", "pct": 1.95},
            {"label": "Capex", "pct": 0.22},
        ],
    )

    assert "Team 3.02%" in _slide_text_content(slide)


def test_missing_allocation_ask_is_restored_from_assigned_fact():
    slide = AskAllocationSlide(
        layout="ask_allocation",
        title="Funding Ask & Use of Funds",
        ask=None,
        items=[{"label": "Team", "pct": 50}, {"label": "Operations", "pct": 50}],
    )

    result = _restore_verbatim_facts(
        slide,
        [RequiredFact(id=0, text="Rs. 14 Cr", verbatim=True)],
    )

    assert isinstance(result, AskAllocationSlide)
    assert result.ask == "Rs. 14 Cr"


def test_title_does_not_repeat_a_funding_amount():
    slide = TitleSlide(
        layout="title",
        title="Acme Pay",
        subtitle="Raising Rs. 14 Cr for fintech growth",
    )

    result = _remove_funding_amount_from_title(slide)

    assert result.subtitle == "fintech growth"


def test_market_without_sourced_data_uses_placeholders():
    class UnsupportedMarketWriter:
        def __init__(self):
            self.models = []

        def generate(self, output_model, **kwargs):
            self.models.append(output_model)
            if output_model is TitleSlide:
                return TitleSlide(layout="title", title="Acme Pay")
            if output_model is MarketSnapshotSlide:
                return MarketSnapshotSlide(
                    layout="market_snapshot",
                    title="Market Snapshot",
                    segments=[
                        {
                            "category": "SME fintech",
                            "lead_text": "Market growth is accelerating",
                            "next_text": "Market demand is rising",
                        }
                    ],
                )
            if output_model is ClosingSlide:
                return ClosingSlide(layout="closing", title="Close")
            raise AssertionError(f"unexpected output model: {output_model}")

    analysis = AnalysisOutput(
        deck_title="Acme Pay",
        deck_type="company introduction",
        slide_count=3,
    )
    outline = OutlineOutput(
        deck_title="Acme Pay",
        slides=[
            {"index": 0, "title": "Acme Pay", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "Market Snapshot",
                "purpose": "Show market context",
                "layout": "market_snapshot",
            },
            {"index": 2, "title": "Close", "purpose": "Close", "layout": "closing"},
        ],
    )

    writer = UnsupportedMarketWriter()
    result = write_slides(
        "Acme Pay is a fintech for SMEs. Include a market snapshot.",
        analysis,
        outline,
        writer,
        "test-model",
    )

    assert isinstance(result[1], MarketSnapshotSlide)
    assert result[1].subtitle == "Add a verified market growth figure"
    assert all(segment.lead_text == "Add verified leader" for segment in result[1].segments)
    assert MarketSnapshotSlide not in writer.models


def test_quadrants_remove_the_presenting_company_from_competitors():
    slide = QuadrantsSlide(
        layout="quadrants",
        title="Competitive Landscape",
        quadrants=[
            {"heading": "Fintech", "names": ["Acme Pay", "Visa"]},
            {"heading": "Banks", "names": ["ADCB"]},
            {"heading": "Platforms", "names": []},
            {"heading": "Other", "names": []},
        ],
    )

    result = _remove_deck_brand_from_quadrants(slide, "Acme Pay Investor Deck")

    assert result.quadrants[0].names == ["Visa"]


def test_reference_numeric_layouts_reject_unprovided_values():
    analysis = AnalysisOutput(
        deck_title="Fundraising",
        deck_type="fundraising",
        slide_count=3,
        required_facts=[
            {"id": 0, "text": "Raising $14M; allocation is 60% product and 40% operations."}
        ],
    )
    valid = AskAllocationSlide(
        layout="ask_allocation",
        title="The Ask",
        ask="$14M",
        items=[{"label": "Product", "pct": 60}, {"label": "Operations", "pct": 40}],
    )
    invalid = AskAllocationSlide(
        layout="ask_allocation",
        title="The Ask",
        ask="$14M",
        items=[{"label": "Product", "pct": 61}, {"label": "Operations", "pct": 39}],
    )

    assert _numeric_data_issue(valid, analysis) is None
    assert "not present" in _numeric_data_issue(invalid, analysis)


def test_market_snapshot_rejects_unprovided_cagr_or_table_metrics():
    analysis = AnalysisOutput(
        deck_title="FinTech",
        deck_type="market analysis",
        slide_count=3,
        required_facts=[
            {"id": 0, "text": "Market size is USD 10 Bn in 2025 and USD 20 Bn in 2030."}
        ],
    )
    slide = MarketSnapshotSlide(
        layout="market_snapshot",
        title="FinTech Market",
        subtitle="CAGR: 18%",
        start_value="USD 10 Bn",
        end_value="USD 20 Bn",
    )

    assert "not present" in _numeric_data_issue(
        slide, analysis, "FinTech Market\nCAGR: 18%\n2025 USD 10 Bn\n2030 USD 20 Bn"
    )


def test_conceptual_flow_can_use_inferred_qualitative_steps():
    assert (
        unsupported_claim_categories(
            "We assess needs and select shade options.",
            "A product introduction",
            allow_structural_inference=True,
        )
        == []
    )


def test_competitor_name_source_check_uses_word_boundaries():
    assert _source_contains_name("Acme Pay", "Acme Pay raised a round.")
    assert not _source_contains_name("Visa", "supervision tools for payments")


def test_user_provenance_stat_must_match_a_supplied_numeric_fact():
    analysis, _ = _inputs()
    slide = StatSlide(
        layout="stat",
        title="Production capacity",
        stats=[
            {
                "value": "10k units/mo",
                "label": "Monthly production capacity",
                "provenance": "user",
            }
        ],
    )

    assert _numeric_data_issue(slide, analysis) == (
        "user-sourced numeric data was not present in the analyzed required facts"
    )


def test_user_provenance_stat_accepts_scaled_value_from_supplied_fact():
    analysis, _ = _inputs()
    analysis = analysis.model_copy(
        update={
            "required_facts": [
                RequiredFact(
                    id=0,
                    text="Monthly production capacity is 10,000 units.",
                    verbatim=True,
                )
            ]
        }
    )
    slide = StatSlide(
        layout="stat",
        title="Production capacity",
        stats=[
            {
                "value": "10k units/mo",
                "label": "Monthly production capacity",
                "provenance": "user",
            }
        ],
    )

    assert _numeric_data_issue(slide, analysis) is None


def test_unbacked_numeric_claim_in_text_slide_is_rejected():
    analysis, _ = _inputs()
    slide = TitleSlide(
        layout="title",
        title="Company Background",
        subtitle="Founded in 2010",
    )

    assert _numeric_data_issue(slide, analysis, "Company Background\nFounded in 2010") == (
        "generated numeric details were not present in the analyzed required facts"
    )


@pytest.mark.parametrize(
    "claim",
    [
        "Our smartphones and IoT devices.",
        "The global supply chain reach.",
        "A monthly production capacity of 10k units.",
        "Product focus and market presence.",
        "Strategic initiatives for market expansion.",
        "Our product line has an R&D focus and broad portfolio.",
        "Global reach, strong supply chain, and customer support.",
        "Contact us for a partnership.",
    ],
)
def test_unsupported_company_scope_is_flagged_when_absent_from_prompt(claim):
    assert "unsupported company scope or operations" in unsupported_claim_categories(
        claim, "Create a presentation for my electronics company."
    )


@pytest.mark.parametrize(
    "kwargs,match",
    [({"max_workers": 0}, "max_workers"), ({"semantic_attempts": 0}, "semantic_attempts")],
)
def test_write_slides_rejects_invalid_limits(kwargs, match):
    analysis, outline = _inputs()
    generator = FakeWriter()
    with pytest.raises(ValueError, match=match):
        write_slides("Make a launch deck", analysis, outline, generator, "test-model", **kwargs)
    assert generator.calls == []


def test_write_slides_rejects_empty_prompt():
    analysis, outline = _inputs()
    generator = FakeWriter()
    with pytest.raises(ValueError, match="must not be empty"):
        write_slides("  ", analysis, outline, generator, "test-model")
    assert generator.calls == []


def test_rewrite_slides_changes_only_requested_indices():
    analysis, outline = _inputs()
    current = [
        TitleSlide(layout="title", title="Launch plan"),
        BulletsSlide(
            layout="bullets",
            title="Launch date",
            bullets=[{"text": "Old wording"}, {"text": "Prepare the team"}],
        ),
        ClosingSlide(layout="closing", title="Next steps"),
    ]
    generator = FakeWriter()

    result = rewrite_slides(
        "Make a launch deck", analysis, outline, current, [1], generator, "test-model"
    )

    assert result[0] is current[0]
    assert result[2] is current[2]
    assert result[1] is not current[1]
    assert any(bullet.text == "Launch is June 1" for bullet in result[1].bullets)


def test_write_slides_rejects_invented_contact_details_and_retries():
    analysis, outline = _inputs()
    generator = FakeWriter(invent_contact_once=True)

    result = write_slides(
        "Make a launch deck", analysis, outline, generator, "test-model", max_workers=2
    )

    assert result[2].contact is None
    assert generator.closing_calls == 2
    closing_calls = [kwargs for model, kwargs in generator.calls if model is ClosingSlide]
    assert "contact information not supplied" in closing_calls[1]["user_prompt"]


def test_write_slides_rejects_unsupported_site_and_sample_offer_then_retries():
    analysis, outline = _inputs()
    generator = FakeWriter(invent_offer_once=True)

    result = write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert result[1].bullets[1].text == "Prepare the team"
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert len(bullet_calls) == 2
    assert "website or sample offers" in bullet_calls[1]["user_prompt"]


def test_unsupported_claim_categories_only_allow_source_supported_categories():
    claims = (
        "AI shade analysis uses an algorithm; we produce it in-house and ship it. "
        "Visit our website for a free sample kit after we assess your skin. "
        "Products are blended and packaged; try our personalized line today. "
        "Customers frequently choose a shade that does not fit. "
        "Standard options often fail to align, and finding a match is difficult."
    )

    categories = unsupported_claim_categories(claims, "Custom-blended makeup for your style.")

    assert categories == [
        "AI or algorithm claims",
        "assessment or diagnostic processes",
        "manufacturing or production processes",
        "shipping or delivery claims",
        "website or sample offers",
        "availability or purchase claims",
        "shade selection processes",
        "unsupported market or consumer behavior",
    ]


def test_unsupported_claim_guard_allows_categories_present_in_source():
    source = (
        "We use AI analysis and an algorithm, assess your skin, blend in-house, "
        "ship orders, and offer a free sample kit through our website."
    )

    assert unsupported_claim_categories(source, source) == []


def test_rewrite_slides_rejects_out_of_range_index_without_model_call():
    analysis, outline = _inputs()
    current = [
        TitleSlide(layout="title", title="Launch plan"),
        BulletsSlide(
            layout="bullets",
            title="Launch date",
            bullets=[{"text": "Launch is June 1"}, {"text": "Prepare the team"}],
        ),
        ClosingSlide(layout="closing", title="Next steps"),
    ]
    generator = FakeWriter()

    with pytest.raises(ValueError, match="out of range"):
        rewrite_slides(
            "Make a launch deck", analysis, outline, current, [3], generator, "test-model"
        )
    assert generator.calls == []
