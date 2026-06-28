"""T4 — rendering the graph-knowledge block + the K4 slot + budget reduction.

T4's gate is mechanical (the orchestrator's scope): the block renders in the
supplementary region with the versioned usage-guidance artifact; a zero-graph
turn is byte-identical to Phase 1 (criterion 9); the graph sheds nodes
gracefully under budget pressure before self-facts; and the K4 surfacing slot
threads end-to-end as a reserved no-op stub. The *performed-knowledge quality*
the guidance aims for is T8's judged eval + the human operator-pass, NOT asserted
here.
"""

# ruff: noqa: SLF001 — reduction tests assert against the builder's private ladder.

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.schema.chunks import PersonaChunk
from persona.schema.conversation import ConversationMessage
from persona.schema.persona import Persona, PersonaIdentity
from persona_runtime.prompt import (
    K3_USAGE_GUIDANCE_VERSION,
    GraphContext,
    GraphKnowledgeItem,
    GraphRecency,
    PromptBuilder,
    RetrievedContext,
)


def _chunk(text: str, *, meta: dict[str, str] | None = None) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 10000}",
        text=text,
        metadata=meta or {},
        created_at=datetime.now(UTC),
    )


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="tenancy law assistant",
            background="Knows husleieloven.",
            constraints=["Never give binding advice."],
        ),
        tools=[],
    )


def _item(
    content: str = "Eats a vegetarian diet.",
    *,
    concept_name: str = "vegetarian",
    recency: GraphRecency = GraphRecency.RECENT,
    source_persona: str | None = "kai",
    wellbeing_category: str | None = None,
) -> GraphKnowledgeItem:
    return GraphKnowledgeItem(
        concept_name=concept_name,
        content=content,
        recency=recency,
        source_persona=source_persona,
        wellbeing_category=wellbeing_category,
    )


def _graph(*items: GraphKnowledgeItem) -> GraphContext:
    return GraphContext(items=items)


@pytest.fixture
def builder() -> PromptBuilder:
    return PromptBuilder()


def _system(builder: PromptBuilder, ctx: RetrievedContext, **kw: object) -> str:
    msgs = builder.build(
        _persona(), ctx, history=[], skill_index="", user_message="q", max_tokens=8000, **kw
    )
    return msgs[0].content  # type: ignore[return-value]


class TestGraphBlockRender:
    def test_renders_block_with_guidance_and_facts_when_graph_present(
        self, builder: PromptBuilder
    ) -> None:
        system = _system(builder, RetrievedContext(graph=_graph(_item())))
        assert "What you already know about this person:" in system
        assert "Eats a vegetarian diet." in system
        # The versioned usage guidance rides with the block.
        assert "Knowing is not for showing." in system or "not for\nshowing" in system
        assert "no mention of notes, a profile, or a graph" in system

    def test_usage_guidance_artifact_is_versioned(self) -> None:
        assert K3_USAGE_GUIDANCE_VERSION == "v1"

    def test_recency_phrasing_is_rendered(self, builder: PromptBuilder) -> None:
        recent = _system(
            builder, RetrievedContext(graph=_graph(_item(recency=GraphRecency.RECENT)))
        )
        old = _system(builder, RetrievedContext(graph=_graph(_item(recency=GraphRecency.LONG_AGO))))
        assert "[recently, from kai]" in recent
        assert "[a long time ago, from kai]" in old

    def test_source_rendered_for_attribution_absent_when_none(self, builder: PromptBuilder) -> None:
        with_src = _system(builder, RetrievedContext(graph=_graph(_item(source_persona="kai"))))
        no_src = _system(builder, RetrievedContext(graph=_graph(_item(source_persona=None))))
        assert "from kai" in with_src
        assert "from" not in no_src.split("What you already know")[1]

    def test_wellbeing_category_is_never_rendered_as_a_label(self, builder: PromptBuilder) -> None:
        # The category routes the K4 slot; it must not leak into the prompt text.
        system = _system(
            builder, RetrievedContext(graph=_graph(_item(wellbeing_category="eating_disorder")))
        )
        assert "eating_disorder" not in system

    def test_placement_is_below_the_floor_and_among_supplementary_memory(
        self, builder: PromptBuilder
    ) -> None:
        ctx = RetrievedContext(
            self_facts=[_chunk("I specialise in tenancy law.")],
            worldview=[_chunk("Tenants have strong protections.")],
            episodic=[_chunk("Last time we discussed mould.")],
            graph=_graph(_item()),
        )
        system = _system(builder, ctx)
        i_identity = system.index("You are Astrid")
        i_constraints = system.index("You must NOT:")
        i_worldview = system.index("Your views:")
        i_graph = system.index("What you already know about this person:")
        i_episodic = system.index("From earlier conversations:")
        # Floor (identity + constraints) strictly above the graph block.
        assert i_identity < i_constraints < i_graph
        # In the supplementary region: after worldview, before episodic.
        assert i_worldview < i_graph < i_episodic


class TestZeroGraphByteIdentical:
    def test_empty_graph_renders_no_block(self, builder: PromptBuilder) -> None:
        system = _system(builder, RetrievedContext())
        assert "What you already know about this person:" not in system
        assert K3_USAGE_GUIDANCE_VERSION not in system  # no guidance markers at all

    def test_empty_graph_is_byte_identical_to_no_graph_field(self, builder: PromptBuilder) -> None:
        # The criterion-9 guard: a default (empty-graph) context produces the
        # exact same system text as one that never mentions the graph.
        ctx_plain = RetrievedContext(self_facts=[_chunk("I specialise in tenancy law.")])
        ctx_empty_graph = RetrievedContext(
            self_facts=[_chunk("I specialise in tenancy law.")], graph=GraphContext()
        )
        assert _system(builder, ctx_plain) == _system(builder, ctx_empty_graph)


class TestK4SurfacingSlot:
    def test_slot_is_a_noop_by_default(self, builder: PromptBuilder) -> None:
        # Reserved-never-built: a categorized item renders no care text without a
        # provider (the stub until K4 lands).
        system = _system(
            builder, RetrievedContext(graph=_graph(_item(wellbeing_category="eating_disorder")))
        )
        assert "(care:" not in system

    def test_slot_injects_care_text_when_a_provider_is_wired(self, builder: PromptBuilder) -> None:
        seen: list[str] = []

        def provider(category: str, recency: GraphRecency) -> str | None:  # noqa: ARG001 — recency unused here
            seen.append(category)
            return "care: be gentle here"

        system = _system(
            builder,
            RetrievedContext(graph=_graph(_item(wellbeing_category="eating_disorder"))),
            graph_surfacing_guidance=provider,
        )
        assert "(care: be gentle here)" in system
        assert seen == ["eating_disorder"]  # consulted with the node's category

    def test_slot_not_consulted_for_uncategorized_items(self, builder: PromptBuilder) -> None:
        seen: list[str] = []

        def provider(category: str, recency: GraphRecency) -> str | None:  # noqa: ARG001 — recency unused here
            seen.append(category)
            return "care text"

        _system(
            builder,
            RetrievedContext(graph=_graph(_item(wellbeing_category=None))),
            graph_surfacing_guidance=provider,
        )
        assert seen == []  # no category → slot never consulted


class TestGraphReductionLadder:
    def _ctx(self, n_graph: int) -> RetrievedContext:
        items = tuple(_item(f"fact {i}", concept_name=f"c{i}") for i in range(n_graph))
        return RetrievedContext(
            self_facts=[_chunk("sf")],
            worldview=[_chunk("wv")],
            episodic=[_chunk("ep")],
            graph=GraphContext(items=items),
        )

    def test_graph_sheds_nodes_then_drops_before_worldview_and_self_facts(self) -> None:
        stages = PromptBuilder._reductions(self._ctx(10))
        graph_sizes = [len(c.graph.items) for c, _ in stages]
        # Sheds 10 → 7 → 5 → 3 (peer of episodic) then → 0 before worldview/self-facts.
        assert graph_sizes[:4] == [7, 5, 3, 3]  # 7/5/3 shed, then episodic-drop holds 3
        assert graph_sizes[-3:] == [0, 0, 0]  # graph gone for the worldview/self-facts drops

    def test_graph_is_empty_before_worldview_is_dropped(self) -> None:
        stages = PromptBuilder._reductions(self._ctx(10))
        # Find the first stage that drops worldview; the graph must already be gone.
        for ctx, _ in stages:
            if not ctx.worldview:
                assert ctx.graph.items == ()
                break
        else:
            pytest.fail("no worldview-drop stage found")

    def test_self_facts_outlast_the_graph_under_pressure(self) -> None:
        stages = PromptBuilder._reductions(self._ctx(10))
        # In every stage where the graph still has nodes, self-facts are intact:
        # externally-shared knowledge yields before the persona's own core.
        for ctx, _ in stages:
            if ctx.graph.items:
                assert ctx.self_facts

    def test_zero_graph_ladder_is_exactly_phase_1(self) -> None:
        # No graph → no extra stages: the four-stage (3 without docs) Phase-1
        # ladder, unchanged. Graph stays empty throughout.
        ctx = RetrievedContext(
            self_facts=[_chunk("sf")], worldview=[_chunk("wv")], episodic=[_chunk("ep")]
        )
        stages = PromptBuilder._reductions(ctx)
        assert len(stages) == 3  # drop episodic → worldview → self-facts (no docs, no graph)
        assert all(c.graph.items == () for c, _ in stages)

    def test_reduction_under_real_budget_keeps_floor_drops_graph(
        self, builder: PromptBuilder
    ) -> None:
        # A tight budget forces reduction; identity/constraints survive, the graph
        # block degrades (fewer or no nodes), never a broken prompt.
        big_items = tuple(
            _item("a long-ish remembered fact that consumes tokens " * 8, concept_name=f"c{i}")
            for i in range(10)
        )
        ctx = RetrievedContext(graph=GraphContext(items=big_items))
        msgs = builder.build(
            _persona(),
            ctx,
            history=[ConversationMessage(role="user", content="hi", created_at=datetime.now(UTC))],
            skill_index="",
            user_message="q",
            max_tokens=120,
        )
        system = msgs[0].content
        assert "You are Astrid" in system  # floor held
        assert "You must NOT:" in system
        assert system.count("a long-ish remembered fact") < 10  # graph shed under pressure
