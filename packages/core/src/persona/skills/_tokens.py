"""Token counting for the skills layer (T02, D-04-2).

Wraps ``tiktoken.get_encoding("cl100k_base")`` once at module import. The
encoder is provider-independent — the 2000-token budget enforced in
``persona.skills.injector`` is a budget, not a contract with any specific
model. Slight over/under-count vs the deployed model is fine.

``tiktoken`` is a core dep (D-01-11, declared in
``packages/core/pyproject.toml``). The import is hard at module load — there
is no ``len(text) // 4`` heuristic fallback (D-04-2). If ``tiktoken`` ever
fails to provide ``cl100k_base``, that is a packaging bug and we want to
crash at startup, not silently degrade the budget enforcement.

The encoder is cached at tiktoken's C layer (``get_encoding`` returns the
same object across calls). Stateless under concurrent access — safe to
share across threads / asyncio tasks without locking.
"""

from __future__ import annotations

import tiktoken

__all__ = ["count_tokens"]

# Loaded once at module import. Cached at the C layer; a module-level
# singleton is idiomatic. Re-binding via ``_ENCODER = ...`` elsewhere is
# unsupported.
_ENCODER = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return the ``cl100k_base`` token count of ``text``.

    Pure function: same input always returns the same output; no side
    effects; thread-safe (the encoder is stateless).

    Args:
        text: The string to tokenise. Empty string returns ``0``.

    Returns:
        The number of tokens.
    """
    return len(_ENCODER.encode(text))
