"""Strict ``use_skill`` argument validation against a skill's ``parameters`` (D-24-8).

JSON Schema (2020-12) is the on-disk *interface*; a frozen ``extra="forbid"``
Pydantic model built from it at call time is the validation *engine* — no new
dependency (Pydantic is a base dep; there is deliberately **no** ``jsonschema``
library, per D-24-8). Only the subset of JSON Schema the skill ``parameters``
blocks actually use is translated: ``type: object`` with typed ``properties``
(``string`` / ``integer`` / ``number`` / ``boolean`` / ``object`` / ``array``,
optional ``enum``), a ``oneOf``/``anyOf`` union of those primitives, a
``required`` list, and ``additionalProperties`` (default forbid). Anything
outside the subset degrades to ``Any`` (accepted) rather than raising —
authoring errors surface as scan-time problems, not validation crashes.

**String→object coercion (D-24-content-spec-string):** a property that accepts
an ``object`` may also be supplied as a bare string — the natural call the model
makes (``content_spec="<the text>"`` rather than ``content_spec={"content":
"..."}``). :func:`coerce_parameters` wraps such a bare string into ``{"content":
<string>}`` *before* validation, so the obvious call succeeds on the first try
while the structured-object form keeps working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from persona.errors import SkillArgumentValidationError

if TYPE_CHECKING:
    from persona.schema.skills import SkillSpec

__all__ = ["build_parameter_model", "coerce_parameters", "validate_parameters"]

# The key a bare-string value is wrapped under when a property accepts an object
# (D-24-content-spec-string). Mirrors the SKILL.md guidance: ``content_spec="x"``
# coerces to ``{"content": "x"}``.
_STRING_COERCION_KEY = "content"

# JSON Schema primitive ``type`` → Python type used to build the validator.
_PRIMITIVES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict[str, Any],
    "array": list[Any],
}


def _union_branches(prop_schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ``oneOf``/``anyOf`` sub-schemas of a property, else ``[]``."""
    for keyword in ("oneOf", "anyOf"):
        branches = prop_schema.get(keyword)
        if isinstance(branches, list) and branches:
            return [b for b in branches if isinstance(b, dict)]
    return []


def _python_type(prop_schema: dict[str, Any]) -> Any:  # noqa: ANN401 — returns a dynamic type object (str/int/Literal/dict/Any/Union) for create_model
    """Map one JSON-Schema property to a Python type for the Pydantic model."""
    enum = prop_schema.get("enum")
    if isinstance(enum, list) and enum:
        # Literal over the enum members (runtime values; the function returns
        # Any so the dynamic Literal subscription is accepted).
        return Literal[tuple(enum)]
    branches = _union_branches(prop_schema)
    if branches:
        member_types = tuple(_python_type(b) for b in branches)
        if Any in member_types:
            # A bare-Any branch makes the whole union accept anything.
            return Any
        return Union[member_types]  # noqa: UP007 — dynamic tuple subscription
    json_type = prop_schema.get("type")
    if isinstance(json_type, str):
        return _PRIMITIVES.get(json_type, Any)
    return Any


def _accepts_object(prop_schema: dict[str, Any]) -> bool:
    """True if a property may be supplied as a JSON object (directly or via union)."""
    if prop_schema.get("type") == "object":
        return True
    return any(b.get("type") == "object" for b in _union_branches(prop_schema))


def build_parameter_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Compile a skill ``parameters`` JSON Schema into a strict Pydantic model.

    Args:
        name: The skill name (used for the generated model's class name).
        schema: The skill's ``parameters`` JSON Schema (``type: object``).

    Returns:
        A frozen Pydantic model. ``additionalProperties: false`` (the default)
        maps to ``extra="forbid"``; required properties are required fields,
        the rest optional with a ``None`` default.
    """
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    forbid = schema.get("additionalProperties", False) is False
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, prop in properties.items():
        prop_dict = prop if isinstance(prop, dict) else {}
        py_type = _python_type(prop_dict)
        if field_name in required:
            fields[field_name] = (py_type, ...)
        else:
            fields[field_name] = (Optional[py_type], None)  # noqa: UP045 — dynamic
    config = ConfigDict(extra="forbid" if forbid else "allow", frozen=True)
    # create_model is dynamically typed; the **fields splat defeats mypy's
    # field-definition checking — acceptable for this bounded translator.
    return create_model(  # type: ignore[call-overload, no-any-return]
        f"{name}_Params",
        __config__=config,
        **fields,
    )


def validate_parameters(spec: SkillSpec, args: dict[str, Any]) -> None:
    """Validate ``args`` against ``spec.parameters``; raise on mismatch.

    No-op when the skill declares no ``parameters`` schema (the common case —
    most skills take no call arguments).

    Args:
        spec: The activated skill.
        args: The ``parameters`` dict the model passed to ``use_skill``.

    Raises:
        SkillArgumentValidationError: ``args`` violate the declared schema;
            ``context`` names the skill and the joined validation messages.
    """
    schema = spec.parameters
    if not schema:
        return
    model = build_parameter_model(spec.name, schema)
    try:
        model.model_validate(args)
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise SkillArgumentValidationError(
            "invalid skill parameters",
            context={"skill": spec.name, "errors": messages},
        ) from exc


def coerce_parameters(spec: SkillSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Normalise ``args`` against ``spec.parameters`` before validation (D-24-content-spec-string).

    A property that accepts an ``object`` (e.g. ``content_spec``) may be supplied
    as a bare string — the natural call ``content_spec="<the text>"``. Such a
    string is wrapped into ``{"content": <string>}`` so the obvious call validates
    and the handler receives the same shape as the explicit-dict form. Any value
    that is already a dict (the back-compat structured form), or a property that
    does not accept an object, is returned unchanged.

    Pure function: returns a new dict, never mutates ``args``. A no-op when the
    skill declares no schema, or no declared property both accepts an object and
    was supplied as a string.

    Args:
        spec: The activated skill.
        args: The ``parameters`` dict the model passed to ``use_skill``.

    Returns:
        The normalised ``parameters`` dict to validate and store.
    """
    schema = spec.parameters
    if not schema:
        return args
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return args
    coerced = dict(args)
    for field_name, value in args.items():
        if not isinstance(value, str):
            continue
        prop = properties.get(field_name)
        if isinstance(prop, dict) and _accepts_object(prop):
            coerced[field_name] = {_STRING_COERCION_KEY: value}
    return coerced
