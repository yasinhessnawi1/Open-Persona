"""Spec 13 T03 tests for the widened ``ConversationMessage.content`` field.

This file is the structural guard around the additive Pydantic widening
of ``content: str`` -> ``content: str | list[MessageContent]``. The
headline test is :class:`TestPhase1RegressionByteForByte` — it replays
the T01 snapshot corpus and asserts every captured Phase 1 dump is
byte-identical under the widening. If any entry diverges, the widening
is *not* additive and the source needs to be fixed (do NOT modify the
fixture to make the test pass).

The remaining tests cover the new shape:

* :class:`TestSingleTextAsListForbidden` — the structural guard that
  text-only messages must stay ``content=str``.
* :class:`TestMultimodalContentConstructs` — list form with mixed
  text + image references constructs cleanly and dumps to a list of
  tagged dicts.
* :class:`TestMultimodalThenSerialise` — JSON round-trip on a multimodal
  message is identity.

The existing Phase 1 tests at
``packages/core/tests/unit/test_schema_conversation.py`` are preserved
untouched and continue to pass — that file covers the per-field
validation (role literals, naive datetime rejection, frozen/extra,
``Conversation`` bookkeeping). This file covers the Spec 13 additions
only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from persona.schema.content import ImageContent, TextContent
from persona.schema.conversation import ConversationMessage
from pydantic import ValidationError

UTC_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)

_SNAPSHOT_PATH = Path(__file__).parent / "_conversation_message_snapshots.json"


def _load_snapshot_corpus() -> list[dict[str, Any]]:
    """Load the T01-captured byte-for-byte regression corpus.

    Returns the list as written to disk by the T01 capture run; each
    entry has ``source`` (origin grep site), ``kwargs`` (constructor
    inputs in JSON-friendly form, with ``created_at`` as an ISO string)
    and ``dump`` (the captured ``model_dump(mode="json")`` shape).
    """
    with _SNAPSHOT_PATH.open(encoding="utf-8") as f:
        data: list[dict[str, Any]] = json.load(f)
    return data


def _kwargs_from_snapshot(raw_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Rehydrate ``created_at`` from ISO string back to a tz-aware datetime.

    The other kwargs (``role``, ``content``, ``metadata``, ``tool_calls``)
    are JSON-native and pass through unchanged — Pydantic v2 will validate
    ``tool_calls`` dict entries into :class:`ToolCall` instances on the
    way in.
    """
    kwargs = dict(raw_kwargs)
    kwargs["created_at"] = datetime.fromisoformat(kwargs["created_at"])
    return kwargs


_CORPUS = _load_snapshot_corpus()


class TestPhase1RegressionByteForByte:
    """The load-bearing structural guard for Spec 13's additive widening.

    Each captured entry must replay to a byte-identical
    ``model_dump(mode="json")``. Any divergence is a Phase 1 contract
    break — the test fails loudly so we catch it before any reader site
    runs into the divergence in real traffic.
    """

    @pytest.mark.parametrize(
        ("entry"),
        _CORPUS,
        ids=[f"{i:02d}-{entry['source']}" for i, entry in enumerate(_CORPUS)],
    )
    def test_snapshot_dump_matches_byte_for_byte(self, entry: dict[str, Any]) -> None:
        kwargs = _kwargs_from_snapshot(entry["kwargs"])
        msg = ConversationMessage(**kwargs)
        dumped = msg.model_dump(mode="json")
        assert dumped == entry["dump"], (
            f"Phase 1 regression: dump for {entry['source']} diverged under "
            f"the Spec 13 widening.\n  expected: {entry['dump']}\n  actual:   {dumped}"
        )

    def test_corpus_is_non_empty(self) -> None:
        """Sanity: T01 captured at least the spec's >=10-entry target."""
        assert len(_CORPUS) >= 10


class TestSingleTextAsListForbidden:
    """Text-only messages must stay ``content=str``.

    A list of length 1 holding only a :class:`TextContent` is the one
    multimodal shape that could silently diverge from the Phase 1 str
    form on the wire. We reject it at validation time so the str-vs-list
    boundary is unambiguous.
    """

    def test_single_text_block_in_list_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ConversationMessage(
                role="user",
                content=[TextContent(text="hi")],
                created_at=UTC_NOW,
            )
        assert "single-text-as-list" in str(excinfo.value)

    def test_single_text_block_in_list_rejected_for_all_roles(self) -> None:
        """The guard fires regardless of role — system/assistant/tool too."""
        for role in ("user", "assistant", "system", "tool"):
            with pytest.raises(ValidationError, match="single-text-as-list"):
                ConversationMessage(
                    role=role,  # type: ignore[arg-type]
                    content=[TextContent(text="x")],
                    created_at=UTC_NOW,
                )

    def test_two_text_blocks_in_list_accepted(self) -> None:
        """Two text blocks is genuinely multi-block content; allowed."""
        msg = ConversationMessage(
            role="user",
            content=[TextContent(text="hello"), TextContent(text="world")],
            created_at=UTC_NOW,
        )
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2

    def test_str_content_still_works(self) -> None:
        """The Phase 1 str form is the canonical shape — must keep working."""
        msg = ConversationMessage(role="user", content="hello", created_at=UTC_NOW)
        assert msg.content == "hello"


class TestMultimodalContentConstructs:
    """List form with mixed text + image references constructs cleanly."""

    def test_text_plus_image_constructs(self) -> None:
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="look at this"),
                ImageContent(workspace_path="abc/img.png", media_type="image/png"),
            ],
            created_at=UTC_NOW,
        )
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2
        assert isinstance(msg.content[0], TextContent)
        assert isinstance(msg.content[1], ImageContent)

    def test_text_plus_image_dump_shape(self) -> None:
        """model_dump returns a list of tagged dicts — discriminator preserved."""
        msg = ConversationMessage(
            role="user",
            content=[
                TextContent(text="look at this"),
                ImageContent(workspace_path="abc/img.png", media_type="image/png"),
            ],
            created_at=UTC_NOW,
        )
        dumped = msg.model_dump(mode="json")
        assert dumped["content"] == [
            {"type": "text", "text": "look at this"},
            {
                "type": "image",
                "workspace_path": "abc/img.png",
                "media_type": "image/png",
            },
        ]
        # The rest of the shape is unchanged from Phase 1.
        assert dumped["role"] == "user"
        assert dumped["metadata"] == {}
        assert dumped["tool_calls"] == []

    def test_image_only_list_constructs(self) -> None:
        """A single image block is a genuine multimodal turn — accepted."""
        msg = ConversationMessage(
            role="user",
            content=[ImageContent(workspace_path="x.png", media_type="image/png")],
            created_at=UTC_NOW,
        )
        assert isinstance(msg.content, list)
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], ImageContent)


class TestMultimodalThenSerialise:
    """JSON round-trip on a multimodal message is identity."""

    def test_round_trip_preserves_blocks(self) -> None:
        original = ConversationMessage(
            role="user",
            content=[
                TextContent(text="look at this"),
                ImageContent(workspace_path="abc/img.png", media_type="image/png"),
            ],
            created_at=UTC_NOW,
        )
        raw = original.model_dump_json()
        restored = ConversationMessage.model_validate_json(raw)
        assert restored == original
        assert isinstance(restored.content, list)
        assert isinstance(restored.content[0], TextContent)
        assert isinstance(restored.content[1], ImageContent)

    def test_round_trip_preserves_all_supported_media_types(self) -> None:
        """All four D-13-3 media types survive the round-trip."""
        for media_type in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            original = ConversationMessage(
                role="user",
                content=[
                    TextContent(text="caption"),
                    ImageContent(workspace_path=f"x.{media_type[-3:]}", media_type=media_type),  # type: ignore[arg-type]
                ],
                created_at=UTC_NOW,
            )
            restored = ConversationMessage.model_validate_json(original.model_dump_json())
            assert restored == original

    def test_round_trip_str_content_unchanged(self) -> None:
        """Sanity: the Phase 1 str form also round-trips identically."""
        original = ConversationMessage(role="user", content="hello", created_at=UTC_NOW)
        restored = ConversationMessage.model_validate_json(original.model_dump_json())
        assert restored == original
        assert restored.content == "hello"
