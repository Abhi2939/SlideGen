import json

from pydantic import BaseModel, Field
from pydantic_core import PydanticCustomError
from pydantic import model_validator

from app.llm.client import LLMClient
from app.llm.structured import StructuredGenerator
from app.pipeline.draft import planned_draft_model
from test_draft_delivery import content
from test_editorial_pipeline import PROMPT
from test_llm_structured import FakeProvider


def generate(model, responses):
    provider = FakeProvider(responses)
    result = StructuredGenerator(LLMClient(provider)).generate(
        model,
        stage="write",
        model="openai/gpt-oss-120b",
        system_prompt="Return JSON.",
        user_prompt="Use the supplied schema.",
        compact_corrections=True,
    )
    return result, provider


def test_validation_logs_and_compact_corrections_identify_the_actual_field(caplog):
    class Item(BaseModel):
        text: str = Field(max_length=3)

    class Result(BaseModel):
        slides: list[Item]

    caplog.set_level("WARNING")
    result, provider = generate(
        Result,
        [
            '{"slides":[{"text":"PRIVATE_COMPLETION_MARKER"}]}',
            '{"slides":[{"text":"OK"}]}',
        ],
    )
    assert result.slides[0].text == "OK"
    assert '"path":"slides.0.text","type":"string_too_long"' in caplog.text
    assert "PRIVATE_COMPLETION_MARKER" not in caplog.text
    correction = provider.calls[1][0][-1]["content"]
    assert "slides.0.text" in correction
    assert "at most 3 characters" in correction
    assert "PRIVATE_COMPLETION_MARKER" not in correction


def test_generated_dictionary_keys_never_appear_in_validation_logs(caplog):
    class Result(BaseModel):
        values: dict[str, int]

    caplog.set_level("WARNING")
    generate(
        Result,
        ['{"values":{"PRIVATE_FIELD_MARKER":"PRIVATE_INPUT_MARKER"}}', '{"values":{"ok":1}}'],
    )
    assert '"path":"values.*","type":"int_parsing"' in caplog.text
    assert "PRIVATE_FIELD_MARKER" not in caplog.text
    assert "PRIVATE_INPUT_MARKER" not in caplog.text


def test_unknown_error_codes_and_validator_messages_are_redacted(caplog):
    class Result(BaseModel):
        title: str

        @model_validator(mode="after")
        def check(self):
            if self.title != "OK":
                raise PydanticCustomError("PRIVATE_ERROR_TYPE", "PRIVATE_VALIDATOR_MESSAGE")
            return self

    caplog.set_level("WARNING")
    generate(Result, ['{"title":"PRIVATE_CONTENT"}', '{"title":"OK"}'])
    assert '"type":"validation_error"' in caplog.text
    assert "PRIVATE" not in caplog.text


def test_source_validation_logs_its_safe_reason_without_the_invented_fact(caplog):
    bad = content()
    bad["facts"].append("PRIVATE invented evidence")
    caplog.set_level("WARNING")
    result, _ = generate(planned_draft_model(PROMPT), [json.dumps(bad), json.dumps(content())])
    assert len(result.sections) == 4
    assert '"type":"draft_fact_not_in_source"' in caplog.text
    assert "PRIVATE invented evidence" not in caplog.text
