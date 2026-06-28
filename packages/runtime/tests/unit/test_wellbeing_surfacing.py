"""The K4 surfacing-guidance provider (Spec K4, T3; K4-D-3 / K4-D-X-surfacing-recency-seam).

Proves the runtime provider maps K3's ``GraphRecency`` to the policy ``RecencyBand``
and returns the recency-weighted core care text — and, end to end, that passing it as
``PromptBuilder.build``'s ``graph_surfacing_guidance`` renders the care text alongside
a flagged node with the band-correct wording, while never narrating the raw tag.
"""

from __future__ import annotations

from persona.schema.persona import Persona, PersonaIdentity
from persona.wellbeing import WellbeingCategory
from persona.wellbeing_care_text import care_text
from persona.wellbeing_policy import RecencyBand
from persona_runtime.prompt import (
    GraphContext,
    GraphKnowledgeItem,
    GraphRecency,
    PromptBuilder,
    RetrievedContext,
)
from persona_runtime.wellbeing import recency_band, surfacing_guidance


class TestRecencyBandMapping:
    def test_recent_is_acute(self) -> None:
        assert recency_band(GraphRecency.RECENT) is RecencyBand.ACUTE

    def test_a_while_back_is_historical(self) -> None:
        assert recency_band(GraphRecency.A_WHILE_BACK) is RecencyBand.HISTORICAL

    def test_long_ago_is_historical(self) -> None:
        assert recency_band(GraphRecency.LONG_AGO) is RecencyBand.HISTORICAL


class TestSurfacingProvider:
    def test_known_tag_returns_band_weighted_text(self) -> None:
        assert surfacing_guidance("self_harm", GraphRecency.RECENT) == care_text(
            WellbeingCategory.SELF_HARM, RecencyBand.ACUTE
        )

    def test_recency_selects_acute_vs_historical(self) -> None:
        # The same category yields different text for recent vs long-ago — the recency
        # weighting flows through the provider, not just the catalogue.
        acute = surfacing_guidance("mental_health_crisis", GraphRecency.RECENT)
        historical = surfacing_guidance("mental_health_crisis", GraphRecency.LONG_AGO)
        assert acute is not None
        assert historical is not None
        assert acute != historical

    def test_unknown_or_none_tag_is_none(self) -> None:
        assert surfacing_guidance("mental_health", GraphRecency.RECENT) is None  # legacy tag
        assert surfacing_guidance("", GraphRecency.RECENT) is None


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(name="Astrid", role="assistant", background="."),
        tools=[],
    )


def _ctx(*, wellbeing_category: str | None, recency: GraphRecency) -> RetrievedContext:
    item = GraphKnowledgeItem(
        concept_name="c",
        content="A sensitive circumstance.",
        recency=recency,
        wellbeing_category=wellbeing_category,
    )
    return RetrievedContext(graph=GraphContext(items=(item,)))


class TestEndToEndRender:
    def test_care_text_rides_the_slot_for_a_flagged_node(self) -> None:
        ctx = _ctx(wellbeing_category="self_harm", recency=GraphRecency.RECENT)
        msgs = PromptBuilder().build(
            _persona(),
            ctx,
            history=[],
            skill_index="",
            user_message="q",
            max_tokens=8000,
            graph_surfacing_guidance=surfacing_guidance,
        )
        system = msgs[0].content
        assert isinstance(system, str)
        # The acute self-harm care text rides the slot; the raw tag never appears.
        assert "never provide, repeat, or ask about methods or means" in system
        assert "self_harm" not in system
        assert "A sensitive circumstance." in system

    def test_band_correct_wording_for_historical(self) -> None:
        ctx = _ctx(wellbeing_category="self_harm", recency=GraphRecency.LONG_AGO)
        msgs = PromptBuilder().build(
            _persona(),
            ctx,
            history=[],
            skill_index="",
            user_message="q",
            max_tokens=8000,
            graph_surfacing_guidance=surfacing_guidance,
        )
        system = msgs[0].content
        assert isinstance(system, str)
        assert "some time ago" in system  # the lighter historical framing

    def test_no_provider_renders_no_care_text(self) -> None:
        # Back-compat: the default no-op path — flagged node, no provider, no care text.
        ctx = _ctx(wellbeing_category="self_harm", recency=GraphRecency.RECENT)
        msgs = PromptBuilder().build(
            _persona(), ctx, history=[], skill_index="", user_message="q", max_tokens=8000
        )
        system = msgs[0].content
        assert isinstance(system, str)
        assert "methods or means" not in system
        assert "A sensitive circumstance." in system  # content still surfaces
