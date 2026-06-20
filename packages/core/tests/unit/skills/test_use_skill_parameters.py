"""B1c: use_skill accepts + strictly validates parameters (D-24-8)."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — runtime fixture annotation

import pytest
from persona.schema.skills import SkillSpec
from persona.skills.use_skill_tool import make_use_skill_tool

_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["format"],
    "properties": {
        "format": {"type": "string", "enum": ["docx", "pdf", "md"]},
        # D-24-content-spec-string: accepts a plain string OR an object.
        "content_spec": {"oneOf": [{"type": "string"}, {"type": "object"}]},
    },
}


def _doc_skill(tmp_path: Path) -> SkillSpec:
    return SkillSpec(
        name="document_generation",
        description="gen",
        path=tmp_path,
        parameters=_PARAMS,
    )


def test_schema_now_exposes_optional_parameters_property(tmp_path: Path) -> None:
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    schema = t.parameters_schema
    assert "skill_name" in schema["properties"]
    assert "parameters" in schema["properties"]
    assert "skill_name" in schema["required"]
    assert "parameters" not in schema.get("required", [])
    assert schema.get("additionalProperties") is False


@pytest.mark.asyncio
async def test_parameterless_activation_data_unchanged(tmp_path: Path) -> None:
    # Byte-for-byte with the Spec 04 contract: no parameters → no extra key.
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    result = await t.execute(skill_name="document_generation")
    assert result.is_error is False
    assert result.data == {"skill_name": "document_generation"}


@pytest.mark.asyncio
async def test_valid_parameters_flow_into_data(tmp_path: Path) -> None:
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    result = await t.execute(skill_name="document_generation", parameters={"format": "docx"})
    assert result.is_error is False
    assert result.data == {"skill_name": "document_generation", "parameters": {"format": "docx"}}


@pytest.mark.asyncio
async def test_invalid_parameters_rejected_with_helpful_message(tmp_path: Path) -> None:
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    result = await t.execute(skill_name="document_generation", parameters={"format": "odt"})
    assert result.is_error is True
    assert "Invalid parameters for document_generation" in result.content


@pytest.mark.asyncio
async def test_empty_parameters_object_triggers_required_check(tmp_path: Path) -> None:
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    result = await t.execute(skill_name="document_generation", parameters={})
    assert result.is_error is True


@pytest.mark.asyncio
async def test_parameters_on_schemaless_skill_are_accepted(tmp_path: Path) -> None:
    plain = SkillSpec(name="web_research", description="r", path=tmp_path)
    t = make_use_skill_tool([plain])
    result = await t.execute(skill_name="web_research", parameters={"anything": 1})
    assert result.is_error is False
    assert result.data == {"skill_name": "web_research", "parameters": {"anything": 1}}


# --- D-24-content-spec-string: the natural string call succeeds first try ---


@pytest.mark.asyncio
async def test_content_spec_plain_string_is_coerced_and_accepted(tmp_path: Path) -> None:
    # The natural call the model makes — content_spec as a bare string — now
    # succeeds and is surfaced to the runtime in the {"content": ...} shape.
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    result = await t.execute(
        skill_name="document_generation",
        parameters={"format": "pdf", "content_spec": "The full document text."},
    )
    assert result.is_error is False
    assert result.data == {
        "skill_name": "document_generation",
        "parameters": {"format": "pdf", "content_spec": {"content": "The full document text."}},
    }


@pytest.mark.asyncio
async def test_content_spec_string_and_dict_yield_identical_handler_input(tmp_path: Path) -> None:
    # Back-compat: the explicit-dict form produces the SAME surfaced parameters
    # as the coerced plain-string form.
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    from_string = await t.execute(
        skill_name="document_generation",
        parameters={"format": "docx", "content_spec": "Body text."},
    )
    from_dict = await t.execute(
        skill_name="document_generation",
        parameters={"format": "docx", "content_spec": {"content": "Body text."}},
    )
    assert from_string.is_error is False
    assert from_dict.is_error is False
    assert from_string.data == from_dict.data


@pytest.mark.asyncio
async def test_content_spec_structured_dict_passes_through(tmp_path: Path) -> None:
    # A richer structured object (named sections) is preserved verbatim.
    t = make_use_skill_tool([_doc_skill(tmp_path)])
    spec_obj = {"title": "Q3", "sections": ["intro", "results"]}
    result = await t.execute(
        skill_name="document_generation",
        parameters={"format": "md", "content_spec": spec_obj},
    )
    assert result.is_error is False
    assert result.data["parameters"]["content_spec"] == spec_obj
