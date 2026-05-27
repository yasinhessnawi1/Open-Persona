"""Integration tests for :class:`PersonaRegistry`.

Uses a real ChromaBackend on tmp_path (mark: integration). Reuses the
:class:`HashEmbedder` from ``test_stores_chroma`` so we don't load
sentence-transformers.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime by tmp_path fixture

import pytest
from persona.audit import MemoryAuditLogger
from persona.errors import StoreNotFoundError
from persona.registry import PersonaRegistry
from persona.stores import (
    ChromaBackend,
    EpisodicStore,
    IdentityStore,
    SelfFactsStore,
    WorldviewStore,
)
from persona.stores.base import TypedStore  # noqa: TC002 — runtime use in fixture

from tests._embedder import HashEmbedder

pytestmark = pytest.mark.integration


VALID_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "personas" / "valid"


def _build_stores(backend: ChromaBackend, audit: MemoryAuditLogger) -> dict[str, TypedStore]:
    return {
        "identity": IdentityStore(backend=backend, audit_logger=audit),
        "self_facts": SelfFactsStore(backend=backend, audit_logger=audit),
        "worldview": WorldviewStore(backend=backend, audit_logger=audit),
        "episodic": EpisodicStore(backend=backend, audit_logger=audit),
    }


@pytest.fixture
def chroma_backend(tmp_path: Path) -> ChromaBackend:
    return ChromaBackend(persist_path=tmp_path / "chroma", embedder=HashEmbedder())


@pytest.fixture
def audit() -> MemoryAuditLogger:
    return MemoryAuditLogger()


class TestRegistryConstructor:
    def test_missing_store_kind_rejected(
        self, chroma_backend: ChromaBackend, audit: MemoryAuditLogger
    ) -> None:
        stores = _build_stores(chroma_backend, audit)
        del stores["episodic"]
        with pytest.raises(StoreNotFoundError, match="missing"):
            PersonaRegistry(stores=stores, audit_logger=audit)


class TestRegistryLoad:
    def test_load_legal_assistant_fixture(
        self, chroma_backend: ChromaBackend, audit: MemoryAuditLogger
    ) -> None:
        registry = PersonaRegistry(stores=_build_stores(chroma_backend, audit), audit_logger=audit)
        persona = registry.load(VALID_FIXTURES / "03_legal_assistant_full.yaml")
        assert persona.persona_id == "legal_assistant_no"
        assert persona.identity.name == "Astrid"

        stores = _build_stores(chroma_backend, audit)
        identity = stores["identity"].get_all(persona.persona_id)
        self_facts = stores["self_facts"].get_all(persona.persona_id)
        worldview = stores["worldview"].get_all(persona.persona_id)

        # Identity: 4 fixed fields (name, role, background, language_default) +
        # each constraint sentence.
        assert len(identity) == 4 + len(persona.identity.constraints)
        assert len(self_facts) == len(persona.self_facts)
        assert len(worldview) == len(persona.worldview)

    def test_load_is_idempotent(
        self, chroma_backend: ChromaBackend, audit: MemoryAuditLogger
    ) -> None:
        registry = PersonaRegistry(stores=_build_stores(chroma_backend, audit), audit_logger=audit)
        registry.load(VALID_FIXTURES / "05_writing_coach.yaml")
        first_self_facts = len(
            _build_stores(chroma_backend, audit)["self_facts"].get_all(
                "writing_coach", include_superseded=True
            )
        )
        registry.load(VALID_FIXTURES / "05_writing_coach.yaml")
        second_self_facts = len(
            _build_stores(chroma_backend, audit)["self_facts"].get_all(
                "writing_coach", include_superseded=True
            )
        )
        assert first_self_facts == second_self_facts

    def test_load_indexes_episodic_entries_from_yaml(
        self, chroma_backend: ChromaBackend, audit: MemoryAuditLogger
    ) -> None:
        stores = _build_stores(chroma_backend, audit)
        registry = PersonaRegistry(stores=stores, audit_logger=audit)
        persona = registry.load(VALID_FIXTURES / "07_with_episodic.yaml")
        assert persona.persona_id == "memoryful"
        episodic = stores["episodic"].get_all(persona.persona_id)
        assert len(episodic) == len(persona.episodic)

    def test_minimal_persona_loads_with_empty_collections(
        self, chroma_backend: ChromaBackend, audit: MemoryAuditLogger
    ) -> None:
        stores = _build_stores(chroma_backend, audit)
        registry = PersonaRegistry(stores=stores, audit_logger=audit)
        persona = registry.load(VALID_FIXTURES / "01_minimal.yaml")
        # Identity is non-empty (4 base fields), the others are empty.
        assert len(stores["identity"].get_all(persona.persona_id or "")) == 4
        assert stores["self_facts"].get_all(persona.persona_id or "") == []
        assert stores["worldview"].get_all(persona.persona_id or "") == []
        assert stores["episodic"].get_all(persona.persona_id or "") == []
