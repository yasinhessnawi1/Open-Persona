"""Unit tests for the authoring response parser (spec 10, T02, §3.3 / D-10-3).

Deterministic, model-free. The parser must be lenient and never raise — these
cover the spec §8 marker fragility: clean marker, missing marker, text variants,
fenced YAML, malformed/empty questions JSON, and the marker nested in content.
"""

from __future__ import annotations

import pytest
from persona_api.services.authoring_parse import split_response, strip_fences

_YAML = 'schema_version: "1.0"\nidentity:\n  name: Sage\n  role: cook'
_QUESTIONS_JSON = '[{"section": "identity", "question": "Which cuisine?"}]'


def test_clean_marker_splits_yaml_and_questions() -> None:
    text = f"{_YAML}\n---QUESTIONS---\n{_QUESTIONS_JSON}"
    yaml_text, questions = split_response(text)
    assert yaml_text == _YAML
    assert len(questions) == 1
    assert questions[0].section == "identity"
    assert questions[0].question == "Which cuisine?"


def test_missing_marker_treats_whole_as_yaml() -> None:
    yaml_text, questions = split_response(_YAML)
    assert yaml_text == _YAML
    assert questions == []


def test_questions_colon_variant_is_honoured() -> None:
    text = f"{_YAML}\n\nQuestions:\n{_QUESTIONS_JSON}"
    yaml_text, questions = split_response(text)
    assert yaml_text == _YAML
    assert len(questions) == 1


def test_clarifying_questions_variant_is_honoured() -> None:
    text = f"{_YAML}\n\nClarifying questions:\n{_QUESTIONS_JSON}"
    yaml_text, questions = split_response(text)
    assert yaml_text == _YAML
    assert len(questions) == 1


def test_fenced_yaml_is_stripped() -> None:
    text = f"```yaml\n{_YAML}\n```\n---QUESTIONS---\n{_QUESTIONS_JSON}"
    yaml_text, questions = split_response(text)
    assert yaml_text == _YAML
    assert len(questions) == 1


def test_malformed_questions_json_degrades_to_none() -> None:
    text = f"{_YAML}\n---QUESTIONS---\n[not valid json"
    yaml_text, questions = split_response(text)
    assert yaml_text == _YAML
    assert questions == []  # never raises; just no questions


def test_empty_questions_block_is_no_questions() -> None:
    text = f"{_YAML}\n---QUESTIONS---\n"
    yaml_text, questions = split_response(text)
    assert yaml_text == _YAML
    assert questions == []


def test_questions_array_not_a_list_degrades() -> None:
    text = f'{_YAML}\n---QUESTIONS---\n{{"section": "x"}}'
    yaml_text, questions = split_response(text)
    # a bare object (not an array) -> the JSON-array regex misses it -> []
    assert questions == []


def test_marker_is_case_insensitive() -> None:
    text = f"{_YAML}\n---questions---\n{_QUESTIONS_JSON}"
    _, questions = split_response(text)
    assert len(questions) == 1


def test_questions_colon_without_array_does_not_false_split() -> None:
    # A YAML whose content happens to contain "questions:" but no JSON array
    # after it must NOT be treated as a marker (the variant requires a `[`).
    yaml_with_word = f"{_YAML}\n  background: answers common questions: politely"
    yaml_text, questions = split_response(yaml_with_word)
    assert questions == []
    assert "background: answers common questions" in yaml_text


def test_questions_drop_items_without_a_question_field() -> None:
    text = (
        f"{_YAML}\n---QUESTIONS---\n"
        '[{"section": "a", "question": "Q1?"}, {"section": "b"}, {"question": "Q3?"}]'
    )
    _, questions = split_response(text)
    assert [q.question for q in questions] == ["Q1?", "Q3?"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("```yaml\nx: 1\n```", "x: 1"),
        ("```\nx: 1\n```", "x: 1"),
        ("x: 1", "x: 1"),
        ("  x: 1  ", "x: 1"),
    ],
)
def test_strip_fences(raw: str, expected: str) -> None:
    assert strip_fences(raw) == expected
