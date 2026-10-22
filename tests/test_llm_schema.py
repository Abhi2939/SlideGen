from copy import deepcopy

import pytest

from app.core.deck_schema import BulletsSlide
from app.llm.schema import recovery_format
from app.pipeline.models import ReviewOutput


@pytest.mark.parametrize("model", ["openai/gpt-oss-20b", "openai/gpt-oss-120b"])
def test_recovery_schema_closes_nested_objects_preserves_nullable_fields_and_limits(model):
    schema = BulletsSlide.model_json_schema()
    original = deepcopy(schema)

    response_format = recovery_format(model, "BulletsSlide", schema)

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    prepared = response_format["json_schema"]["schema"]
    assert prepared["additionalProperties"] is False
    assert set(prepared["required"]) == set(prepared["properties"])
    assert prepared["properties"]["layout"]["enum"] == ["bullets"]
    assert {"type": "null"} in prepared["properties"]["speaker_notes"]["anyOf"]
    assert "default" not in prepared["properties"]["speaker_notes"]
    bullet = prepared["$defs"]["Bullet"]
    assert bullet["additionalProperties"] is False
    assert set(bullet["required"]) == {"text", "detail"}
    assert {"type": "null"} in bullet["properties"]["detail"]["anyOf"]
    assert bullet["properties"]["text"]["maxLength"] == 160
    assert prepared["properties"]["bullets"]["minItems"] == 2
    assert schema == original


def test_recovery_keeps_runtime_review_reference_bounds():
    schema = ReviewOutput.model_json_schema()
    schema["properties"]["regenerate_slide_indices"]["items"]["enum"] = [0, 1, 2, 3]
    schema["$defs"]["ReviewIssue"]["properties"]["slide_indices"]["items"]["enum"] = [0, 1, 2, 3]
    schema["properties"]["missing_fact_ids"]["maxItems"] = 0

    prepared = recovery_format("openai/gpt-oss-20b", "ReviewOutput", schema)["json_schema"][
        "schema"
    ]

    assert prepared["properties"]["regenerate_slide_indices"]["items"]["enum"] == [0, 1, 2, 3]
    assert prepared["$defs"]["ReviewIssue"]["properties"]["slide_indices"]["items"]["enum"] == [
        0,
        1,
        2,
        3,
    ]
    assert prepared["properties"]["missing_fact_ids"]["maxItems"] == 0


def test_other_groq_model_keeps_json_object_recovery():
    assert recovery_format(
        "other-groq-model", "BulletsSlide", BulletsSlide.model_json_schema()
    ) == {"type": "json_object"}
