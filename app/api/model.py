"""Request and response models for the HTTP API."""

from pydantic import BaseModel, ConfigDict, Field


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str = Field(min_length=5, max_length=4000)


class CreateJobResponse(BaseModel):
    id: str
    status: str


class JobStatusResponse(BaseModel):
    id: str
    status: str
    error: str | None
