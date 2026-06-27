"""Tests for the activity args-summary redactor (P2 T1 — the security gate).

Proves no secret leaks into an activity-start args summary, exercising the concrete
worst cases: MCP-credential-shaped args, Authorization/bearer tokens, ``api_key`` /
``*_secret`` keys, and an oversized blob (the truncation + cap backstop).
"""

from __future__ import annotations

import json

from persona.tools.redaction import REDACTED, redact_args


def _serialise(summary: dict[str, str]) -> str:
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


class TestKeyDenylist:
    def test_redacts_authorization_and_bearer_tokens(self) -> None:
        summary = redact_args({"Authorization": "Bearer sk-live-abc123", "bearer_token": "xyz"})
        assert summary["Authorization"] == REDACTED
        assert summary["bearer_token"] == REDACTED
        assert "sk-live-abc123" not in _serialise(summary)
        assert "xyz" not in _serialise(summary)

    def test_redacts_api_key_and_secret_keys(self) -> None:
        summary = redact_args(
            {
                "api_key": "AKIA-SECRET",
                "openai_api_key": "sk-proj-1",
                "client_secret": "shh",
                "db_password": "hunter2",
            }
        )
        assert summary["api_key"] == REDACTED
        assert summary["openai_api_key"] == REDACTED
        assert summary["client_secret"] == REDACTED
        assert summary["db_password"] == REDACTED
        blob = _serialise(summary)
        for secret in ("AKIA-SECRET", "sk-proj-1", "shh", "hunter2"):
            assert secret not in blob

    def test_redacts_secret_nested_in_a_structured_arg(self) -> None:
        # MCP-credential-shaped args: a secret nested under a benign top-level key.
        nested = {"headers": {"Authorization": "Bearer nested-secret", "X-Trace": "ok"}}
        summary = redact_args(nested)
        assert "nested-secret" not in _serialise(summary)
        # The non-sensitive sibling survives (redaction is surgical, not wholesale).
        assert "ok" in summary["headers"]

    def test_benign_key_is_not_over_redacted(self) -> None:
        # "keyword" contains no denylist substring — it must NOT be redacted.
        summary = redact_args({"keyword": "rent disputes", "query": "oslo"})
        assert summary["keyword"] == "rent disputes"
        assert summary["query"] == "oslo"


class TestValueBackstop:
    def test_long_value_is_truncated_and_blob_does_not_leak(self) -> None:
        blob = "A" * 5000
        summary = redact_args({"code": blob})
        assert blob not in summary["code"]
        assert summary["code"].startswith("A" * 120)
        assert "(+4880 chars)" in summary["code"]

    def test_total_summary_is_capped(self) -> None:
        # Many medium values exceed the 512-char total cap → bounded output + marker.
        args = {f"field_{i}": "x" * 100 for i in range(20)}
        summary = redact_args(args)
        assert len(_serialise(summary)) <= 512
        assert "…" in summary

    def test_non_string_values_render_json_safe(self) -> None:
        summary = redact_args({"count": 3, "flags": [True, False], "ratio": 1.5})
        # All values are strings (JSON-safe) and the data is preserved.
        assert all(isinstance(v, str) for v in summary.values())
        assert summary["count"] == "3"
        assert "true" in summary["flags"]


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        args = {"b": "two", "a": "one", "api_key": "secret"}
        assert redact_args(dict(args)) == redact_args(dict(args))

    def test_empty_args(self) -> None:
        assert redact_args({}) == {}
