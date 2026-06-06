"""T22b — operator smoke per format, terminal-driven (no frontend).

Composes a real backend (PERSONA_PROVIDER / PERSONA_API_KEY from .env) +
the in-memory DocumentStore + the PromptBuilder document extensions
(T14/T15/T16) + document_service.upload to ingest each fixture format,
then runs a real chat against the configured backend and verifies the
persona's answer references the document content.

This is the human-side T22b proof analogous to Spec 13's T16/T17 operator
smokes — written as a script (not pytest) because the .env-loaded
credentials path is the operator workflow, not a CI test.

Usage:
    cd /path/to/Open-Persona
    uv run python scripts/t22b_documents_smoke.py [format1] [format2] ...

    With no args, runs all formats. Records results to stdout; the operator
    pastes the result block into docs/specs/phase2/spec_14/state.md
    "Manual smoke results" table.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Load .env without exposing values (Spec 02 D-02-4 — opt-in dotenv).
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Don't overwrite explicit shell vars.
        os.environ.setdefault(key, value)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "core" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "runtime" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "api" / "src"))

from persona.backends import load_backend  # noqa: E402
from persona.backends.config import BackendConfig  # noqa: E402
from persona.schema.persona import Persona, PersonaIdentity  # noqa: E402
from persona.stores.document_store import DocumentStore  # noqa: E402

if TYPE_CHECKING:
    from persona.backends.protocol import ChatBackend
    from persona.schema.chunks import PersonaChunk
from persona_api.services import document_service  # noqa: E402
from persona_runtime.prompt import (  # noqa: E402
    DocumentContext,
    DocumentDescriptor,
    DocumentInjection,
    PromptBuilder,
    RetrievedContext,
)


@dataclass(frozen=True)
class _SmokeScenario:
    """One per-format smoke."""

    format_label: str
    filename: str
    file_bytes: bytes
    question: str
    expected_substrings: tuple[str, ...]
    """Substrings (any one of which counts as a hit) that prove the model
    saw the document content. Case-insensitive substring match."""


class _InMemoryBackend:
    """In-memory Backend stub mirroring the spec-07 transport Protocol."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], list[PersonaChunk]] = {}

    def upsert(
        self,
        *,
        persona_id: str,
        store_kind: str,
        chunks: list[PersonaChunk],
    ) -> None:
        key = (persona_id, store_kind)
        existing = self.store.setdefault(key, [])
        ids = {c.id for c in chunks}
        kept = [c for c in existing if c.id not in ids]
        kept.extend(chunks)
        self.store[key] = kept

    def query(
        self,
        *,
        persona_id: str,
        store_kind: str,
        text: str,
        top_k: int,
        where: dict[str, str] | None = None,  # noqa: ARG002 — Protocol compat
    ) -> list[PersonaChunk]:
        chunks = list(self.store.get((persona_id, store_kind), []))
        # Simple token-overlap "retrieval" — pick chunks with overlapping words.
        query_words = set(text.lower().split())
        scored: list[tuple[int, PersonaChunk]] = []
        for chunk in chunks:
            chunk_words = set(chunk.text.lower().split())
            overlap = len(query_words & chunk_words)
            scored.append((overlap, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _score, c in scored[:top_k]]

    def get_all(self, *, persona_id: str, store_kind: str) -> list[PersonaChunk]:
        return list(self.store.get((persona_id, store_kind), []))

    def delete_persona(self, persona_id: str, store_kind: str) -> None:
        self.store.pop((persona_id, store_kind), None)

    def delete_documents(
        self,
        *,
        persona_id: str,
        store_kind: str,
        ids: list[str],
    ) -> None:
        key = (persona_id, store_kind)
        self.store[key] = [c for c in self.store.get(key, []) if c.id not in set(ids)]


def _build_persona() -> Persona:
    return Persona(
        persona_id="astrid",
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy law assistant",
            background=(
                "Knows Norwegian tenancy law (husleieloven). "
                "Reads documents the user uploads carefully and answers based on their content."
            ),
            constraints=[
                "Answer concisely from the attached document content.",
                "Quote the document when stating specific facts.",
            ],
        ),
    )


def _build_scenarios() -> list[_SmokeScenario]:
    # Synthesized scenarios — small docs whose content has a clear answer
    # to the question. Each expected_substring is a low-ambiguity phrase.
    return [
        _SmokeScenario(
            format_label="txt",
            filename="lease_memo.txt",
            file_bytes=(
                b"Lease memo: The lease runs for 12 months from 1 January 2026. "
                b"Rent is NOK 12000 per month, paid in arrears on the first of each month. "
                b"Heating system maintenance is the landlord's responsibility under "
                b"husleieloven section 5-3."
            ),
            question="How long does the lease run for, and how much is the monthly rent?",
            expected_substrings=("12 months", "12000", "NOK", "twelve"),
        ),
        _SmokeScenario(
            format_label="md",
            filename="rent_terms.md",
            file_bytes=(
                b"# Rent Terms\n\n"
                b"## Monthly Rent\n\nMonthly rent is NOK 14500.\n\n"
                b"## Payment Schedule\n\nPaid quarterly in advance.\n\n"
                b"## Late Fees\n\nA late fee of NOK 500 applies after 7 days."
            ),
            question="What is the monthly rent and the late-fee policy?",
            expected_substrings=("14500", "NOK", "late fee", "500"),
        ),
        _SmokeScenario(
            format_label="csv",
            filename="payments.csv",
            file_bytes=(
                b"date,description,amount\n"
                b"2026-01-01,Rent January,12000\n"
                b"2026-02-01,Rent February,12000\n"
                b"2026-03-15,Maintenance,3500\n"
                b"2026-03-01,Rent March,12000\n"
            ),
            question="What was the maintenance charge and on what date?",
            expected_substrings=("3500", "2026-03-15", "Maintenance"),
        ),
        _SmokeScenario(
            format_label="py",
            filename="calculator.py",
            file_bytes=(
                b'"""Rent calculator utilities."""\n\n'
                b"def calculate_monthly_rent(annual_rent: int, months: int = 12) -> float:\n"
                b'    """Return the monthly rent from an annual amount."""\n'
                b"    if months <= 0:\n"
                b'        raise ValueError("months must be positive")\n'
                b"    return annual_rent / months\n"
            ),
            question=(
                "What does the calculate_monthly_rent function do and what does it "
                "raise when months is zero or negative?"
            ),
            expected_substrings=(
                "monthly rent",
                "annual",
                "ValueError",
                "positive",
            ),
        ),
    ]


def _build_doc_scenarios_with_real_fixtures() -> list[_SmokeScenario]:
    """Scenarios for the formats whose parsers need binary fixtures."""
    fixtures = REPO_ROOT / "packages" / "core" / "tests" / "fixtures" / "documents"
    sample_pdf = fixtures / "sample-text.pdf"
    sample_docx = fixtures / "sample.docx"
    sample_xlsx = fixtures / "sample.xlsx"
    scanned_pdf = fixtures / "scanned-like.pdf"

    return [
        _SmokeScenario(
            format_label="docx",
            filename="tenancy_memo.docx",
            file_bytes=sample_docx.read_bytes(),
            question=(
                "What is in the Tenancy Memo document, specifically about the lease term and rent?"
            ),
            expected_substrings=("twelve months", "12,000", "NOK", "January"),
        ),
        _SmokeScenario(
            format_label="xlsx",
            filename="quarterly_revenue.xlsx",
            file_bytes=sample_xlsx.read_bytes(),
            question="What sheets are in this workbook and what data do they contain?",
            expected_substrings=("Q1", "Q2", "revenue", "month"),
        ),
        _SmokeScenario(
            format_label="pdf-text",
            filename="tenancy_extract.pdf",
            file_bytes=sample_pdf.read_bytes(),
            question="What does this PDF say about the lease and rent?",
            expected_substrings=("twelve months", "12000", "January", "arrears"),
        ),
        _SmokeScenario(
            format_label="pdf-scanned",
            filename="scanned_lease.pdf",
            file_bytes=scanned_pdf.read_bytes(),
            question=("This PDF was uploaded as a scanned document. What do the pages show?"),
            # For a scanned PDF, the runtime would route to a vision tier.
            # The smoke verifies the upload SUCCEEDS via T21's rasterisation path
            # (not the chat response — that needs vision-tier routing wired into
            # the runtime, which Spec 13's T09 handles at the conversation loop
            # level, beyond document_service's scope).
            expected_substrings=("vision_handoff",),
        ),
    ]


async def _run_chat_for_scenario(
    *,
    scenario: _SmokeScenario,
    sandbox_root: Path,
    document_store: DocumentStore,
    persona: Persona,
    builder: PromptBuilder,
    backend: ChatBackend,
    max_tokens: int,
) -> dict[str, Any]:
    """Upload + ingest + build prompt + chat + verify. Returns a result dict."""

    started = time.monotonic()
    error: str | None = None
    answer: str = ""
    upload_strategy: str | None = None
    upload_doc_ref: str | None = None
    images_count: int = 0

    try:
        # Upload + ingest.
        ref = document_service.upload(
            sandbox_root=sandbox_root,
            persona_id=persona.persona_id,
            conversation_id="smoke-conv",
            file_bytes=scenario.file_bytes,
            filename=scenario.filename,
            document_store=document_store,
        )
        upload_strategy = ref.strategy.value
        upload_doc_ref = ref.doc_ref
        images_count = len(ref.images)

        # Special-case: scanned PDF — the chat side requires vision-tier
        # routing wired at the conversation-loop level (Spec 13 T09). For
        # T22b's document-level proof, the scanned-PDF success criterion
        # is "upload returns strategy=vision_handoff with rasterised images";
        # the runtime-level vision dispatch is Spec 13's responsibility.
        if upload_strategy == "vision_handoff":
            answer = (
                f"[skip-chat] vision_handoff strategy returned with "
                f"{images_count} rasterised page image(s); "
                "runtime vision-tier dispatch is Spec 13 T09's territory."
            )
        else:
            # Build a DocumentContext.
            attached = (
                DocumentDescriptor(
                    title=ref.title,
                    format=ref.format,
                    page_count=ref.page_count,
                    sheet_names=ref.sheet_names,
                    size_bytes=ref.size_bytes,
                ),
            )

            if upload_strategy == "whole_inject":
                full_text = document_service.get_document_text(
                    sandbox_root=sandbox_root,
                    persona_id=persona.persona_id,
                    conversation_id="smoke-conv",
                    doc_ref=ref.doc_ref,
                )
                doc_ctx = DocumentContext(
                    attached_documents=attached,
                    whole_inject_docs=(
                        DocumentInjection(
                            title=ref.title,
                            format=ref.format,
                            full_text=full_text,
                        ),
                    ),
                )
            else:
                # RETRIEVAL — query the store using the question.
                retrieved = document_store.query("smoke-conv", scenario.question, top_k=5)
                doc_ctx = DocumentContext(
                    attached_documents=attached,
                    retrieved_chunks=tuple(retrieved),
                )

            messages = builder.build(
                persona,
                RetrievedContext(),
                history=[],
                skill_index="",
                user_message=scenario.question,
                max_tokens=max_tokens,
                document_context=doc_ctx,
            )

            response = await backend.chat(messages)
            answer = response.content

    except Exception as exc:  # noqa: BLE001 — operator-smoke summary
        error = f"{type(exc).__name__}: {exc}"

    elapsed = time.monotonic() - started

    # Verification.
    answer_lower = answer.lower()
    hits = [s for s in scenario.expected_substrings if s.lower() in answer_lower]
    passed = bool(hits) and error is None

    return {
        "format": scenario.format_label,
        "filename": scenario.filename,
        "passed": passed,
        "upload_strategy": upload_strategy,
        "doc_ref": upload_doc_ref,
        "images_count": images_count,
        "expected_substrings": list(scenario.expected_substrings),
        "matched_substrings": hits,
        "answer_excerpt": answer[:300] + ("…" if len(answer) > 300 else ""),
        "error": error,
        "elapsed_s": round(elapsed, 2),
    }


async def _main() -> int:
    persona = _build_persona()
    builder = PromptBuilder()

    # Tier preference: mid-tier (DeepSeek per D-11-9 demo-primary). Fall back
    # to frontier if mid isn't configured.
    provider_prefix = "PERSONA_MID_"
    if not os.environ.get(provider_prefix + "PROVIDER"):
        provider_prefix = "PERSONA_FRONTIER_"
    if not os.environ.get(provider_prefix + "PROVIDER"):
        provider_prefix = "PERSONA_"

    backend_label = (
        f"{os.environ.get(provider_prefix + 'PROVIDER', '?')} / "
        f"{os.environ.get(provider_prefix + 'MODEL', 'default')} "
        f"(prefix={provider_prefix})"
    )

    config = BackendConfig.from_env(prefix=provider_prefix)
    max_tokens = getattr(config, "max_tokens", 4096)
    backend = load_backend(config)

    print("=== T22b document smoke ===")
    print(f"backend: {backend_label}")
    print(f"max_tokens: {max_tokens}")
    print()

    requested = set(sys.argv[1:])
    all_scenarios = _build_scenarios() + _build_doc_scenarios_with_real_fixtures()
    scenarios = (
        [s for s in all_scenarios if s.format_label in requested] if requested else all_scenarios
    )

    sandbox_root = REPO_ROOT / ".tmp_smoke_workspace"
    if sandbox_root.exists():
        import shutil

        shutil.rmtree(sandbox_root)
    sandbox_root.mkdir()

    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        # Fresh DocumentStore per scenario so attachments don't bleed.
        store = DocumentStore(backend=_InMemoryBackend())  # type: ignore[arg-type]
        # Fresh conversation workspace path.
        for child in sandbox_root.iterdir():
            import shutil

            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        print(f"--- {scenario.format_label} ({scenario.filename}) ---")
        result = await _run_chat_for_scenario(
            scenario=scenario,
            sandbox_root=sandbox_root,
            document_store=store,
            persona=persona,
            builder=builder,
            backend=backend,
            max_tokens=max_tokens,
        )
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(
            f"  [{status}] strategy={result['upload_strategy']} "
            f"images={result['images_count']} "
            f"hits={result['matched_substrings']} "
            f"elapsed={result['elapsed_s']}s"
        )
        if result["error"]:
            print(f"  error: {result['error']}")
        print(f"  answer: {result['answer_excerpt']}")
        print()

    # Try to close the backend cleanly.
    aclose = getattr(backend, "aclose", None)
    if aclose is not None:
        await aclose()

    print("=== summary ===")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(
            f"  {status:4}  {r['format']:12}  strategy={r['upload_strategy']:18}"
            f"  hits={len(r['matched_substrings']):>2}/{len(r['expected_substrings'])}"
            f"  {r['elapsed_s']}s"
        )

    failed = sum(1 for r in results if not r["passed"])
    print()
    print(
        f"{len(results) - failed}/{len(results)} scenarios PASSED"
        + (f" ({failed} FAILED)" if failed else "")
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
