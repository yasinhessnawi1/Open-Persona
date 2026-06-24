"""persona-connectors — the connector framework (Spec C1, the trunk).

Makes a persona reachable on messaging platforms (Telegram/Discord/Slack/
WhatsApp/SMS/email). The framework owns everything SHARED across platforms —
the inbound→route→respond→outbound flow, the ``Connector`` protocol +
normalisation contracts, the per-persona parallel-conversation model,
persona-selection / name-parsing, account-linking / identity-mapping, and the
outbound path consuming C0 — so each per-platform adapter (C2–C5) is thin.

It runs as a separate long-lived process (the 3rd, after persona-api and
persona-voice). Per C1-D-1 it reuses persona-api's reply-producing chat flow +
C0's delivery router in-process (the ``run_worker.py`` pattern); the api-coupling
lives ONLY in :mod:`persona_connectors.composition`. The owned surface
(:mod:`persona_connectors.domain`) stays import-decoupled from persona-api so a
future extract-to-core is a dependency swap, not a reshape.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
