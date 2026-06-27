"""T5 — the budget-pressure validation (criterion 6 / K3-R-2), made real.

Not a synthetic ladder check (that is T4's unit assertion) — this drives the
*whole* builder with documents + history + graph + persona memory all present
and a real token budget (the shared ``cl100k_base`` encoder), and proves the
precedence emerges under genuine competition: as the budget tightens, the graph
block yields before the persona's own self-facts, while identity/constraints hold
as the floor. It also MEASURES the real 10-node block size against K3-R-2's
~1500–2000-tokens assumption (the number the shed points were sized on).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.schema.chunks import PersonaChunk
from persona.schema.conversation import ConversationMessage
from persona.schema.documents import DocumentChunk
from persona.schema.persona import Persona, PersonaIdentity
from persona.skills import count_tokens
from persona_runtime.prompt import (
    DocumentContext,
    DocumentInjection,
    GraphContext,
    GraphKnowledgeItem,
    GraphRecency,
    PromptBuilder,
    RetrievedContext,
)

# Representative accumulated graph knowledge — the shape K2 actually writes: a
# concise sentence or two of understanding per concept, a mix of short facts and
# fuller accumulations.
_NODE_CONTENTS = [
    "Eats a vegetarian diet and avoids all meat and fish, including at restaurants.",
    "Is the primary carer for an elderly parent who lives nearby and needs daily help.",
    "Works as a paediatric nurse on rotating night shifts at the city hospital.",
    "Is learning Spanish at an intermediate level and practises most mornings.",
    "Is planning to move to Oslo next month to start a new job in public health.",
    "Finds long study or focus sessions hard and gets distracted after about an hour.",
    "Takes metformin daily to manage blood sugar and prefers reminders in the evening.",
    "Has a border collie named Pixel who needs a long walk twice a day.",
    "Is saving toward a deposit for a first flat and is cautious about overspending.",
    "Prefers concise, direct explanations and dislikes being given options without a pick.",
]


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="a helpful generalist assistant",
            background="Adapts to what is known about the person.",
            constraints=["Never give binding legal advice."],
        ),
        tools=[],
    )


def _chunk(text: str) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 100000}", text=text, metadata={}, created_at=datetime.now(UTC)
    )


def _graph(n: int) -> GraphContext:
    items = tuple(
        GraphKnowledgeItem(
            concept_name=f"concept-{i}",
            content=_NODE_CONTENTS[i],
            recency=GraphRecency.A_WHILE_BACK,
            source_persona="kai",
        )
        for i in range(n)
    )
    return GraphContext(items=items)


def _context(n_graph: int = 10) -> RetrievedContext:
    return RetrievedContext(
        self_facts=[
            _chunk(f"I keep my answers grounded and practical (fact {i}).") for i in range(3)
        ],
        worldview=[
            _chunk(f"Good help adapts to the person's situation (view {i}).") for i in range(3)
        ],
        episodic=[
            _chunk(f"Earlier we worked through a planning problem together (turn {i}).")
            for i in range(3)
        ],
        graph=_graph(n_graph),
    )


def _documents() -> DocumentContext:
    body = " ".join(
        f"Clause {i}: the tenant retains the right to quiet enjoyment." for i in range(40)
    )
    return DocumentContext(
        whole_inject_docs=(DocumentInjection(title="lease.txt", format="txt", full_text=body),),
        retrieved_chunks=tuple(
            DocumentChunk(
                id=f"dc-{i}",
                text=f"Excerpt {i}: deposits must be returned within three weeks of move-out.",
                doc_ref="lease",
                format="txt",
                title="lease.txt",
                created_at=datetime.now(UTC),
            )
            for i in range(3)
        ),
    )


def _history(n_turns: int) -> list[ConversationMessage]:
    out: list[ConversationMessage] = []
    for i in range(n_turns):
        role = "user" if i % 2 == 0 else "assistant"
        out.append(
            ConversationMessage(
                role=role,  # type: ignore[arg-type]
                content=f"Turn {i}: we discussed the move and the budget in some detail here.",
                created_at=datetime.now(UTC),
            )
        )
    return out


@pytest.fixture
def builder() -> PromptBuilder:
    return PromptBuilder()


def _present(system: str) -> dict[str, bool]:
    return {
        "identity": "You are Astrid" in system,
        "constraints": "You must NOT:" in system,
        "self_facts": "Relevant facts about yourself:" in system,
        "worldview": "Your views:" in system,
        "graph": "What you already know about this person:" in system,
    }


class TestBlockSizeMeasurement:
    def test_measure_real_ten_node_block_against_the_estimate(self) -> None:
        # The real rendered block (guidance + 10 nodes), measured with the SAME
        # encoder the budgeter uses — the number the shed points were sized on.
        block = PromptBuilder._render_graph_knowledge(_graph(10), None)  # noqa: SLF001
        guidance_only = PromptBuilder._render_graph_knowledge(_graph(0), None)  # noqa: SLF001
        total = count_tokens(block)
        guidance = count_tokens(guidance_only)
        per_node = (total - guidance) / 10

        print("\nK3-R-2 graph-block size (cl100k_base, real rendered block):")
        print(f"  guidance overhead : {guidance} tokens (fixed, rides with the block)")
        print(f"  10-node block     : {total} tokens")
        print(f"  per-node mean     : {per_node:.1f} tokens")
        print("  K3-R-2 estimate   : ~1500–2000 tokens for 10 nodes")

        # Surface, don't silently pass: the block must not run MATERIALLY larger
        # than the estimate the reduction sizing assumed (a bigger-than-thought
        # block is what would quietly threaten the floor under pressure). Real
        # concise accumulated content runs at/under the estimate; guard the
        # upper bound generously.
        assert total < 2200, f"10-node block {total} tokens exceeds the K3-R-2 sizing assumption"


class TestPrecedenceUnderRealCompetition:
    def test_graph_yields_before_self_facts_with_everything_competing(
        self, builder: PromptBuilder
    ) -> None:
        ctx = _context(10)
        docs = _documents()
        history = _history(6)

        def present_at(budget: int) -> dict[str, bool]:
            msgs = builder.build(
                _persona(),
                ctx,
                history=history,
                skill_index="",
                user_message="help me plan my week",
                max_tokens=budget,
                document_context=docs,
            )
            return _present(msgs[0].content)  # type: ignore[arg-type]

        # The full prompt with everything present (loose budget).
        full = present_at(20000)
        assert all(full.values()), f"loose budget should fit everything: {full}"

        # Scan from loose to tight; record where each section first vanishes (the
        # LOOSEST budget at which it is already gone — earlier-yielding sections
        # vanish at a looser/higher budget).
        budgets = list(range(2000, 80, -120))
        first_gone: dict[str, int] = {}
        for b in budgets:
            p = present_at(b)
            for section, present in p.items():
                if not present and section not in first_gone:
                    first_gone[section] = b
            # The floor must NEVER vanish, at any budget.
            assert p["identity"], f"identity floor broken at budget {b}: {p}"
            assert p["constraints"], f"constraints floor broken at budget {b}: {p}"

        assert "graph" in first_gone, "graph never dropped under pressure"
        assert "self_facts" in first_gone, "self_facts never dropped (budget not tight enough)"
        # The precedence, proven live: the graph block is already gone at a looser
        # budget than self-facts — externally-shared knowledge yields before the
        # persona's own core.
        assert first_gone["graph"] > first_gone["self_facts"], (
            f"graph must yield before self-facts: {first_gone}"
        )

    def test_floor_holds_even_at_a_punishing_budget(self, builder: PromptBuilder) -> None:
        msgs = builder.build(
            _persona(),
            _context(10),
            history=_history(6),
            skill_index="",
            user_message="q",
            max_tokens=60,
            document_context=_documents(),
        )
        system = msgs[0].content
        assert "You are Astrid" in system  # identity floor
        assert "You must NOT:" in system  # constraints floor
        assert "What you already know about this person:" not in system  # graph gone first
