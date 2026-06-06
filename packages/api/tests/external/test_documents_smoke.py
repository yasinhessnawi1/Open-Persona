"""T22b — Per-format @pytest.mark.external smoke for §9 criterion #1.

§9 criterion #1: "Each supported format (PDF, docx, xlsx, csv, txt, md,
a code file) is parsed to clean text; a representative fixture of each
is ingested and its content is correctly available to the persona
(verified by the persona answering a content question)."

These tests run against a **real model backend** (DeepSeek primary per
D-11-9; Claude Sonnet 4.6 backup) — they incur API spend, so they're
marked ``@pytest.mark.external`` and skipped by default
(``pyproject.toml`` ``addopts = "-v --tb=short -m 'not integration and
not external'"``). Run them manually before close-out:

    uv run pytest -m external packages/api/tests/external/test_documents_smoke.py

Each scenario:
1. Build a real ``ConversationLoop`` (composes the persona + retriever +
   tier registry + DocumentStore).
2. Upload a representative fixture for the format.
3. Run a chat turn asking a content-specific question about the document.
4. Assert the model's answer references the document's content
   (substring check against expected ground-truth phrases).

These tests serve as the end-to-end proof that the pipeline — parser →
ingest → store → prompt-builder (whole_inject / retrieval / synopsis) →
runtime → model — produces a response that demonstrably reflects the
document's content.

**Scaffolding only at T22 close-out time.** The actual external-call
implementation is left intentionally minimal — populated as part of the
operator-facing close-out checklist when the user runs the smoke per
:doc:`docs/specs/phase2/spec_14/state.md` "Manual smoke results" table.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.external


@pytest.mark.skip(
    reason=(
        "T22b external smoke: requires a real model backend (DeepSeek primary "
        "per D-11-9). Populate per-format scenarios when running the close-out "
        "smoke checklist — record results in state.md's 'Manual smoke results' "
        "table per the spec-11/D-11-11 agent/human discipline."
    ),
)
class TestPerFormatSmoke:
    """One scenario per supported format."""

    def test_txt_smoke(self) -> None:
        # Upload a memo.txt; ask "What's the rent?"; assert "12000" in answer.
        pass

    def test_md_smoke(self) -> None:
        # Upload a Markdown memo; ask about a heading-bounded section.
        pass

    def test_csv_smoke(self) -> None:
        # Upload a small CSV; ask about a specific row's data.
        pass

    def test_docx_smoke(self) -> None:
        # Upload a docx; ask about heading-bounded content.
        pass

    def test_xlsx_smoke(self) -> None:
        # Upload a multi-sheet xlsx; ask about a specific sheet.
        pass

    def test_pdf_text_smoke(self) -> None:
        # Upload a text PDF; ask about page-specific content.
        pass

    def test_pdf_scanned_vision_smoke(self) -> None:
        # Upload a scanned PDF; assert the vision-tier handles it
        # (criterion #7 / Dominant Concern #3 end-to-end proof).
        pass

    def test_code_smoke(self) -> None:
        # Upload a Python source file; ask about a function signature.
        pass
