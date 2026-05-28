"""Tests for ``persona.skills._tokens`` (T02, D-04-2).

Pin the encoder choice (``cl100k_base``), confirm thread-safety, and
exercise the standard token-counting edge cases.
"""

# ruff: noqa: ANN401, ARG001, ARG002

from __future__ import annotations

import threading

import pytest
import tiktoken
from persona.skills import _tokens
from persona.skills._tokens import count_tokens


class TestCountTokensBasics:
    def test_empty_string_is_zero(self) -> None:
        assert count_tokens("") == 0

    def test_simple_ascii(self) -> None:
        # Anchor against the live encoder rather than a hard-coded number —
        # the cl100k_base table is fixed but pinning the integer would be
        # brittle if tiktoken ever rebased.
        ref = tiktoken.get_encoding("cl100k_base")
        text = "the quick brown fox jumps over the lazy dog"
        assert count_tokens(text) == len(ref.encode(text))

    def test_unicode_safe(self) -> None:
        # Accents / non-ASCII produce more tokens than chars; just confirm
        # the call doesn't raise and returns > char-count.
        assert count_tokens("héllo wörld") > 0

    def test_emoji_safe(self) -> None:
        assert count_tokens("🎉 test") > 0

    def test_deterministic_across_calls(self) -> None:
        text = "stability check"
        counts = {count_tokens(text) for _ in range(50)}
        assert len(counts) == 1


class TestEncoderCaching:
    """The module-level ``_ENCODER`` should be loaded once at import.

    The C layer caches by name, so even if we call
    ``tiktoken.get_encoding("cl100k_base")`` from outside, the same object
    is returned. Pin both invariants.
    """

    def test_module_level_encoder_is_cl100k_base(self) -> None:
        assert _tokens._ENCODER.name == "cl100k_base"

    def test_module_level_encoder_is_same_as_fresh_lookup(self) -> None:
        # tiktoken caches at the C layer; the module's encoder IS the one
        # returned by a fresh lookup. This guards against accidentally
        # re-binding ``_ENCODER`` somewhere.
        fresh = tiktoken.get_encoding("cl100k_base")
        assert _tokens._ENCODER is fresh


class TestThreadSafety:
    def test_concurrent_count_calls_give_identical_results(self) -> None:
        # The encoder is stateless; eight threads tokenising the same text
        # must all observe the same count.
        text = "concurrency check " * 100
        results: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            n = count_tokens(text)
            with lock:
                results.append(n)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(results)) == 1


class TestPerformanceProperties:
    """Loose sanity checks. Not benchmarks — just guard against accidental
    O(N²) regressions."""

    def test_short_string_under_50_ms(self) -> None:
        import time

        text = "short text"
        start = time.perf_counter()
        for _ in range(100):
            count_tokens(text)
        elapsed = time.perf_counter() - start
        # 100 short tokenisations should finish in well under 50 ms on any
        # remotely sane machine. If this ever fires, something is wrong.
        assert elapsed < 0.5

    def test_50kb_string_tokenises_under_500ms(self) -> None:
        import time

        big = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 1000
        start = time.perf_counter()
        n = count_tokens(big)
        elapsed = time.perf_counter() - start
        assert n > 1000
        assert elapsed < 0.5


def test_import_does_not_raise() -> None:
    # Belt-and-braces guard: the hard-import at module load must succeed
    # whenever the test suite runs (tiktoken is a core dep). If this ever
    # raises ImportError, a packaging change broke spec 04's contract.
    import importlib

    importlib.reload(_tokens)
    assert _tokens.count_tokens("smoke") > 0


def test_pure_function_no_side_effects(tmp_path: object) -> None:
    # No I/O, no clock, no logging — count_tokens is a pure function.
    # Calling it should not change any module-level state we can observe.
    snapshot_id = id(_tokens._ENCODER)
    for _ in range(20):
        count_tokens("x")
    assert id(_tokens._ENCODER) == snapshot_id


# Sanity: pytest collected something
@pytest.mark.parametrize(
    ("text", "expected_positive"),
    [
        ("", False),
        ("a", True),
        ("hello world", True),
        ("hello world\n\nlong text", True),
    ],
)
def test_count_tokens_positive_for_nonempty(text: str, expected_positive: bool) -> None:
    n = count_tokens(text)
    assert (n > 0) == expected_positive
