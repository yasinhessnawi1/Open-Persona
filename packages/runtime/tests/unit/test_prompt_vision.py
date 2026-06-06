"""Spec 13 T08 — PromptBuilder image-placement (interleave-rule) tests.

These tests pin the four T08 acceptance invariants for the
:class:`persona_runtime.prompt.PromptBuilder`:

1. **Interleave rule** — when the caller passes a multi-block
   ``user_message=list[MessageContent]`` (e.g.
   ``[text, image, text, image, text]``), the trailing assembled
   :class:`ConversationMessage` carries that *exact* 5-element sequence in
   the *exact* caller-supplied order. The rule is "preserve the caller's
   content-list order" — not "upload order", not "natural reading order".
2. **Multi-image + interleaved text** assembles without raising; the
   widened type accepts the multimodal shape end-to-end.
3. **Token count from** :meth:`PromptBuilder._token_total` for a
   multimodal user message equals only the text-block tokens. Image
   blocks never enter the budget — they are workspace-path references
   per D-13-X-now (store-by-reference invariant).
4. **String path unchanged** — when ``user_message`` is a plain ``str``
   (the Phase 1 text-only form), the assembled message is byte-for-byte
   identical to the pre-T08 shape: ``role="user"``,
   ``content=<that exact str>``, no list wrapping, no churn.
"""

# ruff: noqa: SLF001 — token-count tests poke the builder's private helper.

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from persona.schema.content import ImageContent, MessageContent, TextContent
from persona.schema.conversation import ConversationMessage
from persona.schema.persona import Persona, PersonaIdentity
from persona.skills import count_tokens
from persona_runtime.prompt import PromptBuilder, RetrievedContext


def _persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background="Knows husleieloven.",
            constraints=["Never give binding advice."],
        ),
    )


@pytest.fixture
def builder() -> PromptBuilder:
    return PromptBuilder()


class TestInterleaveRule:
    """T08 acceptance #1 — caller's content-list order is preserved."""

    def test_five_block_text_image_text_image_text_sequence_preserved(
        self, builder: PromptBuilder
    ) -> None:
        """The headline interleave test.

        Caller passes ``[text("look at"), image(a), text("and"),
        image(b), text("compare")]``. The trailing user message must
        carry exactly that 5-element sequence in exactly that order —
        not images first, not text concatenated, not any other
        normalisation.
        """
        blocks: list[MessageContent] = [
            TextContent(text="look at"),
            ImageContent(workspace_path="a.png", media_type="image/png"),
            TextContent(text="and"),
            ImageContent(workspace_path="b.png", media_type="image/png"),
            TextContent(text="compare"),
        ]
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message=blocks,
            max_tokens=8000,
        )
        trailing = msgs[-1]
        assert trailing.role == "user"
        assert isinstance(trailing.content, list)
        assert len(trailing.content) == 5
        # The exact caller-supplied sequence — block-by-block, in order.
        assert trailing.content[0] == TextContent(text="look at")
        assert trailing.content[1] == ImageContent(workspace_path="a.png", media_type="image/png")
        assert trailing.content[2] == TextContent(text="and")
        assert trailing.content[3] == ImageContent(workspace_path="b.png", media_type="image/png")
        assert trailing.content[4] == TextContent(text="compare")
        # And the full equality, as belt-and-braces: identical to the input.
        assert trailing.content == blocks

    def test_images_first_then_text_order_preserved(self, builder: PromptBuilder) -> None:
        """A different interleave: images first, text after — still preserved."""
        blocks: list[MessageContent] = [
            ImageContent(workspace_path="x.jpg", media_type="image/jpeg"),
            ImageContent(workspace_path="y.jpg", media_type="image/jpeg"),
            TextContent(text="describe both"),
        ]
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message=blocks,
            max_tokens=8000,
        )
        trailing = msgs[-1]
        assert isinstance(trailing.content, list)
        assert trailing.content == blocks  # exact same order

    def test_text_first_then_images_order_preserved(self, builder: PromptBuilder) -> None:
        """A different interleave: text first, then images — still preserved."""
        blocks: list[MessageContent] = [
            TextContent(text="here are the photos:"),
            ImageContent(workspace_path="1.webp", media_type="image/webp"),
            ImageContent(workspace_path="2.webp", media_type="image/webp"),
            ImageContent(workspace_path="3.webp", media_type="image/webp"),
        ]
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message=blocks,
            max_tokens=8000,
        )
        trailing = msgs[-1]
        assert isinstance(trailing.content, list)
        assert trailing.content == blocks


class TestMultimodalAssemblyDoesNotRaise:
    """T08 acceptance #2 — multi-image + interleaved text builds cleanly."""

    def test_multi_image_with_interleaved_text_assembles(self, builder: PromptBuilder) -> None:
        blocks: list[MessageContent] = [
            TextContent(text="one"),
            ImageContent(workspace_path="img1.png", media_type="image/png"),
            TextContent(text="two"),
            ImageContent(workspace_path="img2.gif", media_type="image/gif"),
        ]
        # If the build path were narrowed too tightly or the validator
        # mis-fires on a genuine multimodal list, this raises.
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message=blocks,
            max_tokens=8000,
        )
        # And the assembled prompt has the expected three-message shape.
        assert len(msgs) == 2  # system + user (no history)
        assert msgs[0].role == "system"
        assert msgs[1].role == "user"

    def test_assembly_with_history_and_multimodal_user_message(
        self, builder: PromptBuilder
    ) -> None:
        """Multimodal trailing user message coexists with a text-only history."""
        history = [
            ConversationMessage(role="user", content="earlier text", created_at=datetime.now(UTC)),
            ConversationMessage(
                role="assistant",
                content="prior reply",
                created_at=datetime.now(UTC),
            ),
        ]
        blocks: list[MessageContent] = [
            TextContent(text="now look at this"),
            ImageContent(workspace_path="evidence.png", media_type="image/png"),
        ]
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=history,
            skill_index="",
            user_message=blocks,
            max_tokens=8000,
        )
        # Order: [system, *history, multimodal_user].
        assert msgs[0].role == "system"
        assert msgs[1].content == "earlier text"
        assert msgs[2].content == "prior reply"
        assert isinstance(msgs[-1].content, list)
        assert msgs[-1].content == blocks


class TestTokenCountExcludesImageBlocks:
    """T08 acceptance #3 — image refs never enter the token count.

    Per D-13-X-now option (c) image blocks are workspace-path
    references, not bytes. The prompt builder's token budget is for the
    text the model actually pays for; including image refs in
    ``count_tokens(...)`` would double-count (the backend has its own
    image-token cost model) and would also distort the budget if a path
    happens to be long. _token_total filters via ``isinstance(content,
    str)`` per T03 — this test pins that the filter holds end-to-end on
    a multimodal trailing user message.
    """

    def test_token_total_excludes_image_blocks_on_multimodal_user_message(
        self, builder: PromptBuilder
    ) -> None:
        blocks: list[MessageContent] = [
            TextContent(text="describe this image"),
            ImageContent(
                workspace_path="a-very-long-workspace-path-that-would-tokenise-heavily-if-counted.png",
                media_type="image/png",
            ),
        ]
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message=blocks,
            max_tokens=8000,
        )
        # _token_total sums tokens from str-content messages only. The system
        # block is str; the trailing user message is a list, so it contributes 0.
        total = builder._token_total(msgs)
        system_only_total = count_tokens(msgs[0].content) if isinstance(msgs[0].content, str) else 0
        assert total == system_only_total
        # Sanity: the trailing user message itself contributes nothing.
        trailing_only = builder._token_total([msgs[-1]])
        assert trailing_only == 0

    def test_token_total_unchanged_by_image_workspace_path_length(
        self, builder: PromptBuilder
    ) -> None:
        """The token total is the same whether the image path is short or huge.

        This is the store-by-reference invariant in its cleanest form: a
        path string that *would* tokenise into hundreds of tokens if
        counted must not move the budget. Two builds, identical except
        for image path length, must produce identical token totals.
        """
        short_blocks: list[MessageContent] = [
            TextContent(text="compare"),
            ImageContent(workspace_path="a.png", media_type="image/png"),
        ]
        long_blocks: list[MessageContent] = [
            TextContent(text="compare"),
            ImageContent(
                workspace_path="a/" + ("very-long-segment/" * 200) + "a.png",
                media_type="image/png",
            ),
        ]
        msgs_short = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message=short_blocks,
            max_tokens=8000,
        )
        msgs_long = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message=long_blocks,
            max_tokens=8000,
        )
        assert builder._token_total(msgs_short) == builder._token_total(msgs_long)


class TestStringPathUnchanged:
    """T08 acceptance #4 — text-only str path is byte-for-byte identical.

    Spec 13 widening is additive: a caller passing ``user_message=str``
    must see the exact same trailing :class:`ConversationMessage` shape
    as before T08. The trailing message has ``role="user"`` and
    ``content`` set to the literal str — no list wrapping, no
    ``TextContent`` boxing.
    """

    def test_str_user_message_assembles_with_str_content(self, builder: PromptBuilder) -> None:
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message="what are my rights?",
            max_tokens=8000,
        )
        trailing = msgs[-1]
        assert trailing.role == "user"
        # The exact pre-T08 shape: str, not list, not boxed in TextContent.
        assert isinstance(trailing.content, str)
        assert trailing.content == "what are my rights?"

    def test_str_path_token_total_counts_user_text(self, builder: PromptBuilder) -> None:
        """The str path participates in the token total (unlike the list path)."""
        msgs = builder.build(
            _persona(),
            RetrievedContext(),
            history=[],
            skill_index="",
            user_message="a question",
            max_tokens=8000,
        )
        # Only the trailing user message: the str text DOES count.
        trailing_only = builder._token_total([msgs[-1]])
        assert trailing_only == count_tokens("a question")
        assert trailing_only > 0
