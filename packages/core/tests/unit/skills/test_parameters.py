"""B1b: parameters validation engine (D-24-8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from persona.errors import SkillArgumentValidationError
from persona.schema.skills import SkillSpec
from persona.skills.parameters import (
    build_parameter_model,
    coerce_parameters,
    validate_parameters,
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["format"],
    "properties": {
        "format": {"type": "string", "enum": ["docx", "pdf", "md"]},
        "template": {"type": "string"},
        "content_spec": {"type": "object"},
        "copies": {"type": "integer"},
    },
}

# Mirrors the document_generation SKILL.md after D-24-content-spec-string:
# content_spec accepts EITHER a plain string OR an object.
_SCHEMA_UNION = {
    "type": "object",
    "additionalProperties": False,
    "required": ["format"],
    "properties": {
        "format": {"type": "string", "enum": ["docx", "pdf", "md"]},
        "content_spec": {"oneOf": [{"type": "string"}, {"type": "object"}]},
    },
}


def _spec(**kw: object) -> SkillSpec:
    return SkillSpec(name="doc", description="d", path=Path("/tmp/doc"), **kw)  # type: ignore[arg-type]


def test_no_schema_is_a_noop() -> None:
    validate_parameters(_spec(), {"anything": 1})  # no parameters → accept


def test_valid_args_pass() -> None:
    spec = _spec(parameters=_SCHEMA)
    validate_parameters(spec, {"format": "docx", "template": "memo"})
    validate_parameters(spec, {"format": "md"})


def test_missing_required_format_raises() -> None:
    spec = _spec(parameters=_SCHEMA)
    with pytest.raises(SkillArgumentValidationError) as exc:
        validate_parameters(spec, {"template": "memo"})
    assert exc.value.context["skill"] == "doc"
    assert "format" in exc.value.context["errors"]


def test_enum_violation_raises() -> None:
    spec = _spec(parameters=_SCHEMA)
    with pytest.raises(SkillArgumentValidationError):
        validate_parameters(spec, {"format": "odt"})


def test_extra_property_rejected_by_additionalproperties_false() -> None:
    spec = _spec(parameters=_SCHEMA)
    with pytest.raises(SkillArgumentValidationError):
        validate_parameters(spec, {"format": "docx", "bogus": "x"})


def test_wrong_type_rejected() -> None:
    spec = _spec(parameters=_SCHEMA)
    with pytest.raises(SkillArgumentValidationError):
        validate_parameters(spec, {"format": "docx", "copies": "two"})


def test_build_model_marks_required_and_optional() -> None:
    model = build_parameter_model("doc", _SCHEMA)
    assert model.model_fields["format"].is_required()
    assert not model.model_fields["template"].is_required()


# --- D-24-content-spec-string: string→object coercion ----------------------


def test_coerce_wraps_bare_string_for_object_property() -> None:
    # The natural call: content_spec is a plain string of the document text.
    spec = _spec(parameters=_SCHEMA)
    coerced = coerce_parameters(spec, {"format": "docx", "content_spec": "The body."})
    assert coerced == {"format": "docx", "content_spec": {"content": "The body."}}


def test_coerce_wraps_bare_string_for_union_object_property() -> None:
    # Same coercion when the property is a oneOf[string, object] union.
    spec = _spec(parameters=_SCHEMA_UNION)
    coerced = coerce_parameters(spec, {"format": "md", "content_spec": "Hello."})
    assert coerced == {"format": "md", "content_spec": {"content": "Hello."}}


def test_coerce_leaves_dict_content_spec_unchanged() -> None:
    # Back-compat: the explicit-dict form is passed through untouched.
    spec = _spec(parameters=_SCHEMA)
    args = {"format": "docx", "content_spec": {"title": "T", "sections": []}}
    coerced = coerce_parameters(spec, args)
    assert coerced == args


def test_coerce_does_not_wrap_plain_string_property() -> None:
    # `template` is a plain string property — never coerced to an object.
    spec = _spec(parameters=_SCHEMA)
    coerced = coerce_parameters(spec, {"format": "docx", "template": "memo"})
    assert coerced == {"format": "docx", "template": "memo"}


def test_coerce_is_pure_does_not_mutate_input() -> None:
    spec = _spec(parameters=_SCHEMA)
    args = {"format": "docx", "content_spec": "text"}
    coerce_parameters(spec, args)
    assert args == {"format": "docx", "content_spec": "text"}


def test_coerce_noop_without_schema() -> None:
    coerced = coerce_parameters(_spec(), {"content_spec": "text"})
    assert coerced == {"content_spec": "text"}


def test_coerced_string_and_dict_forms_validate_identically() -> None:
    # Both natural-string and explicit-dict produce the SAME validated shape.
    spec = _spec(parameters=_SCHEMA)
    from_string = coerce_parameters(spec, {"format": "pdf", "content_spec": "Body."})
    from_dict = coerce_parameters(spec, {"format": "pdf", "content_spec": {"content": "Body."}})
    assert from_string == from_dict
    validate_parameters(spec, from_string)  # both pass strict validation
    validate_parameters(spec, from_dict)


def test_union_property_accepts_object_after_coercion() -> None:
    # The union validator accepts the coerced dict (object branch).
    spec = _spec(parameters=_SCHEMA_UNION)
    coerced = coerce_parameters(spec, {"format": "md", "content_spec": "x"})
    validate_parameters(spec, coerced)


def test_union_property_still_accepts_a_raw_dict() -> None:
    # And it accepts a structured dict directly (object branch, no coercion).
    spec = _spec(parameters=_SCHEMA_UNION)
    validate_parameters(spec, {"format": "md", "content_spec": {"title": "T"}})
