"""Credential encryption-at-rest for bring-your-own MCP servers (spec 30 T07, D-30-4).

User-supplied MCP-server credentials (a bearer token, a header value) are stored
encrypted, **never** in plaintext and **never** logged. We use Fernet
(AES-128-CBC + HMAC-SHA256, authenticated, timestamped) via ``MultiFernet`` so
key rotation is zero-downtime: the FIRST key encrypts, ALL keys decrypt — rotate
by prepending a fresh key and lazily/batch re-encrypting (procedure in
MAINTENANCE.md). Plain `cryptography`; hazmat AES-GCM rejected (nonce footgun).

The key set comes from ``MCP_CREDENTIAL_KEY`` (one key, or comma-separated for
rotation). If it is unset, :func:`cipher_from_config` returns ``None`` and the
caller fails loud (:class:`~persona_api.errors.MCPCredentialError`) rather than
persist a secret in the clear — a credential is never stored without a key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from persona_api.errors import MCPCredentialError

if TYPE_CHECKING:
    from persona_api.config import APIConfig

__all__ = ["CredentialCipher", "cipher_from_config"]


class CredentialCipher:
    """Encrypt/decrypt short secret strings with a rotatable Fernet key set.

    Args:
        keys: One or more url-safe-base64 Fernet keys. The first encrypts; all
            decrypt (rotation). Must be non-empty.

    Raises:
        MCPCredentialError: No keys, or a key is not a valid Fernet key.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise MCPCredentialError(
                "no credential key configured", context={"reason": "empty_key_set"}
            )
        try:
            self._fernet = MultiFernet([Fernet(k.encode("utf-8")) for k in keys])
        except (ValueError, TypeError) as exc:
            # A malformed key is an operator misconfig — fail loud, never log the key.
            raise MCPCredentialError(
                "credential key is malformed", context={"reason": "invalid_key"}
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        """Return the Fernet token for ``plaintext`` (url-safe text, safe to store)."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        """Return the plaintext for a Fernet ``token``.

        Raises:
            MCPCredentialError: The token is invalid (tampered, or no current key
                can decrypt it). Never leaks the token or key material.
        """
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError) as exc:
            raise MCPCredentialError(
                "stored credential could not be decrypted", context={"reason": "invalid_token"}
            ) from exc

    def rotate(self, token: str) -> str:
        """Re-encrypt ``token`` under the current (first) key — the rotation primitive."""
        return self._fernet.rotate(token.encode("utf-8")).decode("utf-8")


def cipher_from_config(config: APIConfig) -> CredentialCipher | None:
    """Build a :class:`CredentialCipher` from ``MCP_CREDENTIAL_KEY``, or ``None``.

    Returns ``None`` when the key is unset — the caller decides whether that is
    fatal (a server carrying credentials cannot be saved → MCPCredentialError) or
    fine (a no-auth server needs no key). Splits on commas for the rotation key set.
    """
    raw = config.mcp_credential_key
    if raw is None:
        return None
    keys = [k.strip() for k in raw.get_secret_value().split(",") if k.strip()]
    if not keys:
        return None
    return CredentialCipher(keys)
