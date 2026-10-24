from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.assets.models import DeckAsset

TITLE_MAX = 80
SUBTITLE_MAX = 140
BULLET_MAX = 160
DETAIL_MAX = 200
NOTES_MAX = 600

Provenance = Literal["user", "generated", "illustrative"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Bullet(_Model):
    text: str = Field(min_length=1, max_length=BULLET_MAX)
    detail: str | None = Field(default=None, max_length=DETAIL_MAX)


class Column(_Model):
    heading: str = Field(min_length=1, max_length=40)
    bullets: list[Bullet] = Field(min_length=1, max_length=5)


class Stat(_Model):
    value: str = Field(min_length=1, max_length=16)
    label: str = Field(min_length=1, max_length=60)
    context: str | None = Field(default=None, max_length=100)
    provenance: Provenance


class Step(_Model):
    title: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=120)


class ComparisonItem(_Model):
    name: str = Field(min_length=1, max_length=40)
    points: list[str] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def _point_length(self):
        for p in self.points:
            if not 1 <= len(p) <= 100:
                raise ValueError("each comparison point must be 1-100 characters")
        return self


class Series(_Model):
    name: str = Field(min_length=1, max_length=40)
    values: list[float] = Field(min_length=2, max_length=8)


class _SlideBase(_Model):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    speaker_notes: str | None = Field(default=None, max_length=NOTES_MAX)


class TitleSlide(_SlideBase):
    layout: Literal["title"]
    subtitle: str | None = Field(default=None, max_length=SUBTITLE_MAX)


class SectionSlide(_SlideBase):
    layout: Literal["section"]
    subtitle: str = Field(min_length=1, max_length=SUBTITLE_MAX)


class BulletsSlide(_SlideBase):
    layout: Literal["bullets"]
    bullets: list[Bullet] = Field(min_length=2, max_length=6)


class TwoColumnSlide(_SlideBase):
    layout: Literal["two_column"]
    left: Column
    right: Column


class StatSlide(_SlideBase):
    layout: Literal["stat"]
    stats: list[Stat] = Field(min_length=1, max_length=4)


class ProcessSlide(_SlideBase):
    layout: Literal["process"]
    subtitle: str | None = Field(default=None, max_length=SUBTITLE_MAX)
    steps: list[Step] = Field(min_length=2, max_length=8)


class ComparisonSlide(_SlideBase):
    layout: Literal["comparison"]
    items: list[ComparisonItem] = Field(min_length=2, max_length=3)


class ChartSlide(_SlideBase):
    layout: Literal["chart"]
    chart_type: Literal["bar", "line", "pie", "doughnut"]
    categories: list[str] = Field(min_length=2, max_length=8)
    series: list[Series] = Field(min_length=1, max_length=3)
    unit: str | None = Field(default=None, max_length=16)  # "%", "$M", ...
    caption: str | None = Field(default=None, max_length=SUBTITLE_MAX)
    provenance: Provenance
    placeholder: bool = False

    @model_validator(mode="after")
    def _shape_checks(self):
        n = len(self.categories)
        for s in self.series:
            if len(s.values) != n:
                raise ValueError(
                    f"series '{s.name}' has {len(s.values)} values but there are {n} categories"
                )
        if self.chart_type in ("pie", "doughnut") and len(self.series) != 1:
            raise ValueError("pie and doughnut charts need exactly one series")
        if self.placeholder and any(
            value != 0 for series in self.series for value in series.values
        ):
            raise ValueError("placeholder charts must use zero absence markers")
        if (
            self.chart_type in ("pie", "doughnut")
            and not self.placeholder
            and (
                any(value < 0 for value in self.series[0].values) or sum(self.series[0].values) <= 0
            )
        ):
            raise ValueError("pie and doughnut values must be non-negative with a positive total")
        return self


class QuoteSlide(_SlideBase):
    layout: Literal["quote"]
    quote: str = Field(min_length=1, max_length=240)
    attribution: str | None = Field(default=None, max_length=80)
    provenance: Provenance  # never present a "generated" quote as a real person's


class ClosingSlide(_SlideBase):
    layout: Literal["closing"]
    subtitle: str | None = Field(default=None, max_length=SUBTITLE_MAX)
    contact: str | None = Field(default=None, max_length=100)


class AllocationItem(_Model):
    label: str = Field(min_length=1, max_length=40)
    pct: float | None = Field(default=None, ge=0, le=100)


class AskAllocationSlide(_SlideBase):
    layout: Literal["ask_allocation"]
    ask: str | None = Field(default=None, max_length=40)
    center_label: str = Field(default="Fund Allocation", max_length=40)
    items: list[AllocationItem] = Field(min_length=2, max_length=7)

    @model_validator(mode="after")
    def _allocation_total(self):
        values = [item.pct for item in self.items]
        supplied = [value for value in values if value is not None]
        total = sum(supplied)
        if total > 100:
            raise ValueError("allocation percentages cannot exceed 100")
        if len(supplied) == len(values) and not 99 <= total <= 101:
            raise ValueError("complete allocation percentages must total approximately 100")
        if len(supplied) != len(values) and total >= 100:
            raise ValueError("missing allocation percentages need a remaining share")
        return self


class MarketSegment(_Model):
    category: str = Field(min_length=1, max_length=30)
    lead_text: str = Field(min_length=1, max_length=70)
    lead_value: str | None = Field(default=None, max_length=30)
    next_text: str = Field(min_length=1, max_length=70)
    next_value: str | None = Field(default=None, max_length=30)


class MarketSnapshotSlide(_SlideBase):
    layout: Literal["market_snapshot"]
    subtitle: str | None = Field(default=None, max_length=100)
    start_label: str = Field(default="[Base period]", max_length=20)
    start_value: str | None = Field(default=None, max_length=40)
    end_label: str = Field(default="[Forecast period]", max_length=20)
    end_value: str | None = Field(default=None, max_length=40)
    segments: list[MarketSegment] = Field(default_factory=list, max_length=4)
    table_headers: list[str] = Field(default_factory=list, max_length=5)
    table_rows: list[list[str]] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def _table_shape(self):
        if bool(self.table_headers) != bool(self.table_rows):
            raise ValueError("market table headers and rows must be supplied together")
        if any(len(row) != len(self.table_headers) for row in self.table_rows):
            raise ValueError("each market table row must match the header count")
        return self


class FlowStep(_Model):
    title: str = Field(min_length=1, max_length=50)
    detail: str | None = Field(default=None, max_length=120)


class FlowStepsSlide(_SlideBase):
    layout: Literal["flow_steps"]
    tagline: str | None = Field(default=None, max_length=100)
    subtitle: str | None = Field(default=None, max_length=100)
    steps: list[FlowStep] = Field(min_length=3, max_length=8)


class Quadrant(_Model):
    heading: str = Field(min_length=1, max_length=40)
    names: list[str] = Field(default_factory=list, max_length=9)


class QuadrantsSlide(_SlideBase):
    layout: Literal["quadrants"]
    quadrants: list[Quadrant] = Field(min_length=4, max_length=4)


Archetype = Literal[
    "cover",
    "split_image",
    "three_cards",
    "four_cards",
    "timeline_horizontal",
    "big_stat",
    "quote",
    "comparison",
    "process_steps",
    "grid_2x2",
    "closing_cta",
]


class StoryItem(_Model):
    title: str = Field(min_length=1, max_length=55)
    description: str = Field(min_length=1, max_length=190)
    quarter: Literal["Q1", "Q2", "Q3", "Q4"] | None = None


class StorySlide(_SlideBase):
    layout: Literal["editorial"] = "editorial"
    archetype: Archetype
    subtitle: str | None = Field(default=None, max_length=180)
    items: list[StoryItem] = Field(default_factory=list, max_length=6)
    image_query: str | None = Field(default=None, max_length=90)
    icon_names: list[str] = Field(default_factory=list, max_length=6)
    notes: str | None = Field(default=None, max_length=1500)
    illustrative: bool = False
    image_focus: Literal["center", "left", "right", "top", "bottom"] = "center"
    layout_variant: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def _complete_regions(self):
        expected = {"three_cards": 3, "four_cards": 4, "grid_2x2": 4}
        if self.archetype in expected and len(self.items) != expected[self.archetype]:
            raise ValueError("card archetype needs exactly its declared item count")
        if self.archetype in {"cover", "closing_cta"} and not self.subtitle:
            raise ValueError("cover and closing need a subtitle")
        if self.archetype == "timeline_horizontal" and not 3 <= len(self.items) <= 5:
            raise ValueError("timeline needs three to five fully described nodes")
        return self


Slide = Annotated[
    Union[
        TitleSlide,
        SectionSlide,
        BulletsSlide,
        TwoColumnSlide,
        StatSlide,
        ProcessSlide,
        ComparisonSlide,
        ChartSlide,
        QuoteSlide,
        ClosingSlide,
        AskAllocationSlide,
        MarketSnapshotSlide,
        FlowStepsSlide,
        QuadrantsSlide,
        StorySlide,
    ],
    Field(discriminator="layout"),
]


LAYOUTS = [
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
    "ask_allocation",
    "market_snapshot",
    "flow_steps",
    "quadrants",
]


class Deck(_Model):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    subtitle: str | None = Field(default=None, max_length=SUBTITLE_MAX)
    audience: str | None = Field(default=None, max_length=120)
    tone: str | None = Field(default=None, max_length=60)
    theme: str = "default"
    slides: list[Slide] = Field(min_length=3, max_length=30)
    assets: list[DeckAsset] = Field(default_factory=list)
    design_version: Literal["gamma-v1"] | None = None

    @model_validator(mode="after")
    def _structure(self):
        if self.slides[0].layout != "title" and not (
            self.slides[0].layout == "editorial" and self.slides[0].archetype == "cover"
        ):
            raise ValueError("the first slide must use the 'title' layout")
        return self
