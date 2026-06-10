"""Content-hash-only transcript audit per D-V2-X-transcript-content-policy.

Mirrors Spec 15's D-15-X-hard-line-filter content-hash-only-audit
privacy discipline. At v0.1 Open Persona NEVER persists raw transcript
text in :class:`persona_voice.logging.VoiceLog`, the operator-pass log,
the audit-event trail, or the credits-ledger surface. The audit surface
is **sha256(text) + event-shape + timing only**.

**Why content-hash only at v0.1.** Raw transcript audit is sensitive:

* Persona conversations are user-private by construction (single-user
  thesis project; persona schema fields like ``identity.values`` carry
  user-personal worldview information).
* Raw transcripts in the audit log surface PII / user-personal speech
  in any operator-shared observability backend, breaching the
  "user-private by default" contract.
* The cross-spec parallel: Spec 15 D-15-X-hard-line-filter persists
  ``prompt_sha256`` + ``category`` in audit events but NEVER the
  prompt text itself; Spec V2 inherits the same discipline.

**v0.2 candidate.** Operator-pass deploy decision to enable raw-transcript
audit MAY land at v0.2 gated on (a) operator privacy review +
(b) production debugging need + (c) explicit per-conversation opt-in
surface. The Protocol surface here is deliberately small so the v0.2
extension is a clean opt-in rather than a Protocol re-shape.

The audit helper interoperates with T08's :class:`VoiceLog` additive STT
fields: the hash function is called at the listener / writer boundary
producing the structured-context value that goes into observability
backends, while the raw transcript text flows through V2's in-memory
:meth:`StreamingSTT.transcripts` stream to V5 (where the persona model
consumes it for response generation) and then is dropped from memory
when V5's reply pipeline completes.

Hash algorithm + format:

* ``STT_AUDIT_HASH_ALG = "sha256"`` — fixed at v0.1; pinned to a
  module constant so the audit surface is grep-able + the operator can
  verify alignment across systems (e.g. Spec 15 also uses sha256).
* :func:`hash_transcript` returns full 64-char hex; used for collision-
  resistant audit records.
* :func:`hash_transcript_short` returns a 16-char hex prefix; used for
  compact log lines where the full hash would dominate the line.
  Operators can still reverse-lookup the full hash from the short prefix
  via the structured audit record.
"""

from __future__ import annotations

import hashlib

__all__ = [
    "STT_AUDIT_HASH_ALG",
    "hash_transcript",
    "hash_transcript_short",
]


# Audit hash algorithm identifier. Pinned at sha256 for v0.1 alignment with
# Spec 15 D-15-X-hard-line-filter (``prompt_sha256``). Any v0.2 change to
# this constant requires (a) operator coordination across Spec 15 + Spec V2
# audit consumers + (b) a migration path for historical hash lookups.
STT_AUDIT_HASH_ALG: str = "sha256"


def hash_transcript(text: str) -> str:
    """Return ``sha256(text.encode("utf-8")).hexdigest()`` for audit logging.

    Per D-V2-X-transcript-content-policy + Spec 15 D-15-X-hard-line-filter:
    at v0.1, raw transcript text NEVER persists in audit / VoiceLog. The
    audit surface is sha256(text) + event-shape + timing only.

    The function is intentionally a thin shim over :mod:`hashlib` so callers
    can rely on its deterministic behaviour without depending on
    :mod:`hashlib` directly (eases future migration paths). Unicode-safe
    via UTF-8 encoding — Norwegian "blåbær", Arabic "مرحبا", Swedish
    "hjälpa" all hash deterministically.

    Args:
        text: Raw transcript text.

    Returns:
        64-character lowercase hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_transcript_short(text: str, prefix_chars: int = 16) -> str:
    """Return the first ``prefix_chars`` of :func:`hash_transcript`.

    Compact prefix for log lines where the full 64-char hash would
    dominate. Operators reverse-lookup the full hash from the short
    prefix via the structured audit record (the full hash IS the
    canonical identifier; the prefix is convenience).

    Args:
        text: Raw transcript text.
        prefix_chars: Length of the returned prefix. Defaults to 16
            (~64-bit collision space, sufficient for in-conversation
            disambiguation; collisions across conversations resolve via
            the full hash in the structured audit record).

    Returns:
        Lowercase hex digest prefix of length ``prefix_chars``.
    """
    return hash_transcript(text)[:prefix_chars]
