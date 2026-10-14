from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
        if self.chart_type in ("pie", "doughnut") and (
            any(value < 0 for value in self.series[0].values) or sum(self.series[0].values) <= 0
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
]


class Deck(_Model):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    subtitle: str | None = Field(default=None, max_length=SUBTITLE_MAX)
    audience: str | None = Field(default=None, max_length=120)
    tone: str | None = Field(default=None, max_length=60)
    theme: str = "default"
    slides: list[Slide] = Field(min_length=3, max_length=30)

    @model_validator(mode="after")
    def _structure(self):
        if self.slides[0].layout != "title":
            raise ValueError("the first slide must use the 'title' layout")
        return self
