"""The K4 wellbeing runtime providers (Spec K4, T3; K4-D-X-layering).

The runtime half of K4 — the callables that fill K3's reserved seams with the
persona-core policy. Core owns the policy + care text (LLM-free, I/O-free); this
module maps K3's runtime types onto the core primitives at the seam, so core never
imports the runtime (the layering discipline). T3 ships the **surfacing** provider
(the ``graph_surfacing_guidance`` slot); T4 adds the **allowlist** provider here.

The surfacing provider is the ``(category, recency) -> care-text`` callable
:class:`~persona_runtime.prompt.PromptBuilder` consults for each injected graph node
carrying a ``wellbeing_category``. It translates K3's coarse
:class:`~persona_runtime.prompt.GraphRecency` bucket into the policy's
:class:`~persona.wellbeing_policy.RecencyBand` and looks up the recency-weighted care
text. An untagged or unrecognised category yields ``None`` — the slot then renders
nothing (the reserved no-op), so wiring this provider in is purely additive.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from persona.wellbeing import (
    WellbeingCategory,  # noqa: TC002 — Pydantic needs the runtime ref (FlaggedNode.category)
)
from persona.wellbeing_care_text import care_text_for_tag
from persona.wellbeing_policy import RecencyBand, should_subtract
from pydantic import BaseModel, ConfigDict

from persona_runtime.prompt import GraphRecency

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from persona_runtime.graph_selection import GatingContext

__all__ = [
    "FlaggedNode",
    "make_allowlist_provider",
    "recency_band",
    "surfacing_guidance",
    "topical_overlap",
]


def recency_band(recency: GraphRecency) -> RecencyBand:
    """Map K3's coarse recency bucket to the policy's recency band (K4-D-4).

    ``RECENT`` is the acute window; everything older (``A_WHILE_BACK`` / ``LONG_AGO``)
    is historical — the two-band coarsening criterion 6 needs. Done runtime-side so
    persona-core never imports :class:`GraphRecency`.
    """
    return RecencyBand.ACUTE if recency is GraphRecency.RECENT else RecencyBand.HISTORICAL


def surfacing_guidance(category: str, recency: GraphRecency) -> str | None:
    """The K4 surfacing-guidance provider for K3's slot (K4-D-3).

    Wraps the core care-text catalogue: maps the node's recency bucket to a band and
    returns the recency-weighted care text for the node's ``wellbeing_category`` tag,
    or ``None`` for an untagged / unrecognised tag (the slot then renders nothing).
    Pass this directly as ``PromptBuilder.build``'s ``graph_surfacing_guidance``.

    Args:
        category: The node's stored ``wellbeing_category`` tag.
        recency: The node's coarse recency bucket (from the K3 projection).

    Returns:
        The persona-facing care instruction, or ``None`` when there is no care text
        for the tag.
    """
    return care_text_for_tag(category, recency_band(recency))


# ===== the context gate + allowlist provider (T4; K4-D-2) =====================


class FlaggedNode(BaseModel):
    """A gate-eligible flagged node, as the allowlist gate needs to see it (K4-D-2).

    The light shape the gate operates over — the composition (T6) builds these from
    the owner's wellbeing-tagged :class:`~persona.graph.models.ConceptNode`\\ s
    (resolving the recency band from provenance + ``now``). Keeping it light keeps the
    gate pure and unit-testable without a store.

    Attributes:
        node_id: The durable node id (what the allowlist subtracts).
        category: The (gate-eligible) sensitive category.
        recency: The disclosure's recency band — selects the gate strength.
        text: The node's salient text (concept name + content) for the topical signal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    category: WellbeingCategory
    recency: RecencyBand
    text: str


# A small English stop list for the lexical overlap signal. Lean by intent — the
# overlap is meant to be explainable (shared salient words), not a model. Non-English
# tokens simply aren't filtered (they still match on shared terms); robust multilingual
# / paraphrase matching is the documented limit of the v1 lexical scorer, which is
# INJECTABLE so the composition (T6) can swap a semantic scorer if T7 evidence asks.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "we",
        "they",
        "he",
        "she",
        "my",
        "your",
        "our",
        "their",
        "me",
        "us",
        "them",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "not",
        "can",
        "could",
        "would",
        "should",
        "will",
        "about",
        "what",
        "how",
        "when",
        "then",
        "so",
        "if",
        "at",
        "by",
        "as",
        "from",
    }
)


def _tokens(text: str) -> set[str]:
    """Salient lowercase word tokens — Unicode-aware, stop-words and short words dropped."""
    return {
        w
        for w in re.findall(r"\w+", text.lower(), re.UNICODE)
        if len(w) > 2 and w not in _STOPWORDS
    }


def topical_overlap(text: str, node_text: str) -> float:
    """Lean, explainable topical overlap of ``text`` against a node's salient terms.

    The fraction of the node's salient terms that appear in ``text`` — a shared-words
    signal a human can read (K4-D-2: lean and explainable, not a black box). Returns
    ``0.0`` when the node has no salient terms (conservative — no spurious lift). The
    default gate scorer; the composition may inject a semantic scorer with the same
    ``(text, node_text) -> [0, 1]`` shape.
    """
    node_terms = _tokens(node_text)
    if not node_terms:
        return 0.0
    return len(node_terms & _tokens(text)) / len(node_terms)


# How strongly this turn's query alone must overlap a node for "the user raised it
# now" — an explicit, recency-independent lift (criterion: the user opening the topic
# always surfaces the knowledge). Higher than the relevance floors because it reads
# the bare query, not the whole window.
_TOPIC_OPENED_FLOOR = 0.5


def make_allowlist_provider(
    *,
    flagged_nodes: Callable[[str], Sequence[FlaggedNode]],
    owner_node_ids: Callable[[str], set[str]],
    relevance_scorer: Callable[[str, str], float] = topical_overlap,
) -> Callable[[GatingContext], set[str] | None]:
    """Build the K4 allowlist provider for K3's subtraction seam (K4-D-2).

    The returned callable computes, per turn, the positive allowlist
    ``owner_nodes − gated`` (Option A) — or ``None`` on the common path so the hot
    path stays free. It gates only the **gate-eligible** crisis/trauma categories
    (the policy's ``should_subtract`` enforces that), and only when the user has
    neither opened the topic this turn nor is the conversation topically about it
    (the recency-weighted lift). ``None`` is returned whenever nothing is flagged or
    nothing gates — most turns — so K1 reads the whole owner graph at zero added cost
    (and K1's dense leg already enumerates the owner set in-kernel, so the rare
    ``owner_node_ids`` read is the same cost class, not a new scan).

    Args:
        flagged_nodes: ``owner_id -> the owner's gate-eligible flagged nodes`` (the
            small wellbeing-tagged set; the composition wires the store read).
        owner_node_ids: ``owner_id -> the owner's full node-id set`` — only consulted
            on a turn that actually gates something (Option A's positive allowlist).
        relevance_scorer: ``(text, node_text) -> [0, 1]`` topical signal; defaults to
            :func:`topical_overlap`. Injectable so a semantic scorer can replace the
            lexical default without touching the gate.

    Returns:
        The ``GatingContext -> set[str] | None`` callable for ``make_graph_retrieval``'s
        ``allowlist_provider``.
    """

    def provider(ctx: GatingContext) -> set[str] | None:
        flagged = flagged_nodes(ctx.owner_id)
        if not flagged:
            return None  # common path: nothing sensitive in the graph → no subtraction
        window_text = " ".join((ctx.query, *ctx.recent_messages))
        gated = {
            node.node_id
            for node in flagged
            if should_subtract(
                node.category,
                recency=node.recency,
                topic_opened=relevance_scorer(ctx.query, node.text) >= _TOPIC_OPENED_FLOOR,
                relevance=relevance_scorer(window_text, node.text),
            )
        }
        if not gated:
            return None  # nothing gated this turn → None (zero-cost; no owner-graph read)
        return owner_node_ids(ctx.owner_id) - gated  # Option A: the positive allowlist

    return provider
