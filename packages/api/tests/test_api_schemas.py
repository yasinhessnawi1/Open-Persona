"""Unit tests for the request/response schemas (spec 08, T06, D-08-3).

No DB. Cover the approved channel/format_hints change: ChannelContext present +
absent round-trips through the message request, the done event defaults
format_hints to {}, and unknown fields are rejected (extra='forbid').
"""

from __future__ import annotations

import pytest
from persona_api.schemas import (
    ChannelContext,
    DoneEvent,
    PostMessageRequest,
    ToolResultEvent,
)
from pydantic import ValidationError


def test_message_request_without_channel_is_the_web_ui_case() -> None:
    req = PostMessageRequest(content="hello")
    assert req.content == "hello"
    assert req.channel is None


def test_message_request_with_channel_round_trips() -> None:
    req = PostMessageRequest(
        content="hi",
        channel=ChannelContext(
            platform="telegram",
            platform_user_id="12345",
            platform_chat_id="67890",
            metadata={"k": "v"},
        ),
    )
    assert req.channel is not None
    assert req.channel.platform == "telegram"
    # round-trips through JSON (the wire form)
    restored = PostMessageRequest.model_validate_json(req.model_dump_json())
    assert restored.channel == req.channel


def test_channel_minimal_fields_default() -> None:
    ch = ChannelContext(platform="discord")
    assert ch.platform_user_id is None
    assert ch.platform_chat_id is None
    assert ch.metadata == {}


def test_message_request_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        PostMessageRequest(content="hi", bogus="x")  # type: ignore[call-arg]


def test_message_request_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        PostMessageRequest(content="")


def test_done_event_defaults_format_hints_empty() -> None:
    done = DoneEvent(tier="frontier")
    assert done.format_hints == {}
    assert done.usage == {}
    # serialises with format_hints present (the spec-12 echo channel)
    assert "format_hints" in done.model_dump_json()


def test_done_event_carries_format_hints_when_set() -> None:
    done = DoneEvent(
        tier="mid", usage={"prompt_tokens": 10}, format_hints={"parse_mode": "MarkdownV2"}
    )
    assert done.format_hints == {"parse_mode": "MarkdownV2"}


def test_done_event_routing_and_budget_default_none_backcompat() -> None:
    # Spec 31 (D-31-1/2): both additive fields default None ⇒ a rule-based turn
    # omits them; a pre-Spec-31 `done` dict still validates.
    done = DoneEvent(tier="frontier")
    assert done.routing is None
    assert done.budget is None


def test_done_event_carries_separate_routing_and_budget() -> None:
    from persona_api.schemas.responses import BudgetSnapshot, RoutingSummary

    done = DoneEvent(
        tier="frontier",
        routing=RoutingSummary(chosen_model="anthropic/good", dominant_factor="quality"),
        budget=BudgetSnapshot(session_spent_cents=1.5, max_cents_per_session=50.0),
    )
    assert done.routing is not None
    assert done.routing.chosen_model == "anthropic/good"
    assert done.routing.model_fallback_engaged is False  # defaulted
    assert done.budget is not None
    assert done.budget.session_spent_cents == 1.5
    assert done.budget.max_cents_per_turn is None  # unset cap
    # The raw score vector is not part of the wire summary.
    assert "score_vector" not in RoutingSummary.model_fields


def test_tool_result_event_uses_is_error_not_error_field() -> None:
    # D-03-3: ToolResult has is_error + content, no `error` field.
    ev = ToolResultEvent(tool="web_search", content="...results...", is_error=False)
    assert ev.is_error is False
    with pytest.raises(ValidationError):
        ToolResultEvent(tool="x", content="y", error="boom")  # type: ignore[call-arg]


# -- ConversationSummary last-message preview fields -------------------------


def test_conversation_summary_preview_fields_default_none() -> None:
    from datetime import UTC, datetime

    from persona_api.schemas import ConversationSummary

    now = datetime.now(UTC)
    summary = ConversationSummary(
        id="conv_1", persona_id="p1", title="t", created_at=now, updated_at=now
    )
    # A conversation with no messages: both fields default to None.
    assert summary.last_message_preview is None
    assert summary.last_message_role is None


def test_conversation_summary_accepts_preview_and_role() -> None:
    from datetime import UTC, datetime

    from persona_api.schemas import ConversationSummary

    now = datetime.now(UTC)
    summary = ConversationSummary(
        id="conv_1",
        persona_id="p1",
        title="t",
        created_at=now,
        updated_at=now,
        last_message_preview="Hello there!",
        last_message_role="assistant",
    )
    assert summary.last_message_preview == "Hello there!"
    assert summary.last_message_role == "assistant"


def test_conversation_summary_rejects_unknown_role() -> None:
    from datetime import UTC, datetime

    from persona_api.schemas import ConversationSummary

    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        ConversationSummary(
            id="c",
            persona_id="p",
            title="t",
            created_at=now,
            updated_at=now,
            last_message_role="robot",  # type: ignore[arg-type]
        )


# -- _truncate_preview (server-side preview trimming/truncation) -------------


def test_truncate_preview_passes_through_short_text() -> None:
    from persona_api.services.chat_service import _truncate_preview

    assert _truncate_preview("hello") == "hello"
    # surrounding whitespace is collapsed
    assert _truncate_preview("  hi there  ") == "hi there"


def test_truncate_preview_returns_none_for_no_message() -> None:
    from persona_api.services.chat_service import _truncate_preview

    assert _truncate_preview(None) is None


def test_truncate_preview_truncates_with_ellipsis() -> None:
    from persona_api.services.chat_service import LAST_MESSAGE_PREVIEW_MAX_LEN, _truncate_preview

    body = "x" * 500
    out = _truncate_preview(body)
    assert out is not None
    assert len(out) <= LAST_MESSAGE_PREVIEW_MAX_LEN
    assert out.endswith("…")
