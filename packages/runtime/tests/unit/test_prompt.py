"""Unit tests for persona_runtime.prompt (T05; D-05-6, D-05-7, D-05-8)."""

# ruff: noqa: SLF001 — budget tests assert against the builder's private helpers.

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.history import ConversationHistoryManager
from persona.schema.chunks import PersonaChunk
from persona.schema.conversation import Conversation, ConversationMessage
from persona.schema.persona import Persona, PersonaIdentity
from persona.skills import count_tokens
from persona_runtime.prompt import PromptBuilder, RetrievedContext


def _chunk(
    text: str, *, distance: float | None = None, meta: dict[str, str] | None = None
) -> PersonaChunk:
    return PersonaChunk(
        id=f"id-{abs(hash(text)) % 10000}",
        text=text,
        metadata=meta or {},
        distance=distance,
        created_at=datetime.now(UTC),
    )


def _persona(*, constraints: list[str] | None = None) -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background="Knows husleieloven.",
            constraints=constraints if constraints is not None else ["Never give binding advice."],
        ),
    )


def _msg(role: str, content: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=content, created_at=datetime.now(UTC))  # type: ignore[arg-type]


@pytest.fixture
def builder() -> PromptBuilder:
    return PromptBuilder()


class TestSectionOrdering:
    def test_system_block_has_sections_in_spec_order(self, builder: PromptBuilder) -> None:
        ctx = RetrievedContext(
            self_facts=[_chunk("I specialise in tenancy law.")],
            worldview=[_chunk("Tenants have strong protections.", meta={"epistemic": "fact"})],
            episodic=[_chunk("Last time we discussed mould.")],
        )
        msgs = builder.build(
            _persona(),
            ctx,
            history=[],
            skill_index="Available skills:\n- web_research",
            user_message="What are my rights?",
            max_tokens=8000,
            matched_skill_content="SKILL: do web research carefully.",
        )
        system = msgs[0].content
        # Each section appears, and in the spec §5.1 order.
        order_markers = [
            "You are Astrid",
            "You must NOT:",
            "Relevant facts about yourself:",
            "Your views:",
            "From earlier conversations:",
            "Available skills:",
            "SKILL: do web research",
            "Stay in character.",
        ]
        positions = [system.index(m) for m in order_markers]
        assert positions == sorted(positions), f"sections out of order: {positions}"

    def test_first_message_is_system_last_is_user(self, builder: PromptBuilder) -> None:
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[_msg("user", "earlier"), _msg("assistant", "reply")],
            skill_index="",
            user_message="current question",
            max_tokens=8000,
        )
        assert msgs[0].role == "system"
        assert msgs[-1].role == "user"
        assert msgs[-1].content == "current question"
        # History sits between the system block and the current user message.
        assert [m.content for m in msgs[1:-1]] == ["earlier", "reply"]

    def test_worldview_epistemic_tag_in_parentheses(self, builder: PromptBuilder) -> None:
        ctx = RetrievedContext(
            worldview=[_chunk("ODR is usually preferable.", meta={"epistemic": "belief"})]
        )
        system = builder.build(_persona(), ctx, [], "", "q", max_tokens=8000)[0].content
        assert "ODR is usually preferable. (belief)" in system

    def test_empty_skill_index_omitted(self, builder: PromptBuilder) -> None:
        system = builder.build(_persona(), RetrievedContext(), [], "", "q", max_tokens=8000)[
            0
        ].content
        assert "Available skills" not in system

    def test_matched_skill_content_none_omits_section(self, builder: PromptBuilder) -> None:
        system = builder.build(
            _persona(),
            RetrievedContext(),
            [],
            "idx",
            "q",
            max_tokens=8000,
            matched_skill_content=None,
        )[0].content
        # Footer present, but no skill body beyond the index string itself.
        assert "Stay in character." in system


class TestIdentityFloor:
    def test_identity_and_constraints_survive_budget_reduction(
        self, builder: PromptBuilder
    ) -> None:
        # Give a tiny budget so reduction fires; identity + constraints must remain.
        ctx = RetrievedContext(
            self_facts=[_chunk("fact " * 50)],
            worldview=[_chunk("view " * 50)],
            episodic=[_chunk("episode " * 50)],
        )
        msgs = builder.build(
            _persona(constraints=["Never give binding advice."]),
            ctx,
            history=[],
            skill_index="Available skills:\n- web_research",
            user_message="hi",
            max_tokens=80,  # forces dropping retrieved context
        )
        system = msgs[0].content
        assert "You are Astrid" in system
        assert "You must NOT:" in system
        assert "Available skills:" in system


class TestContextBudgetReduction:
    def test_episodic_dropped_first(self, builder: PromptBuilder) -> None:
        # Budget that fits identity+constraints+self_facts+worldview but not episodic.
        big = "word " * 200
        ctx = RetrievedContext(
            self_facts=[_chunk("a short fact")],
            worldview=[_chunk("a short view", meta={"epistemic": "fact"})],
            episodic=[_chunk(big)],
        )
        # Pick a budget above the no-episodic size but below the with-episodic size.
        no_ep = builder.build(
            _persona(), ctx.model_copy(update={"episodic": []}), [], "", "q", max_tokens=100000
        )
        budget = builder._token_total(no_ep) + 5
        msgs = builder.build(_persona(), ctx, [], "", "q", max_tokens=budget)
        system = msgs[0].content
        assert "a short fact" in system  # self_facts kept
        assert "a short view" in system  # worldview kept
        assert big.strip() not in system  # episodic dropped

    def test_reduction_order_episodic_then_worldview_then_self_facts(
        self, builder: PromptBuilder
    ) -> None:
        ctx = RetrievedContext(
            self_facts=[_chunk("SELFFACT")],
            worldview=[_chunk("WORLDVIEW", meta={"epistemic": "fact"})],
            episodic=[_chunk("EPISODIC")],
        )
        # T15 widened _reductions to return tuples of
        # (RetrievedContext, DocumentContext | None). Without documents, the
        # ladder still has 3 stages with the same RetrievedContext semantics.
        stages = builder._reductions(ctx)
        assert len(stages) == 3
        # Stage 1: only episodic dropped.
        assert stages[0][0].episodic == []
        assert stages[0][0].worldview != []
        # Stage 2: episodic + worldview dropped, self_facts kept.
        assert stages[1][0].episodic == []
        assert stages[1][0].worldview == []
        assert stages[1][0].self_facts != []
        # Stage 3: all retrieved context cleared.
        assert stages[2][0].self_facts == []
        # DocumentContext slot rides through unchanged.
        for _, doc_ctx in stages:
            assert doc_ctx is None


class TestAcceptance12ContextWindow:
    """Acceptance #12: a 30-turn conversation stays within an 8K mid-tier window.

    Per Phase 1 steer #7: construct a 30-turn Conversation directly and assert
    the rendered prompt token count is < 8000 after history-manage + prompt-build.
    No 30 mock round-trips.
    """

    def test_30_turn_prompt_under_8000_tokens(self, builder: PromptBuilder) -> None:
        manager = ConversationHistoryManager(compact_every=10, keep_recent=5)
        # 30 turns of realistic-length messages.
        messages = []
        for i in range(30):
            role = "user" if i % 2 == 0 else "assistant"
            text = (
                f"Turn {i}: this is a reasonably sized conversational message about "
                f"Norwegian tenancy law, deposits, and dispute resolution procedures."
            )
            messages.append(
                ConversationMessage(role=role, content=text, created_at=datetime.now(UTC))
            )  # type: ignore[arg-type]
        conv = Conversation(conversation_id="c30", persona_id="astrid", messages=messages)

        # The loop pre-computes the summary; here we simulate a compact summary.
        history = manager.manage(
            conv, summariser=lambda _msgs: "Summary of earlier turns about tenancy law."
        )

        ctx = RetrievedContext(
            self_facts=[_chunk("I specialise in husleieloven.")],
            worldview=[_chunk("Tenants have strong protections.", meta={"epistemic": "fact"})],
            episodic=[_chunk("Earlier we discussed mould complaints.")],
        )
        prompt = builder.build(
            _persona(),
            ctx,
            history,
            skill_index="Available skills:\n- web_research\n- document_drafting",
            user_message="So what should I do about my deposit?",
            max_tokens=8000,
        )
        total = sum(count_tokens(m.content) for m in prompt)
        assert total < 8000, f"30-turn prompt was {total} tokens (expected < 8000)"
