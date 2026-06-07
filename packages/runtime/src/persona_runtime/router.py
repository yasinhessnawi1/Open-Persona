"""Spec 05 :class:`Router` → Spec 18 strangler-fig alias.

The Spec 05 rule-based router moved to
:class:`persona_runtime.routing.heuristic.HeuristicRouter` at Spec 18 T05.
This module preserves the public ``Router`` name as a re-export so existing
callers (the API composition root at ``runtime_factory.py``, the
:class:`~persona_runtime.loop.ConversationLoop`, the agentic loop, and the
existing test fixtures) stay zero-touch — see
``docs/specs/phase2/spec_18/decisions.md`` D-18-X-strangler-fig-alias-shape
and ``handover.md`` "Strangler-fig alias discipline (load-bearing reminder)".

The existing ``Router.choose(persona, message, conversation, *, turn_has_image,
tier_registry) -> str`` interface is unchanged because
:class:`HeuristicRouter.choose` preserves it verbatim. New callers use
:meth:`HeuristicRouter.route` (the Spec 18 Router Protocol entry point) and
consume :class:`~persona_runtime.routing.types.RoutingDecision`.

The Spec 13 T09 structural test at
``test_router_vision.py::TestStructuralPreFilter`` patches
``Router._candidate_tiers`` — the alias resolves to
:class:`HeuristicRouter._candidate_tiers` so the patch target still matches.
"""

from __future__ import annotations

from persona_runtime.routing.heuristic import HeuristicRouter as Router

__all__ = ["Router"]
