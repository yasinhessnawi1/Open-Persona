"""Unit tests for ``persona_voice.stt.audit`` — D-V2-X-transcript-content-policy.

Mirrors Spec 15 D-15-X-hard-line-filter content-hash-only-audit privacy
discipline. At v0.1 Open Persona NEVER persists raw transcript text in
the audit surface; only ``sha256(text)`` + event-shape + timing.
"""

from __future__ import annotations

from persona_voice.stt.audit import (
    STT_AUDIT_HASH_ALG,
    hash_transcript,
    hash_transcript_short,
)


def test_audit_hash_alg_pinned_to_sha256() -> None:
    """D-V2-X-transcript-content-policy — algorithm identifier is stable."""
    assert STT_AUDIT_HASH_ALG == "sha256"


def test_hash_transcript_returns_64_char_hex() -> None:
    """SHA-256 hex digest is always 64 characters."""
    digest = hash_transcript("hello")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_transcript_is_deterministic() -> None:
    """Same input → same hash (audit lookups depend on this)."""
    assert hash_transcript("hello world") == hash_transcript("hello world")


def test_hash_transcript_distinguishes_different_inputs() -> None:
    """Different inputs → different hashes (collision-resistant)."""
    assert hash_transcript("hello") != hash_transcript("world")
    assert hash_transcript("foo") != hash_transcript("Foo")  # case-sensitive


def test_hash_transcript_matches_known_sha256_value() -> None:
    """Pin against the published SHA-256 hex for the empty string."""
    empty_hex = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert hash_transcript("") == empty_hex


def test_hash_transcript_handles_unicode_norwegian() -> None:
    """Norwegian 'blåbær' UTF-8 encodes cleanly."""
    digest = hash_transcript("blåbær")
    assert len(digest) == 64
    # Deterministic — same value across runs.
    assert digest == hash_transcript("blåbær")


def test_hash_transcript_handles_unicode_arabic() -> None:
    """Arabic 'مرحبا' UTF-8 encodes cleanly."""
    digest = hash_transcript("مرحبا")
    assert len(digest) == 64
    assert digest == hash_transcript("مرحبا")


def test_hash_transcript_handles_unicode_swedish() -> None:
    """Swedish 'hjälpa' UTF-8 encodes cleanly."""
    digest = hash_transcript("hjälpa")
    assert len(digest) == 64
    assert digest == hash_transcript("hjälpa")


def test_hash_transcript_short_default_prefix_is_16_chars() -> None:
    """Compact-log convenience: 16-char prefix by default."""
    prefix = hash_transcript_short("hello")
    assert len(prefix) == 16
    assert all(c in "0123456789abcdef" for c in prefix)


def test_hash_transcript_short_prefix_matches_full_hash_prefix() -> None:
    """The short prefix is exactly the start of the full hash (no rehashing)."""
    full = hash_transcript("hello world")
    short = hash_transcript_short("hello world")
    assert full.startswith(short)


def test_hash_transcript_short_custom_prefix_length() -> None:
    """Operators can request a shorter or longer prefix."""
    assert len(hash_transcript_short("hello", prefix_chars=8)) == 8
    assert len(hash_transcript_short("hello", prefix_chars=32)) == 32
    assert len(hash_transcript_short("hello", prefix_chars=64)) == 64
