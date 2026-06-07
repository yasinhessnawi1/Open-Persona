"""Spec 18 routing evaluation harness (T13; D-18-X-routing-eval-shape).

Two complementary deliverables:

* **Regression catching** (:mod:`routing_eval.replay`) — load a labelled
  YAML fixture of representative turns, run them through the configured
  router, assert each chose the ``expected_tier``. CI-runnable.
* **Drift catching** (:mod:`routing_eval.aggregate`) — read JSONL
  :class:`~persona_runtime.logging.TurnLog` files written by production /
  staging, print per-tier and per-profile distributions, fallback rates,
  and latency percentiles. Manual tool the
  D-18-X-monthly-review-cadence checklist consumes.

v0.1 out-of-scope (production-rationale per D-18-X-routing-eval-shape):
automated quality scoring (needs a quality signal v0.1 doesn't ship);
retraining triggers (no learned component v0.1); live A/B routing
(needs a quality signal to drive comparison). These are legitimate v0.2
candidates, NOT demo-scoping.
"""

from __future__ import annotations
