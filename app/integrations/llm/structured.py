"""Structured outputs utilities for Groq LLMs with strict schema enforcement.

Enforces Groq's constrained decoding requirements (Draft 2020-12 / Draft-07):
- `additionalProperties: false` on every object
- All properties explicitly listed in `required`
- `strict: true` enabled in the json_schema response_format
"""

from copy import deepcopy
from typing import Any, TypeVar

from groq import AsyncGroq
from pydantic import BaseModel

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)


def to_strict_json_schema(model_or_schema: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    """Convert a Pydantic model or raw JSON Schema dict into a strict schema.

    Strict mode requires:
    - Every object has `additionalProperties: false`.
    - Every defined property must appear in `required`.
    """
    if isinstance(model_or_schema, type) and issubclass(model_or_schema, BaseModel):
        raw_schema = model_or_schema.model_json_schema()
    elif isinstance(model_or_schema, dict):
        raw_schema = model_or_schema
    else:
        raise TypeError(f"Expected BaseModel subclass or dict, got {type(model_or_schema)}")

    strict = deepcopy(raw_schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["type"] = "object"
                node["additionalProperties"] = False
                props = node.setdefault("properties", {})
                node["required"] = list(props.keys())

            for val in node.values():
                walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(strict)
    return strict


async def extract_structured(
    client: AsyncGroq,
    response_model: type[T],
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    schema_name: str | None = None,
    strict: bool | None = None,
    temperature: float | None = 0.0,
    **kwargs: Any,
) -> T:
    """Execute a chat completion with structured outputs and strict schema enforcement.

    Args:
        client: AsyncGroq client.
        response_model: Pydantic model class describing the desired output.
        messages: Chat messages prompt list.
        model: Groq model name; defaults to settings.GROQ_MODEL.
        schema_name: Name for the schema; defaults to response_model.__name__.lower().
        strict: Enforce strict schema; defaults to settings.GROQ_STRICT_STRUCTURED_OUTPUTS (True).
        temperature: Sampling temperature (default 0.0 for deterministic extraction).
        **kwargs: Extra parameters passed to client.chat.completions.create.

    Returns:
        Instance of response_model parsed from Groq's response.
    """
    is_strict = settings.GROQ_STRICT_STRUCTURED_OUTPUTS if strict is None else strict
    chosen_model = model or settings.GROQ_MODEL
    name = schema_name or response_model.__name__.lower()

    schema = to_strict_json_schema(response_model)

    response_format: dict[str, Any] = {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": is_strict,
            "schema": schema,
        },
    }

    create_kwargs: dict[str, Any] = {
        "model": chosen_model,
        "messages": messages,
        "response_format": response_format,
        **kwargs,
    }
    if temperature is not None:
        create_kwargs["temperature"] = temperature

    completion = await client.chat.completions.create(**create_kwargs)

    raw_content = completion.choices[0].message.content
    if not raw_content:
        raise ValueError("Groq returned empty response content for structured output.")

    return response_model.model_validate_json(raw_content)

