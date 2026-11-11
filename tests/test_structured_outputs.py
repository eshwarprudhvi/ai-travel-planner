from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from app.integrations.llm.structured import extract_structured, to_strict_json_schema


# Override conftest DB fixture for pure unit tests
@pytest.fixture(scope="session", autouse=True)
def create_test_tables():
    yield


class Address(BaseModel):
    city: str
    country: str


class Person(BaseModel):
    name: str
    age: Optional[int] = None
    address: Address
    hobbies: list[str] = Field(default_factory=list)


def test_to_strict_json_schema_sets_additional_properties_false():
    schema = to_strict_json_schema(Person)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"name", "age", "address", "hobbies"}

    # Check nested definitions
    if "$defs" in schema:
        for def_schema in schema["$defs"].values():
            if def_schema.get("type") == "object" or "properties" in def_schema:
                assert def_schema["additionalProperties"] is False
                assert set(def_schema["required"]) == set(def_schema["properties"].keys())


def test_to_strict_json_schema_with_dict():
    raw = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
    }
    strict = to_strict_json_schema(raw)
    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == {"query", "limit"}


@pytest.mark.asyncio
async def test_extract_structured_calls_groq_with_strict_true():
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"city": "Tokyo", "country": "Japan"}'
    mock_response = MagicMock(choices=[mock_choice])

    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = await extract_structured(
        client=mock_client,
        response_model=Address,
        messages=[{"role": "user", "content": "Where is Shibuya?"}],
        strict=True,
    )

    assert isinstance(result, Address)
    assert result.city == "Tokyo"
    assert result.country == "Japan"

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    rf = call_kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "address"
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    assert set(rf["json_schema"]["schema"]["required"]) == {"city", "country"}

