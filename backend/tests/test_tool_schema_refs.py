"""LLM parameter schemas inline finite refs without weakening JSON validation.

These are local Draft2020-12 checks, not evidence of a live provider accepting
schemas. The shared contract export deliberately retains its own definitions.
"""

from copy import deepcopy
from enum import StrEnum

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field

from app.tools.registry import TOOLS, tool_specs
from app.tools.schema import tools_schema
from app.tools.schema_refs import SchemaInliningError, inline_local_refs

EXAMPLES = {
    "search_flights": {"origin": "SVO", "destination": "DXB", "departure_date": "2026-11-18"},
    "search_hotels": {"destination": "DXB", "check_in": "2026-11-18", "check_out": "2026-11-25"},
    "search_trains": {"origin": "MOW", "destination": "LED", "departure_date": "2026-11-18"},
    "get_transfer": {"airport": "DXB", "hotel_id": "osm:node/530098775", "pickup_at": "2026-11-18T16:00:00+04:00"},
    "suggest_destinations": {"origin": "SVO", "month": 11},
    "price_calendar": {"origin": "SVO", "destination": "DXB", "month": "2026-11"},
}


def assert_no_references(value):
    if isinstance(value, dict):
        assert not {"$ref", "$defs", "definitions"} & value.keys()
        for child in value.values():
            assert_no_references(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_references(child)


def assert_validation_parity(original, flattened, instances):
    Draft202012Validator.check_schema(original)
    Draft202012Validator.check_schema(flattened)
    before = Draft202012Validator(original, format_checker=FormatChecker())
    after = Draft202012Validator(flattened, format_checker=FormatChecker())
    for instance in instances:
        assert before.is_valid(instance) == after.is_valid(instance), instance


def test_every_registered_llm_schema_has_no_refs_and_preserves_semantics():
    specs = {spec.name: spec for spec in tool_specs()}
    assert set(specs) == set(TOOLS) == set(EXAMPLES)
    for name, tool in TOOLS.items():
        source = tool.input_model.model_json_schema()
        flattened = specs[name].parameters
        assert_no_references(flattened)
        assert flattened.get("required") == source.get("required")
        assert flattened.get("additionalProperties") == source.get("additionalProperties")
        valid = EXAMPLES[name]
        normalized = tool.input_model.model_validate(valid).model_dump(mode="json")
        instances = [valid, normalized, {}, {**valid, "not_a_field": 1}]
        for party in (
            {"adults": 0},
            {"children_ages": [18]},
            {"pets": -1},
            {"adults": 2, "children_ages": [0, 17], "pets": 0},
            {"children_ages": [None]},
        ):
            instances.append({**valid, "travelers": party})
        if "max_price_rub" in source.get("properties", {}):
            instances.extend([{**valid, "max_price_rub": None}, {**valid, "max_price_rub": -1}])
        assert_validation_parity(source, flattened, instances)
    # This separate representation still serves documentation/schema consumers.
    assert tools_schema()["$defs"]


class Choice(StrEnum):
    ONE = "one"
    TWO = "two"


class Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1, le=5)
    title: str = Field(min_length=2)


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    child: Nested | None
    repeated: list[Nested] = Field(default_factory=list)
    choice: Choice = Choice.ONE
    nullable_budget: int | None = Field(default=None, ge=0)


def test_nested_repeated_models_enum_nullable_and_defaults_remain_equivalent():
    source = Envelope.model_json_schema()
    unchanged = deepcopy(source)
    flattened = inline_local_refs(source)
    assert source == unchanged
    assert_no_references(flattened)
    assert flattened["required"] == ["child"]
    assert flattened["properties"]["nullable_budget"]["default"] is None
    instances = [
        {"child": None},
        {"child": {"count": 2, "title": "ok"}, "repeated": [{"count": 5, "title": "yes"}], "choice": "two"},
        {},
        {"child": {}},
        {"child": {"count": 0, "title": "ok"}},
        {"child": {"count": 2, "title": "x"}},
        {"child": None, "choice": "three"},
        {"child": None, "nullable_budget": None},
        {"child": None, "nullable_budget": -1},
        {"child": None, "repeated": [{"count": 6, "title": "ok"}]},
        {"child": {"count": 2, "title": "ok", "extra": 1}},
    ]
    assert_validation_parity(source, flattened, instances)


def test_reference_siblings_keep_intersections_and_unevaluated_property_scope():
    source = {
        "$defs": {"Base": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}},
        "$ref": "#/$defs/Base",
        "properties": {"value": {"minimum": 2}},
        "allOf": [{"properties": {"value": {"maximum": 4}}}],
        "unevaluatedProperties": False,
    }
    flattened = inline_local_refs(source)
    assert_no_references(flattened)
    assert_validation_parity(
        source,
        flattened,
        [
            {"value": 2},
            {"value": 4},
            {"value": 1},
            {"value": 5},
            {"value": "2"},
            {"value": 2, "other": 3},
            {},
        ],
    )
    assert Draft202012Validator(flattened).is_valid({"value": 2})


def test_pointer_escaping_boolean_targets_and_literal_default_data():
    source = {"$defs": {"a/b~c": {"type": "integer", "minimum": 2}}, "$ref": "#/$defs/a~1b~0c"}
    assert_validation_parity(source, inline_local_refs(source), [1, 2, "2", None])
    assert_validation_parity(
        {"$defs": {"No": False}, "$ref": "#/$defs/No"},
        inline_local_refs({"$defs": {"No": False}, "$ref": "#/$defs/No"}),
        [1, None, {}],
    )
    source = {
        "type": "object",
        "default": {"$ref": "literal user data", "$defs": {"x": 1}},
        "examples": [{"$ref": "also literal"}],
    }
    assert inline_local_refs(source) == source
    annotation = {
        "$defs": {"Value": {"type": "integer", "title": "Original"}},
        "$ref": "#/%24defs/Value",
        "title": "Override",
        "default": 2,
    }
    flattened = inline_local_refs(annotation)
    assert flattened == {"type": "integer", "title": "Override", "default": 2}


@pytest.mark.parametrize(
    "source, message",
    [
        ({"$ref": "https://example.test/schema.json#/$defs/X"}, "External"),
        ({"$ref": "#/$defs/Missing"}, "Unresolved"),
        ({"$ref": "#/$defs/bad~2escape"}, "escape"),
        ({"$ref": "#namedAnchor"}, "JSON Pointer"),
        ({"$ref": "#/$defs/NotSchema", "$defs": {"NotSchema": 42}}, "does not identify a schema"),
        ({"$id": "https://example.test/scoped.json", "type": "object"}, "$id"),
        ({"$dynamicRef": "#node"}, "$dynamicRef"),
        ({"$recursiveRef": "#"}, "$recursiveRef"),
        ({"$ref": "#"}, "Recursive"),
        ({"$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}}, "$ref": "#/$defs/A"}, "Recursive"),
    ],
)
def test_unsupported_or_recursive_references_fail_clearly(source, message):
    with pytest.raises(SchemaInliningError, match=message.replace("$", r"\$")):
        inline_local_refs(source)


def test_reference_array_annotations_keep_unevaluated_items_scope():
    source = {
        "$defs": {"Sequence": {"type": "array", "prefixItems": [{"type": "integer"}]}},
        "$ref": "#/$defs/Sequence",
        "unevaluatedItems": False,
    }
    flattened = inline_local_refs(source)
    assert_no_references(flattened)
    assert_validation_parity(source, flattened, [[], [1], ["bad"], [1, 2], {}])
    assert Draft202012Validator(flattened).is_valid([1])


def test_local_pointer_can_target_an_array_schema_position():
    source = {"allOf": [{"type": "integer", "minimum": 2}], "$ref": "#/allOf/0"}
    assert_validation_parity(source, inline_local_refs(source), [1, 2, "2", None])
