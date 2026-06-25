"""verify_webhook_secret — the mandatory webhook security gate (Spec C2 T7, D-C2-2).

The three load-bearing properties: constant-time compare, a configured-and-matching
secret is required, and — the one that matters most — an UNSET secret fails closed
(rejects every request) rather than falling open.
"""

from __future__ import annotations

from persona_connectors.telegram.webhook import verify_webhook_secret
from pydantic import SecretStr


def test_matching_secret_is_accepted() -> None:
    assert verify_webhook_secret(SecretStr("s3cret"), "s3cret") is True


def test_wrong_secret_is_rejected() -> None:
    assert verify_webhook_secret(SecretStr("s3cret"), "guess") is False


def test_unset_secret_fails_closed() -> None:
    """The critical one: no configured secret → reject EVERY request (never fall open)."""
    assert verify_webhook_secret(None, "anything") is False
    assert verify_webhook_secret(None, None) is False


def test_absent_header_is_rejected() -> None:
    assert verify_webhook_secret(SecretStr("s3cret"), None) is False


def test_uses_constant_time_compare(monkeypatch: object) -> None:
    """The compare goes through hmac.compare_digest, not ``==`` (timing-safe)."""
    import persona_connectors.telegram.webhook as webhook_mod

    calls: list[tuple[str, str]] = []
    real = webhook_mod.hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(webhook_mod.hmac, "compare_digest", spy)  # type: ignore[attr-defined]
    assert verify_webhook_secret(SecretStr("s3cret"), "s3cret") is True
    assert calls == [("s3cret", "s3cret")]
