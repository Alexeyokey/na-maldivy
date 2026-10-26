"""Inline finite local JSON Schema references for LLM tool parameters.

This operates on schema positions, never on literal defaults/examples. Shared
contract exports retain their original definitions. No validation constraints,
required fields or defaults are added by this transformation.
"""

from copy import deepcopy
from typing import Any
from urllib.parse import unquote


class SchemaInliningError(ValueError):
    """A schema cannot safely be represented without local references."""


_SCHEMA_VALUES = frozenset(
    {
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "items",
        "additionalItems",
        "unevaluatedItems",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
    }
)
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "dependentSchemas"})
_SCHEMA_ARRAYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_ANNOTATIONS = frozenset(
    {"title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly", "$comment"}
)


def inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy a finite schema, replacing local JSON Pointer refs and removing defs.

    Assertion siblings stay alongside an allOf reference expansion, preserving
    both intersecting constraints and unevaluatedProperties/Items annotation
    scope. Pure annotation siblings can be merged directly. External refs,
    recursive refs, URI-ID scopes and dynamic references fail explicitly.
    """
    if not isinstance(schema, dict):
        raise SchemaInliningError("Tool parameter schema must be an object")

    def resolve(reference: Any) -> dict[str, Any] | bool:
        if not isinstance(reference, str) or not reference.startswith("#"):
            raise SchemaInliningError(f"External or invalid schema reference is unsupported: {reference!r}")
        try:
            pointer = unquote(reference[1:], errors="strict")
        except UnicodeDecodeError as exc:
            raise SchemaInliningError(f"Invalid UTF-8 in local schema reference: {reference!r}") from exc
        if pointer and not pointer.startswith("/"):
            raise SchemaInliningError(f"Only local JSON Pointer references are supported: {reference!r}")
        current: Any = schema
        for token in pointer[1:].split("/") if pointer else ():
            if any(token[index + 1 : index + 2] not in {"0", "1"} for index, char in enumerate(token) if char == "~"):
                raise SchemaInliningError(f"Invalid JSON Pointer escape in schema reference: {reference!r}")
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and token in current:
                current = current[token]
            elif isinstance(current, list) and (
                token == "0" or token.isascii() and token.isdecimal() and not token.startswith("0")
            ):
                index = int(token)
                if index >= len(current):
                    raise SchemaInliningError(f"Unresolved local schema reference: {reference!r}")
                current = current[index]
            else:
                raise SchemaInliningError(f"Unresolved local schema reference: {reference!r}")
        if not isinstance(current, (dict, bool)):
            raise SchemaInliningError(f"Local reference does not identify a schema: {reference!r}")
        return current

    def visit(value: Any, references: tuple[str, ...]) -> dict[str, Any] | bool:
        if isinstance(value, bool):
            return value
        if not isinstance(value, dict):
            raise SchemaInliningError("Expected an object or boolean in a JSON Schema position")
        for unsupported in ("$id", "$dynamicRef", "$recursiveRef"):
            if unsupported in value:
                raise SchemaInliningError(f"Cannot safely inline schema with {unsupported}")
        result = {}
        for key, item in value.items():
            if key in {"$ref", "$defs", "definitions"}:
                continue
            if key in _SCHEMA_VALUES:
                result[key] = (
                    [visit(child, references) for child in item]
                    if key == "items" and isinstance(item, list)
                    else visit(item, references)
                )
            elif key in _SCHEMA_MAPS:
                result[key] = {name: visit(child, references) for name, child in item.items()}
            elif key in _SCHEMA_ARRAYS:
                result[key] = [visit(child, references) for child in item]
            elif key == "dependencies":
                result[key] = {
                    name: deepcopy(child) if isinstance(child, list) else visit(child, references)
                    for name, child in item.items()
                }
            else:
                result[key] = deepcopy(item)
        if "$ref" not in value:
            return result
        reference = value["$ref"]
        if reference in references:
            raise SchemaInliningError(
                f"Recursive schema reference cannot be inlined: {' -> '.join((*references, reference))}"
            )
        target = visit(resolve(reference), (*references, reference))
        if not result:
            return target
        if isinstance(target, dict) and result.keys() <= _ANNOTATIONS:
            return {**target, **result}
        # Keep assertion siblings at this level: nesting them in a second allOf
        # branch would change unevaluatedProperties/Items annotation scope.
        result["allOf"] = [target, *result.get("allOf", [])]
        return result

    result = visit(schema, ())
    return result if isinstance(result, dict) else {"allOf": [result]}
