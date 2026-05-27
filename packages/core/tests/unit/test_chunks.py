"""Tests for ``persona.schema.chunks``."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from persona.schema.chunks import (
    CHUNK_ID_INDEX_WIDTH,
    ChunkProvenance,
    PersonaChunk,
    WriteSource,
    make_chunk_id,
)
from pydantic import ValidationError

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


class TestWriteSource:
    def test_values_are_lowercase_strings(self) -> None:
        assert WriteSource.SYSTEM == "system"
        assert WriteSource.USER == "user"
        assert WriteSource.PERSONA_SELF == "persona_self"

    def test_is_str_subclass_for_json_interop(self) -> None:
        assert isinstance(WriteSource.SYSTEM, str)


class TestMakeChunkId:
    def test_canonical_format(self) -> None:
        assert make_chunk_id("astrid", "episodic", 7) == "astrid::episodic::0007"

    def test_index_is_zero_padded_to_four_digits(self) -> None:
        assert make_chunk_id("p", "k", 0) == "p::k::0000"
        assert make_chunk_id("p", "k", 9999) == "p::k::9999"

    def test_negative_index_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            make_chunk_id("p", "k", -1)

    def test_id_lexicographic_sort_matches_insertion_order(self) -> None:
        ids = [make_chunk_id("p", "episodic", i) for i in range(15)]
        assert sorted(ids) == ids

    def test_constant_matches_format(self) -> None:
        assert CHUNK_ID_INDEX_WIDTH == 4


class TestChunkProvenance:
    def test_minimal_provenance(self) -> None:
        prov = ChunkProvenance(
            source=WriteSource.USER,
            logical_id="astrid::self_facts::0001",
            written_at=UTC_NOW,
        )
        assert prov.version == 1
        assert prov.superseded_by is None
        assert prov.reason is None

    def test_naive_written_at_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive datetime"):
            ChunkProvenance(
                source=WriteSource.USER,
                logical_id="lid",
                written_at=datetime(2026, 5, 27, 12, 0, 0),  # noqa: DTZ001
            )

    def test_non_utc_offset_is_converted_to_utc(self) -> None:
        oslo = timezone(timedelta(hours=2))
        prov = ChunkProvenance(
            source=WriteSource.USER,
            logical_id="lid",
            written_at=datetime(2026, 5, 27, 14, 0, 0, tzinfo=oslo),
        )
        assert prov.written_at.tzinfo == UTC
        assert prov.written_at.hour == 12

    def test_version_must_be_at_least_one(self) -> None:
        with pytest.raises(ValidationError):
            ChunkProvenance(
                source=WriteSource.SYSTEM,
                logical_id="lid",
                version=0,
                written_at=UTC_NOW,
            )

    def test_frozen_rejects_mutation(self) -> None:
        prov = ChunkProvenance(source=WriteSource.USER, logical_id="lid", written_at=UTC_NOW)
        with pytest.raises(ValidationError):
            prov.source = WriteSource.SYSTEM  # type: ignore[misc]

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            ChunkProvenance(
                source=WriteSource.USER,
                logical_id="lid",
                written_at=UTC_NOW,
                bogus="x",  # type: ignore[call-arg]
            )


class TestPersonaChunk:
    def _provenance(self, *, version: int = 1) -> ChunkProvenance:
        return ChunkProvenance(
            source=WriteSource.USER,
            logical_id="astrid::self_facts::0001",
            version=version,
            written_at=UTC_NOW,
        )

    def test_minimal_chunk_construction(self) -> None:
        c = PersonaChunk(
            id="astrid::self_facts::0001",
            text="hello",
            created_at=UTC_NOW,
        )
        assert c.metadata == {}
        assert c.distance is None
        assert c.provenance is None
        assert c.content_hash

    def test_content_hash_is_deterministic(self) -> None:
        c1 = PersonaChunk(id="x", text="abc", metadata={"a": "1"}, created_at=UTC_NOW)
        c2 = PersonaChunk(id="x", text="abc", metadata={"a": "1"}, created_at=UTC_NOW)
        assert c1.content_hash == c2.content_hash

    def test_content_hash_independent_of_metadata_key_order(self) -> None:
        c1 = PersonaChunk(id="x", text="abc", metadata={"a": "1", "b": "2"}, created_at=UTC_NOW)
        c2 = PersonaChunk(id="x", text="abc", metadata={"b": "2", "a": "1"}, created_at=UTC_NOW)
        assert c1.content_hash == c2.content_hash

    def test_content_hash_changes_with_text(self) -> None:
        c1 = PersonaChunk(id="x", text="abc", created_at=UTC_NOW)
        c2 = PersonaChunk(id="x", text="abd", created_at=UTC_NOW)
        assert c1.content_hash != c2.content_hash

    def test_content_hash_changes_with_metadata(self) -> None:
        c1 = PersonaChunk(id="x", text="t", metadata={"a": "1"}, created_at=UTC_NOW)
        c2 = PersonaChunk(id="x", text="t", metadata={"a": "2"}, created_at=UTC_NOW)
        assert c1.content_hash != c2.content_hash

    def test_supplied_content_hash_must_match(self) -> None:
        good_hash = hashlib.sha256(b"abc\x00[]").hexdigest()
        c = PersonaChunk(id="x", text="abc", content_hash=good_hash, created_at=UTC_NOW)
        assert c.content_hash == good_hash

        with pytest.raises(ValidationError, match="content_hash mismatch"):
            PersonaChunk(
                id="x",
                text="abc",
                content_hash="0" * 64,
                created_at=UTC_NOW,
            )

    def test_naive_created_at_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive datetime"):
            PersonaChunk(
                id="x",
                text="t",
                created_at=datetime(2026, 5, 27, 12, 0, 0),  # noqa: DTZ001
            )

    def test_frozen_rejects_mutation(self) -> None:
        c = PersonaChunk(id="x", text="t", created_at=UTC_NOW)
        with pytest.raises(ValidationError):
            c.text = "y"  # type: ignore[misc]

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            PersonaChunk(
                id="x",
                text="t",
                created_at=UTC_NOW,
                unknown="x",  # type: ignore[call-arg]
            )

    def test_chunk_with_provenance_serialises_round_trip(self) -> None:
        c = PersonaChunk(
            id="astrid::self_facts::0001",
            text="confidently held",
            metadata={"confidence": "0.9"},
            provenance=self._provenance(version=2),
            created_at=UTC_NOW,
        )
        dumped = c.model_dump_json()
        restored = PersonaChunk.model_validate_json(dumped)
        assert restored == c
        assert restored.provenance is not None
        assert restored.provenance.version == 2
