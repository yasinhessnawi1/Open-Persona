"""Spec P3 (P3-D-3b) — the API-side half of the anti-drift contract.

P3 reconstructs the interleaved chat view from the persisted ordered log
(``messages.stream_events``). That log is a hybrid: text deltas the worker appends
as ``{"kind": "text", "delta": ...}``, interleaved with ``RunEvent`` dumps
(``event.model_dump(mode="json")`` → ``{type, step, data, timestamp}``). The
frontend ``toChatEvent`` + ``reduceChatEvent`` read **specific keys** out of each
entry's ``data`` payload.

The frontend live-equivalence test proves the *frontend* doesn't fork. This test
is the other half: it pins the **emitted persisted-envelope shape** at the source
(the ``RunEvent`` constructors the worker dumps) against exactly the keys the
frontend reducer consumes. If the API renames/moves a payload key, this fails —
catching cross-layer drift before reconstruction silently rots.

The expected key sets below mirror, BY HAND (the D-09-1 hand-mirror discipline —
OpenAPI can't model the SSE/log payloads), the frontend contract in
``packages/web/src/lib/sse-types.ts`` (``ToolCallingData`` / ``ToolResultData`` /
``ArtifactRef`` / ``ActivityStartData`` / ``ActivityEndData`` / ``AskingUserData`` /
``MemoryRecallData``) and ``packages/web/src/lib/chat/reduce-chat-event.ts``
(``toChatEvent`` + ``reduceChatEvent``). Keep them in sync — that IS the contract.
"""

from __future__ import annotations

from persona.schema.tools import PersistedArtifact, ToolCall, ToolResult
from persona_runtime.agentic.events import RunEvent
from persona_runtime.questions import QuestionOption

# The persisted RunEvent dump envelope the worker writes (chat_turn_worker._on_event:
# `handle.event_log.append(event.model_dump(mode="json"))`). `toChatEvent` maps it to
# `{event: dump["type"], data: dump["data"]}` (step/timestamp are dropped on read).
ENVELOPE_KEYS = {"type", "step", "data", "timestamp"}


def _dump(event: RunEvent) -> dict[str, object]:
    """Exactly what the worker persists per RunEvent."""
    return event.model_dump(mode="json")


def test_envelope_shape_is_type_step_data_timestamp() -> None:
    """Every persisted RunEvent dump is the 4-key envelope `toChatEvent` expects."""
    dump = _dump(RunEvent.tool_calling(-1, [ToolCall(name="web_search", call_id="c1")]))
    assert set(dump) == ENVELOPE_KEYS
    assert dump["type"] == "tool_calling"


def test_text_delta_entry_shape() -> None:
    """The worker appends text deltas as `{kind, delta}` (NOT a RunEvent). `toChatEvent`
    maps this to a `chunk` event whose `data.delta` the reducer concatenates."""
    # Mirror of chat_turn_worker: `handle.event_log.append({"kind": "text", "delta": ...})`.
    entry = {"kind": "text", "delta": "Hello "}
    assert set(entry) == {"kind", "delta"}
    assert entry["kind"] == "text"
    assert isinstance(entry["delta"], str)


def test_tool_calling_payload_keys_match_frontend() -> None:
    """`data` = {tool_names, tool_calls:[{name, call_id, args, kind?}]} — the keys
    `reduceChatEvent`'s tool_calling arm reads (c.name / c.call_id / c.args / c.kind)."""
    dump = _dump(
        RunEvent.tool_calling(
            0,
            [ToolCall(name="web_search", call_id="c1", args={"q": "x"})],
            kind_of=lambda _name: "builtin",
        )
    )
    data = dump["data"]
    assert isinstance(data, dict)
    assert set(data) == {"tool_names", "tool_calls"}
    call = data["tool_calls"][0]
    assert set(call) == {"name", "call_id", "args", "kind"}
    assert call == {"name": "web_search", "call_id": "c1", "args": {"q": "x"}, "kind": "builtin"}


def test_tool_calling_without_kind_is_backcompat_shape() -> None:
    """No `kind_of` ⇒ the call dict omits `kind` (the additive back-compat default the
    frontend treats as an optional field)."""
    dump = _dump(RunEvent.tool_calling(0, [ToolCall(name="file_read", call_id="c2")]))
    call = dump["data"]["tool_calls"][0]
    assert "kind" not in call
    assert set(call) == {"name", "call_id", "args"}


def test_tool_result_payload_keys_match_frontend() -> None:
    """`data` = {tool_name, is_error, content, kind?, produced_files?, artifacts?} — the
    keys `reduceChatEvent`'s tool_result arm reads; artifacts are the FileCard ref path."""
    result = ToolResult(
        tool_name="web_search",
        content="results",
        is_error=False,
        artifacts=(
            PersistedArtifact(
                workspace_path="uploads/a.png",
                mime_type="image/png",
                size_bytes=10,
                rendered_inline=True,
            ),
        ),
    )
    data = _dump(RunEvent.tool_result(0, "web_search", result, kind="builtin"))["data"]
    assert set(data) == {"tool_name", "is_error", "content", "kind", "artifacts"}
    assert data["tool_name"] == "web_search"
    assert data["is_error"] is False
    # NB the artifact ref shape MUST match the frontend `ArtifactRef` (mime_type, NOT
    # media_type; no `name` — the web normaliser derives the display name).
    assert set(data["artifacts"][0]) == {
        "workspace_path",
        "mime_type",
        "size_bytes",
        "rendered_inline",
    }
    assert data["artifacts"][0]["workspace_path"] == "uploads/a.png"


def test_tool_result_produced_files_key_present_when_set() -> None:
    """`produced_files` rides `ToolResult.data["produced_files"]` (the F4 fallback path
    the frontend reads when artifacts are absent)."""
    result = ToolResult(
        tool_name="code_execution",
        content="ran",
        is_error=False,
        data={"produced_files": [{"path": "out.txt", "size_bytes": 3, "media_type": "text/plain"}]},
    )
    data = _dump(RunEvent.tool_result(0, "code_execution", result))["data"]
    assert "produced_files" in data
    assert data["produced_files"][0]["path"] == "out.txt"


def test_tool_result_omits_empty_artifacts_and_produced_files() -> None:
    """Absence IS the back-compat shape — a bare result carries neither key (the
    frontend treats absence as 'no files')."""
    bare = ToolResult(tool_name="web_search", content="x", is_error=False)
    data = _dump(RunEvent.tool_result(0, "web_search", bare))["data"]
    assert "artifacts" not in data
    assert "produced_files" not in data
    assert set(data) == {"tool_name", "is_error", "content"}


def test_activity_start_end_payload_keys_match_frontend() -> None:
    """P2 activity contract — the keys the frontend `reduceActivityStart/End` read."""
    start = _dump(
        RunEvent.activity_start(
            0,
            activity_id="a1",
            kind="web",
            name="web_search",
            label="Searching the web",
            args_summary={"q": "x"},
        )
    )["data"]
    assert set(start) == {"activity_id", "kind", "name", "label", "args_summary"}

    end = _dump(
        RunEvent.activity_end(0, activity_id="a1", status="ok", duration_ms=12.0, is_error=False)
    )["data"]
    assert set(end) == {"activity_id", "status", "duration_ms", "is_error"}


def test_asking_user_and_memory_recall_payload_keys() -> None:
    """The chat-rail + recall payloads the frontend reducer reads."""
    plain = _dump(RunEvent.asking_user(0, "continue?"))["data"]
    assert set(plain) == {"question"}  # bare back-compat ask

    rich = _dump(
        RunEvent.asking_user(0, "pick", options=[QuestionOption(label="A")], allow_free_form=False)
    )["data"]
    assert set(rich) == {"question", "options", "allow_free_form"}
    assert set(rich["options"][0]) == {"label", "description"}

    recall = _dump(RunEvent.memory_recall(0, "episodic", count=3))["data"]
    assert set(recall) == {"store", "count"}


def test_tier_is_not_persisted_in_the_log() -> None:
    """The `tier` event rides the `tier_used` column (P3-D-6), NOT the persisted log —
    and `routing`/`budget` ride only the live `done` payload (documented degradation).
    The reducer's `done` arm never sees a persisted entry; the log carries no `tier`/
    `done`. This pins that contract so a future change that starts logging tier (and
    would need a frontend handler) fails loudly here."""
    # `tier` IS a constructible RunEvent, but the worker filters it out of event_log
    # (chat_turn_worker._on_event: `if event.type == "tier": ... return`). This test
    # documents the seam; the worker-filter itself is covered by the worker's own tests.
    tier_dump = _dump(RunEvent.tier("mid"))
    assert tier_dump["type"] == "tier"  # constructible…
    # …but `done` (which carries routing/budget) is an API-layer SSE frame, never a
    # RunEvent — so it can never appear in the RunEvent-dump log. (No assertion needed
    # beyond documenting it: there is no RunEvent.done constructor.)
    assert not hasattr(RunEvent, "done")
