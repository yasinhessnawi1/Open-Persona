"""External-only tests (spec 10 T06/T08, spec 13 T16).

``@pytest.mark.external`` — skipped by default per the workspace pyproject's
``addopts = "-v --tb=short -m 'not integration and not external'"``. Run
manually with::

    uv run pytest -m external [-k <selector>]

These tests call real third-party APIs (Anthropic, OpenAI, ...) so they are
paid, non-deterministic, and rate-limited. They are intentionally outside
CI and intentionally outside the default local cadence.
"""
