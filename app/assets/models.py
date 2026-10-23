from typing import Literal

from pydantic import BaseModel, Field


class DeckAsset(BaseModel):
    slide_index: int = Field(ge=0)
    role: Literal["photo", "icon", "logo"]
    key: str
    path: str
    sha256: str
    source_url: str
    licence: str
    photographer: str | None = None
    query: str
