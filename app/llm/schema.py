"""Groq strict-schema recovery for the two configured GPT-OSS models."""

from copy import deepcopy
from typing import Any, Mapping


GPT_OSS_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})


def recovery_format(model: str, name: str, schema: Mapping[str, Any]) -> dict:
    """Narrow optional object fields for native constrained decoding.

    Nullable fields remain nullable, nested definitions retain their limits,
    and the application's Pydantic models and semantic checks still validate
    every result. Other Groq models keep the existing JSON-object behavior.
    """
    if model not in GPT_OSS_MODELS:
        return {"type": "json_object"}
    strict_schema = deepcopy(dict(schema))

    def visit(node):
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        if "const" in node:
            node["enum"] = [node.pop("const")]
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties)
            node["additionalProperties"] = False
            for child in properties.values():
                visit(child)
        for child in node.get("$defs", {}).values():
            visit(child)
        visit(node.get("items"))
        for keyword in ("anyOf", "oneOf", "allOf"):
            for child in node.get(keyword, []):
                visit(child)

    visit(strict_schema)
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": strict_schema},
    }
