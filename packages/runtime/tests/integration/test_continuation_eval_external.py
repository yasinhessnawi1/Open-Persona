"""The continuation eval against a REAL LLM judge (Spec A2, T11 / A2-R-2).

Validates that the reference-guided judge actually discriminates a coherent continuation from
an amnesiac one (re-deriving an established conclusion) on a committed scenario. The full
multi-leg run of real legs through the judge is T12; this proves the judge + rubric end-to-end.

``@pytest.mark.external`` — skipped in normal CI; needs a real backend from the root ``.env``
(``PERSONA_PROVIDER``/``PERSONA_MODEL``/``PERSONA_API_KEY``). The judge should be a DIFFERENT
model family than the agent under test (self-enhancement bias — D-A2-X-eval-gate); the operator
selects it via env at run time.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _continuation_eval import RubricScores, Scenario, Verdict, verdict_for
from persona.backends import BackendConfig, load_backend
from persona.schema.conversation import ConversationMessage

if TYPE_CHECKING:
    from persona.backends.protocol import ChatBackend

_SUITE = Path(__file__).resolve().parents[1] / "fixtures" / "continuation_scenarios.yaml"

pytestmark = [
    pytest.mark.external,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("PERSONA_PROVIDER") or not os.environ.get("PERSONA_MODEL"),
        reason="needs a real backend (PERSONA_PROVIDER/PERSONA_MODEL from root .env)",
    ),
]

_JUDGE_SYSTEM = (
    "You are a strict evaluator of an autonomous agent's task leg. You are given the task "
    "CONTRACT, the agent's prior ESTABLISHED CONCLUSION (settled — must NOT be re-derived), a "
    "FRESH FACT that invalidates a standing plan step (must be incorporated), the leg's "
    "RECONSTRUCTED CONTEXT, and the leg's OUTPUT. Score each dimension 0-2 and decide whether "
    "the fresh fact was present in the reconstructed context. Reply with ONLY a JSON object: "
    '{"contract_anchoring":int,"builds_on_conclusions":int,"freshness":int,'
    '"no_redundant_rework":int,"coherent_next_step":int,"fresh_fact_present":bool}. '
    "builds_on_conclusions=0 means the leg re-derived the established conclusion (amnesia). "
    "freshness=0 means it ignored the fresh fact and executed the invalidated step (ossification)."
)


def _judge_user(scenario: Scenario, reconstructed_context: str, leg_output: str) -> str:
    return (
        f"CONTRACT: {scenario.contract_goal}\n\n"
        f"ESTABLISHED CONCLUSION (do not re-derive): {scenario.established_conclusion}\n\n"
        f"FRESH FACT (must incorporate): {scenario.injected_fresh_fact}\n"
        f"INVALIDATED PLAN STEP: {scenario.invalidated_plan_step}\n\n"
        f"RECONSTRUCTED CONTEXT:\n{reconstructed_context}\n\n"
        f"LEG OUTPUT:\n{leg_output}\n"
    )


def _parse_scores(content: str) -> RubricScores:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    data = json.loads(match.group(0) if match else content)
    return RubricScores(
        contract_anchoring=int(data["contract_anchoring"]),
        builds_on_conclusions=int(data["builds_on_conclusions"]),
        freshness=int(data["freshness"]),
        no_redundant_rework=int(data["no_redundant_rework"]),
        coherent_next_step=int(data["coherent_next_step"]),
        fresh_fact_present=bool(data["fresh_fact_present"]),
    )


async def _score(
    backend: ChatBackend, scenario: Scenario, context: str, output: str
) -> RubricScores:
    now = datetime.now(UTC)
    response = await backend.chat(
        [
            ConversationMessage(role="system", content=_JUDGE_SYSTEM, created_at=now),
            ConversationMessage(
                role="user", content=_judge_user(scenario, context, output), created_at=now
            ),
        ]
    )
    return _parse_scores(response.content)


async def test_judge_discriminates_coherent_from_amnesiac() -> None:
    from _continuation_eval import load_scenarios

    scenario = next(s for s in load_scenarios(_SUITE) if s.mission == "research")
    backend = load_backend(BackendConfig.from_env())
    # F IS present in the reconstructed context (checkpoint conclusion + retrieved fresh fact).
    context = (
        f"CONTRACT: {scenario.contract_goal}\n"
        f"CHECKPOINT conclusions: {scenario.established_conclusion}\n"
        f"RETRIEVAL: {scenario.injected_fresh_fact}\n"
        f"TRIGGER: continue"
    )
    coherent = (
        "The 9h pgvector HNSW build exceeds the 2h window, so I'm NOT writing up pgvector; "
        "I'm re-ranking Qdrant and Milvus on build-time. Pinecone stays excluded (managed-only)."
    )
    amnesiac = (
        "Let me survey vector stores from scratch. Candidates: Pinecone, pgvector, Qdrant, "
        "Milvus. Pinecone is managed-only, so I'll exclude it. Next I'll benchmark the rest."
    )

    coherent_verdict = verdict_for(await _score(backend, scenario, context, coherent))
    amnesiac_verdict = verdict_for(await _score(backend, scenario, context, amnesiac))

    assert coherent_verdict == Verdict.COHERENT, f"coherent leg judged {coherent_verdict}"
    assert amnesiac_verdict != Verdict.COHERENT, f"amnesiac leg judged {amnesiac_verdict}"
