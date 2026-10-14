"""Validated contracts exchanged between generation-pipeline stages."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.deck_schema import TITLE_MAX


LayoutName = Literal[
    "title",
    "section",
    "bullets",
    "two_column",
    "stat",
    "process",
    "comparison",
    "chart",
    "quote",
    "closing",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequiredFact(_Model):
    id: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=500)
    verbatim: bool = False


class AnalysisOutput(_Model):
    deck_title: str = Field(min_length=1, max_length=TITLE_MAX)
    deck_type: str = Field(min_length=1, max_length=60)
    audience: str | None = Field(default=None, max_length=120)
    tone: str | None = Field(default=None, max_length=60)
    slide_count: int = Field(ge=3, le=30)
    required_facts: list[RequiredFact] = Field(default_factory=list, max_length=80)
    constraints: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _unique_fact_ids(self):
        ids = [fact.id for fact in self.required_facts]
        if len(ids) != len(set(ids)):
            raise ValueError("required fact IDs must be unique")
        return self


class OutlineSlidePlan(_Model):
    index: int = Field(ge=0, le=29)
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    purpose: str = Field(min_length=1, max_length=240)
    layout: LayoutName
    key_points: list[str] = Field(default_factory=list, max_length=8)
    required_fact_ids: list[int] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def _unique_fact_refs(self):
        if len(self.required_fact_ids) != len(set(self.required_fact_ids)):
            raise ValueError("required fact references must be unique per slide")
        return self


class OutlineOutput(_Model):
    deck_title: str = Field(min_length=1, max_length=TITLE_MAX)
    subtitle: str | None = Field(default=None, max_length=140)
    slides: list[OutlineSlidePlan] = Field(min_length=3, max_length=30)

    @model_validator(mode="after")
    def _slide_order_and_cover(self):
        if self.slides[0].layout != "title":
            raise ValueError("the first outline slide must use the 'title' layout")
        if self.slides[0].title != self.deck_title:
            raise ValueError("the title slide title must match the outline deck title")
        if [slide.index for slide in self.slides] != list(range(len(self.slides))):
            raise ValueError("outline slide indices must be consecutive and start at 0")
        return self


def validate_outline_facts(analysis: AnalysisOutput, outline: OutlineOutput) -> None:
    """Require the outline to place every analyzed user fact at least once."""
    if outline.deck_title != analysis.deck_title:
        raise ValueError("outline deck title does not match the analyzed deck title")
    if len(outline.slides) != analysis.slide_count:
        raise ValueError(
            f"outline has {len(outline.slides)} slides; analysis requested {analysis.slide_count}"
        )
    known_ids = {fact.id for fact in analysis.required_facts}
    assigned_ids = {fact_id for slide in outline.slides for fact_id in slide.required_fact_ids}
    unknown_ids = assigned_ids - known_ids
    if unknown_ids:
        raise ValueError(f"outline references unknown fact IDs: {sorted(unknown_ids)}")
    missing_ids = known_ids - assigned_ids
    if missing_ids:
        raise ValueError(f"outline omits required fact IDs: {sorted(missing_ids)}")


class ReviewIssue(_Model):
    category: Literal[
        "missing_fact", "duplicate", "unsupported_claim", "overflow", "coherence", "other"
    ]
    severity: Literal["low", "medium", "high"]
    detail: str = Field(min_length=1, max_length=500)
    slide_indices: list[int] = Field(default_factory=list, max_length=30)
    repair_instruction: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _unique_slide_refs(self):
        if len(self.slide_indices) != len(set(self.slide_indices)):
            raise ValueError("issue slide indices must be unique")
        return self


class ReviewOutput(_Model):
    passed: bool
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=40)
    missing_fact_ids: list[int] = Field(default_factory=list, max_length=80)
    regenerate_slide_indices: list[int] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _consistent_result(self):
        if len(self.missing_fact_ids) != len(set(self.missing_fact_ids)):
            raise ValueError("missing fact IDs must be unique")
        if len(self.regenerate_slide_indices) != len(set(self.regenerate_slide_indices)):
            raise ValueError("regeneration slide indices must be unique")
        has_findings = bool(self.issues or self.missing_fact_ids or self.regenerate_slide_indices)
        if self.passed and has_findings:
            raise ValueError("a passed review cannot include findings or regeneration requests")
        if not self.passed and not has_findings:
            raise ValueError("a failed review must include a finding or regeneration request")
        return self
