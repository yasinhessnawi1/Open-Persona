"""Structural self-harm means backstop (Spec K2, T8; D-K2-7 hardening).

Guidance alone is insufficient for a hard safety rule a jailbroken persona could
bypass (orchestrator T7 directive). This is a fast, deterministic, non-blocking
scan that rejects self-harm METHOD/MEANS specifics before a write — a structural
backstop, not a scrubber. It is best-effort by nature (a deterministic lexicon
cannot match the synthesis eval-gate's rigor); the residual is documented and
coordinated with K4. It MUST NOT over-reject ordinary health (the medication-budget
case) — means cues only bite in a self-harm context.
"""

from __future__ import annotations

import pytest
from persona_runtime.extraction.means_guard import contains_self_harm_means


@pytest.mark.parametrize(
    "text",
    [
        "I keep thinking about taking all my pills at once",
        "I've been hurting myself with a razor",
        "I want to kill myself by overdosing on my medication",
        "thinking about ending it with a noose",
        "I've thought about how many pills it would take",
    ],
)
def test_self_harm_with_means_is_flagged(text: str) -> None:
    assert contains_self_harm_means(text, category="self_harm")


def test_unambiguous_means_flagged_even_without_explicit_context() -> None:
    # Strong means terms imply self-harm even if no separate phrase is present.
    assert contains_self_harm_means("I bought a noose", category=None)
    assert contains_self_harm_means("thinking about an overdose", category=None)


@pytest.mark.parametrize(
    "text",
    [
        "I take pills every morning for my blood pressure",
        "I'm allergic to shellfish",
        "my daughter Lina started school",
        "I've been feeling really down lately",  # struggle, no means
        "I disclosed self-harm urges to my therapist",  # context, NO means specifics
    ],
)
def test_ordinary_or_means_free_content_is_not_flagged(text: str) -> None:
    # The medication-budget case must flow; a means-free disclosure is fine (the
    # care-relevant struggle is exactly what we DO keep).
    assert not contains_self_harm_means(text, category=None)


def test_means_free_self_harm_disclosure_passes_even_when_tagged() -> None:
    # A self_harm-tagged fact with no means specifics is allowed (we keep the struggle).
    assert not contains_self_harm_means(
        "the user disclosed self-harm urges recently", category="self_harm"
    )


def test_scan_is_case_insensitive() -> None:
    assert contains_self_harm_means("KILL MYSELF by OVERDOSE", category=None)
