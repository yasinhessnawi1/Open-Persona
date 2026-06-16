"""BYO-MCP credential encryption (spec 30 T07, D-30-4).

Round-trips, ciphertext ≠ plaintext, tamper rejection, MultiFernet rotation, and
the config helper's unset-key → None contract.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from persona_api.config import APIConfig
from persona_api.errors import MCPCredentialError
from persona_api.mcp.crypto import CredentialCipher, cipher_from_config


def test_round_trips_and_ciphertext_differs_from_plaintext() -> None:
    cipher = CredentialCipher([Fernet.generate_key().decode()])
    secret = "Bearer super-secret-token-123"  # noqa: S105 — test fixture
    token = cipher.encrypt(secret)
    assert token != secret
    assert secret not in token
    assert cipher.decrypt(token) == secret


def test_decrypt_rejects_a_tampered_token() -> None:
    cipher = CredentialCipher([Fernet.generate_key().decode()])
    token = cipher.encrypt("secret-value")
    # Flip a character in the ciphertext body → HMAC verification fails.
    mid = len(token) // 2
    flipped = "A" if token[mid] != "A" else "B"
    tampered = token[:mid] + flipped + token[mid + 1 :]
    with pytest.raises(MCPCredentialError):
        cipher.decrypt(tampered)


def test_decrypt_rejects_token_from_a_foreign_key() -> None:
    a = CredentialCipher([Fernet.generate_key().decode()])
    b = CredentialCipher([Fernet.generate_key().decode()])
    with pytest.raises(MCPCredentialError):
        b.decrypt(a.encrypt("secret"))


def test_multifernet_decrypts_old_key_after_rotation() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old_only = CredentialCipher([old])
    token = old_only.encrypt("rotate-me")
    # New cipher: new key first (encrypts), old key still present (decrypts).
    rotated = CredentialCipher([new, old])
    assert rotated.decrypt(token) == "rotate-me"


def test_empty_key_set_is_fatal() -> None:
    with pytest.raises(MCPCredentialError):
        CredentialCipher([])


def test_malformed_key_is_fatal_and_does_not_leak() -> None:
    with pytest.raises(MCPCredentialError) as exc:
        CredentialCipher(["not-a-valid-fernet-key"])
    assert "not-a-valid-fernet-key" not in str(exc.value)


def test_cipher_from_config_none_when_key_unset() -> None:
    assert cipher_from_config(APIConfig(mcp_credential_key=None)) is None


def test_cipher_from_config_builds_when_key_set() -> None:
    key = Fernet.generate_key().decode()
    cipher = cipher_from_config(APIConfig(mcp_credential_key=key))
    assert cipher is not None
    assert cipher.decrypt(cipher.encrypt("hi")) == "hi"


def test_cipher_from_config_parses_rotation_key_set() -> None:
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    cipher = cipher_from_config(APIConfig(mcp_credential_key=f"{k1},{k2}"))
    assert cipher is not None
    # A token made with k2 (the second/decrypt-only key) still decrypts.
    assert cipher.decrypt(CredentialCipher([k2]).encrypt("y")) == "y"
