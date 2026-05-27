"""Integration tests for ``persona chat`` — spec §8 #6.

Verifies the REPL flow end-to-end against the EchoBackend + a real
ChromaBackend on tmp_path, and that episodic memory from session N is
retrievable in session N+1 (same ``PERSONA_CHROMA_PATH``).

To keep these fast we stub ``SentenceTransformerEmbedder`` with the
:class:`HashEmbedder` from ``tests._embedder`` via monkeypatching.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from persona.cli.main import app
from typer.testing import CliRunner

from tests._embedder import HashEmbedder

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "personas" / "valid"


@pytest.fixture(autouse=True)
def stub_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace SentenceTransformerEmbedder with the HashEmbedder for chat tests."""
    monkeypatch.setattr(
        "persona.cli.chat_cmd.SentenceTransformerEmbedder",
        lambda **_kwargs: HashEmbedder(),
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "PERSONA_BACKEND": "echo",
        "PERSONA_CHROMA_PATH": str(tmp_path / "chroma"),
        "PERSONA_AUDIT_PATH": str(tmp_path / "audit"),
    }


def test_chat_replies_via_echo_backend(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    result = runner.invoke(
        app,
        ["chat", str(FIXTURES / "01_minimal.yaml")],
        input="hello there\n\n",
    )
    assert result.exit_code == 0, result.stderr
    assert "I would say: hello there" in result.stdout


def test_episodic_memory_persists_across_sessions(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)

    persona_path = FIXTURES / "01_minimal.yaml"

    # Session N: write one turn.
    result1 = runner.invoke(app, ["chat", str(persona_path)], input="remember tenancy law\n\n")
    assert result1.exit_code == 0, result1.stderr

    # Session N+1: re-invoke. The same chroma path means session N's
    # episodic chunks should still be there.
    result2 = runner.invoke(app, ["chat", str(persona_path)], input="hi again\n\n")
    assert result2.exit_code == 0, result2.stderr

    # Read episodic store directly to verify both turns landed and survived.
    from persona.audit import MemoryAuditLogger
    from persona.stores import ChromaBackend, EpisodicStore

    backend = ChromaBackend(persist_path=tmp_path / "chroma", embedder=HashEmbedder())
    store = EpisodicStore(backend=backend, audit_logger=MemoryAuditLogger())
    chunks = store.get_all("01_minimal", include_superseded=True)
    assert len(chunks) >= 2, (
        f"expected >=2 episodic chunks across sessions, got {len(chunks)}: "
        f"{[c.text for c in chunks]}"
    )
    texts = " | ".join(c.text for c in chunks)
    assert "remember tenancy law" in texts
    assert "hi again" in texts
