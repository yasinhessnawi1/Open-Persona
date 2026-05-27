"""``persona chat <path>`` — REPL loop against a placeholder backend.

Spec 02 replaces the :class:`EchoBackend` with a real model backend; this
command's structure stays the same. Episodic memory persists across REPL
sessions because the registry indexes through a stable ``PERSONA_CHROMA_PATH``.
"""
# ruff: noqa: B008 — typer.Argument/Option in defaults is the framework idiom

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 — typer needs runtime access
from typing import TYPE_CHECKING

import typer

from persona.audit import JSONLAuditLogger
from persona.cli._echo import ChatBackendStub, EchoBackend
from persona.config import PersonaCoreConfig
from persona.history import ConversationHistoryManager
from persona.logging import get_logger
from persona.registry import PersonaRegistry
from persona.schema.chunks import ChunkProvenance, PersonaChunk, WriteSource, make_chunk_id
from persona.schema.conversation import Conversation, ConversationMessage
from persona.stores import (
    ChromaBackend,
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    SentenceTransformerEmbedder,
    WorldviewStore,
)

if TYPE_CHECKING:
    from persona.stores.base import TypedStore

_log = get_logger("cli.chat")

__all__ = ["chat"]


def chat(
    persona_path: Path = typer.Argument(..., help="Path to a persona YAML."),
) -> None:
    """Start a REPL chat with the persona at ``persona_path``."""
    config = PersonaCoreConfig()
    backend = _build_backend(config)

    embedder = SentenceTransformerEmbedder(model_name="BAAI/bge-small-en-v1.5")
    chroma = ChromaBackend(persist_path=config.chroma_path, embedder=embedder)
    audit_root = config.audit_path or (config.chroma_path / "audit")
    audit_logger = JSONLAuditLogger(audit_root)

    stores: dict[str, TypedStore] = {
        "identity": IdentityStore(backend=chroma, audit_logger=audit_logger),
        "self_facts": SelfFactsStore(backend=chroma, audit_logger=audit_logger),
        "worldview": WorldviewStore(backend=chroma, audit_logger=audit_logger),
        "episodic": EpisodicStore(backend=chroma, audit_logger=audit_logger),
    }
    registry = PersonaRegistry(stores=stores, audit_logger=audit_logger)
    persona = registry.load(persona_path)
    persona_id = persona.persona_id or persona_path.stem

    typer.echo(f"Loaded {persona.identity.name} ({persona_id}).")
    typer.echo(f"Backend: {getattr(backend, 'name', type(backend).__name__)}")
    typer.echo("Type a message and press Enter. Empty line to exit.\n")

    history_manager = ConversationHistoryManager()
    conversation = Conversation(
        conversation_id=f"cli-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
        persona_id=persona_id,
    )
    episodic_store = stores["episodic"]
    turn_index = _next_episodic_index(episodic_store, persona_id)

    while True:
        try:
            user_input = typer.prompt("you", default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            typer.echo("\ngoodbye")
            return
        if not user_input.strip():
            typer.echo("goodbye")
            return

        user_msg = ConversationMessage(
            role="user", content=user_input, created_at=datetime.now(UTC)
        )
        conversation.messages.append(user_msg)

        prompt_messages = history_manager.manage(conversation, _no_op_summariser)
        reply_text = backend.generate(prompt_messages)
        assistant_msg = ConversationMessage(
            role="assistant", content=reply_text, created_at=datetime.now(UTC)
        )
        conversation.messages.append(assistant_msg)
        typer.echo(f"{persona.identity.name}: {reply_text}")

        # Write the exchange to episodic memory as one chunk.
        _write_turn_to_episodic(
            episodic_store,
            persona_id=persona_id,
            index=turn_index,
            user_text=user_input,
            assistant_text=reply_text,
        )
        turn_index += 1


def _build_backend(config: PersonaCoreConfig) -> ChatBackendStub:
    """Return the configured backend or the echo placeholder."""
    # In v0.1 only the echo backend exists. Spec 02 routes via PERSONA_BACKEND.
    if config.backend not in {"echo", "anthropic", "openai", "deepseek", "groq", "ollama"}:
        typer.echo(
            f"unknown PERSONA_BACKEND={config.backend!r}; falling back to echo",
            err=True,
        )
    if config.backend != "echo":
        typer.echo(
            "Note: no real model backends are wired yet (spec 02 ships them). "
            "Using EchoBackend; set PERSONA_BACKEND=echo to suppress this notice.",
            err=True,
        )
    return EchoBackend()


def _no_op_summariser(messages: list[ConversationMessage]) -> str:
    """Concatenate role+content; the small-tier model summariser lands in spec 05."""
    return " | ".join(f"{m.role}: {m.content[:60]}" for m in messages)


def _next_episodic_index(store: TypedStore, persona_id: str) -> int:
    """Compute the next 4-digit index for episodic chunk IDs."""
    existing = store.get_all(persona_id, include_superseded=True)
    return len(existing)


def _write_turn_to_episodic(
    store: TypedStore,
    *,
    persona_id: str,
    index: int,
    user_text: str,
    assistant_text: str,
) -> None:
    chunk_id = make_chunk_id(persona_id, "episodic", index)
    text = f"USER: {user_text}\nASSISTANT: {assistant_text}"
    now = datetime.now(UTC)
    store.write(
        persona_id,
        [
            PersonaChunk(
                id=chunk_id,
                text=text,
                metadata={"importance": "0.5"},
                created_at=now,
                provenance=ChunkProvenance(
                    source=WriteSource.SYSTEM,
                    logical_id=chunk_id,
                    version=1,
                    written_at=now,
                    written_by="cli.chat",
                ),
            ),
        ],
        source=WriteSource.SYSTEM,
        written_by="cli.chat",
    )
