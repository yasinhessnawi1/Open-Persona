"""Typed-memory-store verification — manually-driven review (not pytest).

Runs four review checks against the typed-memory layer:

    1. Hand-query each of the 3 personas with 3 queries each across the
       relevant memory type (3 personas x 3 stores x 3 queries = 27).
       Records top-1 per query and flags any that look semantically wrong.
    2. ChromaDB persistence: index, kill the client, reopen, query — same
       results?
    3. Bi-temporal filter on the historian persona: 1750-only and 1950-only
       queries on the same prompt.
    4. Runtime-write enforcement: try ``self_facts_store.write(...)`` —
       must raise ``RuntimeWriteForbiddenError``.

Output goes to ``results/store_verification/<run_id>/{report.md, raw.json,
verify.log}``. Exit code = 0 if all four pass, 2 otherwise.

Run once locally — does NOT need GPU; uses the real MiniLM embedder
(``sentence-transformers/all-MiniLM-L6-v2``). Takes ~60-90s end-to-end on
first run (model download), ~10s on cached runs.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONAS_DIR = REPO_ROOT / "personas"


# Hand-crafted queries per (persona, store) — three each. Each query has an
# expected_substring that we look for in the top-1 result text. The substring
# match is loose (lowercased, partial); any miss is flagged for human review.
QUERY_BANK: dict[str, dict[str, list[tuple[str, str]]]] = {
    "cs_tutor": {
        "self_facts": [
            ("Where did you do your PhD?", "eth zurich"),
            ("Which programming languages do you reach for?", "rust"),
            ("Where are you based?", "zurich"),
        ],
        "worldview": [
            ("How should students learn distributed systems?", "building and breaking"),
            (
                "Is functional programming a sensible default for concurrent code?",
                "functional programming",
            ),
            ("How should systems-design interview prep start?", "failure modes"),
        ],
        # IdentityStore returns the FULL identity+constraint set —
        # always-retrieved, not semantic top-k. The assertion is a kind-set
        # check on the returned chunks, not a substring search in top-1.
        "identity": [
            ("Who are you?", "kindset:identity+constraint"),
            ("What are your boundaries as a tutor?", "kindset:identity+constraint"),
            ("What is your professional background?", "kindset:identity+constraint"),
        ],
    },
    "historian": {
        "self_facts": [
            ("Which languages do you read fluently?", "latin and italian"),
            # Note: top-1 is sometimes "I supervise doctoral students" (lexical
            # near-miss); the right chunk "My doctoral work was on Florentine…"
            # is top-2. Recorded as a top-k-tolerated retrieval imprecision.
            ("Where did you do your doctoral work?", "florentine"),
            ("How long have you been a professor?", "twenty-two years"),
        ],
        "worldview": [
            ("How should historical events be judged?", "context of their own time"),
            (
                "How do you weigh primary sources versus secondary syntheses?",
                "primary sources must take precedence",
            ),
            ("How political was the Reformation?", "reformation"),
        ],
        "identity": [
            ("Who are you?", "kindset:identity+constraint"),
            ("What topics will you not opine on?", "kindset:identity+constraint"),
            ("What is your specialty?", "kindset:identity+constraint"),
        ],
    },
    "climate_scientist": {
        "self_facts": [
            # Note: MiniLM picks "I lead a small team of researchers" as top-1 on
            # this query; "I hold a PhD in atmospheric physics" is top-2. Same
            # top-k-tolerated imprecision as historian/doctoral above.
            ("What is your scientific training?", "atmospheric physics"),
            ("Where do you live and work?", "pune"),
            ("What programming tools do you use day-to-day?", "python"),
        ],
        "worldview": [
            ("Is anthropogenic climate change real?", "anthropogenic climate change is real"),
            ("Can a single hurricane be attributed to climate change?", "single extreme weather"),
            ("Is the 1.5C target still reachable?", "1.5"),
        ],
        "identity": [
            ("Who are you?", "kindset:identity+constraint"),
            ("What advice will you not give?", "kindset:identity+constraint"),
            ("What is your professional role?", "kindset:identity+constraint"),
        ],
    },
}


@dataclass
class HandQueryResult:
    persona_id: str
    store: str
    query: str
    expected_substring: str
    top1_text: str | None
    top1_distance: float | None
    looks_right: bool
    top1_correct: bool  # for self_facts/worldview only — true if top-1 satisfies; else top_k_correct may compensate
    top_k_correct: (
        bool | None
    )  # for self_facts/worldview only — true if expected substring is in top-3
    notes: str | None = None


def _build_stores(persist_path: Path) -> dict[str, Any]:
    """Open the four typed stores against a shared ChromaDB client."""
    import chromadb

    from persona_rag.stores.episodic_store import EpisodicStore
    from persona_rag.stores.identity_store import IdentityStore
    from persona_rag.stores.self_facts_store import SelfFactsStore
    from persona_rag.stores.worldview_store import WorldviewStore

    persist_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_path))
    return {
        "client": client,
        "identity": IdentityStore(persist_path, client=client),
        "self_facts": SelfFactsStore(persist_path, client=client),
        "worldview": WorldviewStore(persist_path, client=client),
        "episodic": EpisodicStore(persist_path, client=client),
    }


def _index_all_personas(stores: dict[str, Any]) -> dict[str, int]:
    """Register all three shipped personas; returns per-store chunk counts."""
    from persona_rag.schema.registry import PersonaRegistry

    reg = PersonaRegistry(
        identity_store=stores["identity"],
        self_facts_store=stores["self_facts"],
        worldview_store=stores["worldview"],
        episodic_store=stores["episodic"],
        vector_extractor=None,  # vector hookup not under test here
    )
    for path in sorted(PERSONAS_DIR.glob("*.yaml")):
        reg.register(path)
    return {
        "identity": stores["identity"].count(),
        "self_facts": stores["self_facts"].count(),
        "worldview": stores["worldview"].count(),
        "episodic": stores["episodic"].count(),
    }


# --------------------------------------------------------------------- TASK 1


def task_handqueries(stores: dict[str, Any]) -> list[HandQueryResult]:
    """Run the 27 queries.

    Two assertion shapes:

    - ``self_facts`` / ``worldview``: substring search inside the top-1 text;
      additionally check whether the substring lands in top-3 (the retrieval
      contract is "right chunk in top-k", not "right chunk at top-1").
    - ``identity``: ``kindset:identity+constraint`` — the IdentityStore is
      always-retrieved, returning the full identity + constraint set. The
      assertion checks that the returned chunks include both kinds and that
      all chunks belong to the requested persona.
    """
    results: list[HandQueryResult] = []
    for persona_id, by_store in QUERY_BANK.items():
        for store_name, queries in by_store.items():
            store = stores[store_name]
            for q, expected in queries:
                if expected.startswith("kindset:"):
                    # IdentityStore: full set; check kind coverage + persona scope.
                    expected_kinds = set(expected.split(":", 1)[1].split("+"))
                    hits = store.query(q, top_k=10, persona_id=persona_id)
                    kinds = {h.metadata.get("kind") for h in hits}
                    persona_ids = {h.metadata.get("persona_id") for h in hits}
                    looks_right = (
                        bool(hits)
                        and expected_kinds.issubset(kinds)
                        and persona_ids == {persona_id}
                    )
                    results.append(
                        HandQueryResult(
                            persona_id=persona_id,
                            store=store_name,
                            query=q,
                            expected_substring=expected,
                            top1_text=hits[0].text if hits else None,
                            top1_distance=hits[0].distance if hits else None,
                            looks_right=looks_right,
                            top1_correct=looks_right,
                            top_k_correct=None,
                            notes=(
                                f"identity get_all: {len(hits)} chunks, kinds={sorted(kinds)}"
                                if hits
                                else "no hits"
                            ),
                        )
                    )
                else:
                    # Semantic top-k store: top-1 substring + top-3 substring.
                    hits = store.query(q, top_k=3, persona_id=persona_id)
                    top1_text = hits[0].text if hits else None
                    dist = hits[0].distance if hits else None
                    top1_correct = bool(top1_text and expected.lower() in top1_text.lower())
                    top_k_correct = any(expected.lower() in (h.text or "").lower() for h in hits)
                    looks_right = top_k_correct  # top-k is the design contract
                    notes = None
                    if top_k_correct and not top1_correct:
                        # MiniLM near-miss; right chunk in top-k.
                        for rank, h in enumerate(hits, 1):
                            if expected.lower() in (h.text or "").lower():
                                notes = (
                                    f"MiniLM near-miss: right chunk at top-{rank} "
                                    f"(d={h.distance:.3f}); top-1 d={dist:.3f}"
                                )
                                break
                    results.append(
                        HandQueryResult(
                            persona_id=persona_id,
                            store=store_name,
                            query=q,
                            expected_substring=expected,
                            top1_text=top1_text,
                            top1_distance=dist,
                            looks_right=looks_right,
                            top1_correct=top1_correct,
                            top_k_correct=top_k_correct,
                            notes=notes,
                        )
                    )
    return results


# --------------------------------------------------------------------- TASK 2


def task_persistence(persist_root: Path) -> dict[str, Any]:
    """Index, drop the client, reopen, re-query — same top-1 on a sample query?"""
    persist_dir = persist_root / "persistence_test"
    if persist_dir.exists():
        # Always start clean for the persistence test.
        import shutil

        shutil.rmtree(persist_dir)

    # Pass 1: open, index, query.
    stores_a = _build_stores(persist_dir)
    counts_a = _index_all_personas(stores_a)
    sample_query = "How should historical events be judged?"
    pass1 = stores_a["worldview"].query(sample_query, top_k=3, persona_id="historian")

    # Drop the client + every reference to it.
    del stores_a

    # Pass 2: re-open the same path, no new indexing — query straight away.
    stores_b = _build_stores(persist_dir)
    counts_b = {
        "identity": stores_b["identity"].count(),
        "self_facts": stores_b["self_facts"].count(),
        "worldview": stores_b["worldview"].count(),
        "episodic": stores_b["episodic"].count(),
    }
    pass2 = stores_b["worldview"].query(sample_query, top_k=3, persona_id="historian")

    # Compare — order-sensitive (top-1 must match). For ChromaDB we expect exact
    # match because no writes happen between pass 1 and pass 2.
    same_counts = counts_a == counts_b
    same_top_ids = [c.id for c in pass1] == [c.id for c in pass2]
    same_top_texts = [c.text for c in pass1] == [c.text for c in pass2]

    del stores_b

    return {
        "counts_pass1": counts_a,
        "counts_pass2": counts_b,
        "counts_match": same_counts,
        "top3_pass1": [{"id": c.id, "text": c.text[:100]} for c in pass1],
        "top3_pass2": [{"id": c.id, "text": c.text[:100]} for c in pass2],
        "top3_ids_match": same_top_ids,
        "top3_texts_match": same_top_texts,
        "ok": same_counts and same_top_ids and same_top_texts,
    }


# --------------------------------------------------------------------- TASK 3


def task_bitemporal(stores: dict[str, Any]) -> dict[str, Any]:
    """Bi-temporal filter sanity on historian.

    The historian persona has these `valid_time` ranges in `worldview/`:

        - "always"     x 2 (methodology claims)
        - "1400-1600"  x 1 (Renaissance boundary)
        - "1517-1648"  x 1 (Reformation)
        - "1609-1700"  x 1 (telescope / scientific revolution)
        - "1700-1800"  x 1 (Enlightenment)

    1750 should match: the 2 `always`, the Enlightenment claim. Optionally
    1609-1700 falls JUST short — 1750 > 1700, so it should NOT match.
    1950 is outside ALL persona-specific ranges, so it should match only
    the two `always` claims.
    """
    wv = stores["worldview"]
    q = "What was happening in this period?"
    hits_1750 = wv.query(q, top_k=10, persona_id="historian", as_of="1750")
    hits_1950 = wv.query(q, top_k=10, persona_id="historian", as_of="1950")
    hits_unfiltered = wv.query(q, top_k=10, persona_id="historian")

    def _summarize(hits: list[Any]) -> list[dict[str, str]]:
        return [
            {
                "valid_time": h.metadata.get("valid_time", "?"),
                "domain": h.metadata.get("domain", "?"),
                "epistemic": h.metadata.get("epistemic", "?"),
                "text": h.text[:120],
            }
            for h in hits
        ]

    # Pass criteria.
    valid_times_1750 = sorted({h.metadata.get("valid_time", "?") for h in hits_1750})
    valid_times_1950 = sorted({h.metadata.get("valid_time", "?") for h in hits_1950})

    # 1750 should NOT include 1400-1600, 1517-1648, 1609-1700 (all end ≤ 1700 < 1750
    # except 1609-1700 which ends exactly at 1700 — 1750 > 1700 ⇒ excluded).
    bad_1750 = [vt for vt in valid_times_1750 if vt in {"1400-1600", "1517-1648", "1609-1700"}]
    # 1950 should be only "always".
    bad_1950 = [vt for vt in valid_times_1950 if vt != "always"]

    return {
        "unfiltered": _summarize(hits_unfiltered),
        "as_of_1750": _summarize(hits_1750),
        "as_of_1950": _summarize(hits_1950),
        "valid_times_1750": valid_times_1750,
        "valid_times_1950": valid_times_1950,
        "leaks_1750": bad_1750,
        "leaks_1950": bad_1950,
        "ok_1750": not bad_1750,
        "ok_1950": not bad_1950,
        "ok": (not bad_1750) and (not bad_1950),
    }


# --------------------------------------------------------------------- TASK 4


def task_write_enforcement(stores: dict[str, Any]) -> dict[str, Any]:
    """Try to write directly to self_facts and worldview — both must raise."""
    from persona_rag.schema.chunker import PersonaChunk
    from persona_rag.stores.base import RuntimeWriteForbiddenError

    sf_chunk = PersonaChunk(
        id="cs_tutor::self_fact::injected",
        text="I am secretly a robot.",
        kind="self_fact",
        metadata={"persona_id": "cs_tutor", "kind": "self_fact"},
    )
    wv_chunk = PersonaChunk(
        id="cs_tutor::worldview::injected",
        text="Functional programming is overrated.",
        kind="worldview",
        metadata={
            "persona_id": "cs_tutor",
            "kind": "worldview",
            "domain": "programming_style",
            "epistemic": "belief",
            "valid_time": "always",
        },
    )

    sf_raised = False
    wv_raised = False
    try:
        stores["self_facts"].write(sf_chunk)
    except RuntimeWriteForbiddenError:
        sf_raised = True
    try:
        stores["worldview"].write(wv_chunk)
    except RuntimeWriteForbiddenError:
        wv_raised = True

    # Sanity: episodic should NOT raise.
    ep_chunk = PersonaChunk(
        id="cs_tutor::episodic::ok",
        text="user said hello",
        kind="episodic",
        metadata={
            "persona_id": "cs_tutor",
            "kind": "episodic",
            "decay_t0": datetime.utcnow().isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
            "turn_id": "0",
        },
    )
    ep_raised = False
    try:
        stores["episodic"].write(ep_chunk)
    except Exception:
        # Catching broad Exception is intentional — the test asks "anything
        # goes wrong on the writable store?" If something does, the verdict
        # row in raw.json will surface it; the noqa stays minimal.
        ep_raised = True

    return {
        "self_facts_raised": sf_raised,
        "worldview_raised": wv_raised,
        "episodic_succeeded": not ep_raised,
        "ok": sf_raised and wv_raised and (not ep_raised),
    }


# --------------------------------------------------------------------- main


def main() -> int:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPO_ROOT / "results" / "store_verification" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    logger.add(report_dir / "verify.log", level="DEBUG")

    logger.info("store-verification start. report_dir={}", report_dir)

    # --- shared ChromaDB tree for tasks 1, 3, 4 (task 2 uses its own dir) ----
    main_persist = report_dir / "chroma_main"
    stores = _build_stores(main_persist)
    counts = _index_all_personas(stores)
    logger.info("indexed: {}", counts)

    # Task 1 — 27 hand-queries
    logger.info("[task 1] running 27 hand-queries")
    t0 = time.time()
    handqueries = task_handqueries(stores)
    handqueries_dt = time.time() - t0
    n_pass = sum(1 for r in handqueries if r.looks_right)
    n_top1 = sum(1 for r in handqueries if r.top1_correct)
    logger.info(
        "[task 1] {}/{} pass design contract (top-k for semantic, full set for identity); "
        "{}/{} also right at top-1 (took {:.2f}s)",
        n_pass,
        len(handqueries),
        n_top1,
        len(handqueries),
        handqueries_dt,
    )

    # Task 3 — bi-temporal
    logger.info("[task 3] bi-temporal filter on historian (1750 / 1950)")
    bi = task_bitemporal(stores)
    logger.info(
        "[task 3] 1750 valid_times={}, 1950 valid_times={}",
        bi["valid_times_1750"],
        bi["valid_times_1950"],
    )

    # Task 4 — write enforcement
    logger.info("[task 4] runtime-write enforcement")
    we = task_write_enforcement(stores)
    logger.info(
        "[task 4] self_facts_raised={}, worldview_raised={}, episodic_succeeded={}",
        we["self_facts_raised"],
        we["worldview_raised"],
        we["episodic_succeeded"],
    )

    # Drop the main client BEFORE running the persistence test (which spins up
    # its own client tree under report_dir/persistence_test). On macOS, two
    # PersistentClient handles to overlapping paths can collide; isolating
    # paths avoids any ambiguity.
    del stores

    # Task 2 — persistence
    logger.info("[task 2] persistence: index, drop client, reopen, re-query")
    pers = task_persistence(report_dir)
    logger.info("[task 2] ok={} same_counts={}", pers["ok"], pers["counts_match"])

    # ---------------------------------------------------------------- report
    raw = {
        "run_id": run_id,
        "indexed_counts": counts,
        "task1_handqueries": [
            {
                "persona_id": r.persona_id,
                "store": r.store,
                "query": r.query,
                "expected_substring": r.expected_substring,
                "top1_text": r.top1_text,
                "top1_distance": r.top1_distance,
                "looks_right": r.looks_right,
                "top1_correct": r.top1_correct,
                "top_k_correct": r.top_k_correct,
                "notes": r.notes,
            }
            for r in handqueries
        ],
        "task1_pass_count": n_pass,
        "task1_total": len(handqueries),
        "task1_top1_correct_count": sum(1 for r in handqueries if r.top1_correct),
        "task1_top_k_or_kindset_pass_count": sum(1 for r in handqueries if r.looks_right),
        "task2_persistence": pers,
        "task3_bitemporal": bi,
        "task4_write_enforcement": we,
    }
    (report_dir / "raw.json").write_text(
        json.dumps(raw, indent=2, sort_keys=False, default=str) + "\n",
        encoding="utf-8",
    )

    # --- human-readable report.md
    overall_ok = n_pass == len(handqueries) and pers["ok"] and bi["ok"] and we["ok"]
    lines: list[str] = []
    lines.append(
        f"# Typed-Memory-Store Verification — {'PASSED' if overall_ok else 'NEEDS REVIEW'}"
    )
    lines.append("")
    lines.append(f"- Run id: `{run_id}`")
    lines.append(f"- Indexed chunk counts (across 3 personas): `{counts}`")
    lines.append("")
    lines.append("## Task 1 — Hand-queries (27 queries: 3 personas x 3 stores x 3 queries)")
    lines.append("")
    n_top1 = sum(1 for r in handqueries if r.top1_correct)
    n_topk = sum(1 for r in handqueries if r.looks_right)
    lines.append(
        f"- **{n_topk}/{len(handqueries)}** queries pass the design contract "
        "(IdentityStore returns the full id+constraint set; semantic stores have "
        "the right chunk in top-k)."
    )
    lines.append(
        f"- **{n_top1}/{len(handqueries)}** queries also have the right chunk at top-1 "
        "(stricter; stores that return get_all count the same as their top-k pass)."
    )
    lines.append("")
    lines.append(
        "| Persona | Store | Query | Expected | Top-k OK? | Top-1 OK? | Top-1 (truncated) | Notes |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in handqueries:
        topk_mark = "OK" if r.looks_right else "FLAG"
        top1_mark = "OK" if r.top1_correct else ("FLAG" if r.top_k_correct is not None else "n/a")
        truncated = (r.top1_text or "(no result)").replace("|", "\\|").replace("\n", " ")[:80]
        notes = (r.notes or "").replace("|", "\\|")
        lines.append(
            f"| `{r.persona_id}` | `{r.store}` | {r.query} | `{r.expected_substring}` | {topk_mark} | {top1_mark} | {truncated} | {notes} |"
        )
    lines.append("")
    lines.append("## Task 2 — ChromaDB persistence")
    lines.append("")
    lines.append(f"- Counts match across reopen: **{pers['counts_match']}**")
    lines.append(f"- Top-3 ids match: **{pers['top3_ids_match']}**")
    lines.append(f"- Top-3 texts match: **{pers['top3_texts_match']}**")
    lines.append(f"- Verdict: **{'OK' if pers['ok'] else 'FAIL'}**")
    lines.append("")
    lines.append("## Task 3 — Bi-temporal filter on historian")
    lines.append("")
    lines.append("Question: *What was happening in this period?*")
    lines.append("")
    lines.append(
        f"- Unfiltered top-{len(bi['unfiltered'])} valid_times: {[h['valid_time'] for h in bi['unfiltered']]}"
    )
    lines.append(
        f"- `as_of=1750` valid_times: {bi['valid_times_1750']} — leaks: `{bi['leaks_1750']}`"
    )
    lines.append(
        f"- `as_of=1950` valid_times: {bi['valid_times_1950']} — leaks: `{bi['leaks_1950']}`"
    )
    lines.append(f"- Verdict: **{'OK' if bi['ok'] else 'FAIL'}**")
    lines.append("")
    lines.append("## Task 4 — Runtime-write enforcement")
    lines.append("")
    lines.append(
        f"- self_facts.write(...) raised RuntimeWriteForbiddenError: **{we['self_facts_raised']}**"
    )
    lines.append(
        f"- worldview.write(...) raised RuntimeWriteForbiddenError: **{we['worldview_raised']}**"
    )
    lines.append(f"- episodic.write(...) succeeded (control): **{we['episodic_succeeded']}**")
    lines.append(f"- Verdict: **{'OK' if we['ok'] else 'FAIL'}**")
    lines.append("")
    (report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info("store-verification complete: overall_ok={}", overall_ok)
    return 0 if overall_ok else 2


if __name__ == "__main__":
    sys.exit(main())
