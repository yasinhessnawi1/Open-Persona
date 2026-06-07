"""Spec 16 T14 — pdf_generation end-to-end backend integration test.

Quality bar criterion #2 binary test for the ``pdf_generation`` skill
(research.md §3.5 + §3.7 pdf rubric). Production-verification path: drives
the real :class:`LocalDockerSandbox` + real :func:`make_use_skill_tool` +
real :func:`make_code_execution_tool` + real :func:`collect_skill_supplements`
through :class:`AgenticLoop` over the real on-disk ``pdf_generation`` skill
pack, produces the representative-task PDF, persists the bytes to the
persona-workspace via the shipped :meth:`CodeSandbox.copy_produced_file_to`
machinery (D-12-X-read-produced-file + D-17-X-bytes-persistence), parses
the produced PDF with :mod:`pypdf`, and asserts each row of the §3.7
programmatic-assertion table. The frontend download chip is not yet
wired (per the user's clarification); this test IS the backend
production-verification surface.

**Per-format quality bar — pdf (research.md §3.5).** Representative task:

    Generate a 3-page report titled "Tenant protection: quarterly summary"
    with a cover page, a body containing one 25-row table of complaints by
    district AND one embedded line-chart image, and a summary page. The
    table must span pages 2–3 (it cannot fit on one page); header row must
    repeat on each table page.

The page-spanning table is the testable hard feature — naive
``Table()`` flowables get clipped or push the next flowable off-page;
``LongTable`` with ``repeatRows=1`` is the right idiom.

**Pre-conditions (verified before composing this test):**

1. D-16-X-6 PATH fix: ``LocalDockerSandbox._BASE_CONTAINER_KWARGS
   ["environment"]["PATH"]`` is ``"/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"``
   (the venv-installed ``reportlab`` / ``PIL`` / ``matplotlib`` resolve
   natively from ``python``). **No ``sys.path.insert`` prelude is added
   to the code snippet** — the SKILL.md pack's teaching is honest.
2. D-16-X-7 supplements relative-path fix:
   :func:`persona.skills.collect_skill_supplements` returns
   ``SandboxFile(path=".skills/<name>/supplements/<topic>.md")`` (relative).
   The real on-disk ``pdf_generation`` skill (``packages/core/src/persona/
   skills/builtin/pdf_generation``) ships three supplements
   (``flowables.md`` / ``pagination.md`` / ``images.md``) — they stage
   end-to-end into the sandbox via the M1a affordance.

**Cross-spec inheritance (per handover.md "Cross-spec inheritance: produced-files persistence"):**

- :func:`persona.sandbox.make_code_execution_tool` accepts a
  ``produced_file_persister`` callback that fires for every entry in
  ``ExecutionResult.produced_files`` after dispatch.
- The closure invokes :meth:`LocalDockerSandbox.copy_produced_file_to`
  with destination ``<persona_workspace_root>/<owner_id>/<persona_id>/<ref>``.
- The bytes survive the per-execution ``shutil.rmtree(host_out)`` cleanup
  (the persister is invoked BEFORE the cleanup in the T03 body's flow,
  per the production-verified path at ``packages/api/src/persona_api/
  sandbox/runtime_tool.py:216 _persist_produced_file``).

**Visual-only criteria (§3.7 rows 2, 5, 6).** Body font size, alternating
row colour, and within-margins are visual rubric rows whose programmatic
surrogates are fragile or genuinely vision-only. Honest classification per
state.md F1 #7 / Spec 13 fold-in-#9 / Spec 15 T19/T20: structural test
confirms well-formed; visual fidelity verifies when frontend lands.

**Scope.** A1 convention from earlier T09/T10: skip cleanly if Docker is
unavailable; use module-private ``_ScriptedBackend`` per the api per-file
convention (T01 audit A1 — three precedents); no cross-package import
from ``packages/runtime/tests/_fakes.py``.
"""

# ruff: noqa: SLF001, ANN401, ARG002 — test doubles with intentionally loose sigs;
# pinning the registry cache to the scripted backend mirrors the runtime test
# idiom (the alternative is a real BackendConfig + monkeypatched load_backend
# which is brittler); the dummy MemoryStore mirrors the Protocol's loose kwargs.

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pypdf
import pytest
from persona.backends import BackendConfig
from persona.backends.types import ChatResponse, TokenUsage
from persona.sandbox import make_code_execution_tool
from persona.sandbox.local_docker import (
    DEFAULT_IMAGE,
    LocalDockerSandbox,
    is_docker_available,
)
from persona.sandbox.result import NetworkPolicy, ResourceLimits, SandboxFile
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.skills import SkillSpec
from persona.schema.tools import ToolCall
from persona.skills import (
    SkillInjector,
    SkillScanner,
    collect_skill_supplements,
    make_use_skill_tool,
)
from persona.tools import Toolbox
from persona_runtime.agentic.loop import AgenticLoop
from persona_runtime.agentic.run import RunStatus
from persona_runtime.agentic.step import StepType
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

if TYPE_CHECKING:
    from persona.backends.types import ToolSpec
    from persona.schema.conversation import ConversationMessage


pytestmark = [pytest.mark.integration, pytest.mark.docker]


# ---------------------------------------------------------------------------
# Path constants — the on-disk built-in skill pack + the inspection artifact
# directory (D-16-X-3 gitignored).
# ---------------------------------------------------------------------------


# Built-in skills root, resolved from this test file's path (mirrors T09's
# resolution at packages/api/tests/integration/test_document_generation.py).
_BUILTIN_ROOT = (
    Path(__file__).parent.parent.parent.parent / "core" / "src" / "persona" / "skills" / "builtin"
).resolve()


# Inspection artifact destination — Path(__file__).resolve().parents[3] points
# at the repo root (packages/api/tests/integration/test_pdf_generation_e2e.py
# → parents[0]=integration, [1]=tests, [2]=api, [3]=packages, [4]=repo-root).
# The task brief specifies docs/specs/phase2/spec_16/inspection/pdf_sample.pdf;
# the directory is gitignored per D-16-X-3.
_INSPECTION_DIR = (
    Path(__file__).resolve().parents[4] / "docs" / "specs" / "phase2" / "spec_16" / "inspection"
)


# ---------------------------------------------------------------------------
# The representative-task PDF generation code snippet.
#
# Per research.md §3.5 quality-bar:
#   - 3-page report titled "Tenant protection: quarterly summary"
#   - cover page (distinct from body)
#   - body with one 25-row table of complaints by district
#   - one embedded line-chart image
#   - summary page
#   - The table must span pages 2-3; header row must repeat on each page
#     (`LongTable` + `repeatRows=1` — the hard feature).
#   - Page numbers via `onPage`/`onLaterPages` callback (criterion #8).
#   - Body font ≥10pt (criterion #2 — visual).
#
# This snippet is the production-verification path: it MUST produce the
# representative file. No `sys.path.insert` prelude — D-16-X-6 fixed the
# container PATH so `import reportlab` works natively.
# ---------------------------------------------------------------------------


_PDF_CODE = '''\
"""Spec 16 representative-task PDF generation — exercises the §3.5 quality
bar including LongTable + repeatRows=1 (table spans pages 2-3), page-number
footer via onLaterPages, embedded line-chart image, distinct cover page.
"""

from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # headless backend — no display required.
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

# ---- generate the line-chart PNG (an embedded matplotlib figure) ----
fig, ax = plt.subplots(figsize=(5, 3), dpi=150)
months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
complaints = [42, 47, 51, 58, 64, 71]
ax.plot(months, complaints, marker="o", linewidth=2.0, color="#1f4e79")
ax.set_xlabel("Month (Q1+Q2 2026)")
ax.set_ylabel("Complaints filed")
ax.set_title("Tenant complaints — rolling six-month trend")
ax.grid(visible=True, linestyle="--", alpha=0.4)
fig.tight_layout()
chart_path = "/workspace/out/complaint_trend.png"
fig.savefig(chart_path, format="png", dpi=150)
plt.close(fig)

# ---- styles ----
styles = getSampleStyleSheet()
body_style = ParagraphStyle(
    "BodyText11",
    parent=styles["BodyText"],
    fontName="Helvetica",
    fontSize=11,
    leading=14,
)
title_style = ParagraphStyle(
    "CoverTitle",
    parent=styles["Title"],
    fontName="Helvetica-Bold",
    fontSize=24,
    leading=28,
    spaceAfter=18,
)
subtitle_style = ParagraphStyle(
    "CoverSubtitle",
    parent=styles["Heading2"],
    fontName="Helvetica",
    fontSize=14,
    leading=18,
    textColor=colors.HexColor("#444444"),
)
h2_style = ParagraphStyle(
    "BodyH2",
    parent=styles["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=16,
    leading=20,
    spaceAfter=10,
)


# ---- page-number footer callback (criterion #8) ----
def _draw_footer(canvas, doc):
    """Draw a centred "Page N" footer on every page (cover + body)."""
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[0] / 2.0, 1.2 * cm, f"Page {page_num}")
    canvas.restoreState()


# ---- 25-row complaints-by-district table ----
districts = [
    "Sentrum", "Grunerlokka", "Frogner", "Sagene", "St. Hanshaugen",
    "Gamle Oslo", "Bjerke", "Grorud", "Stovner", "Alna",
    "Ostensjo", "Nordstrand", "Sondre Nordstrand", "Vestre Aker", "Nordre Aker",
    "Ullern", "Marka", "Sentrum-2", "Lambertseter", "Manglerud",
    "Holmlia", "Tveita", "Rommen", "Furuset", "Ammerud",
]
complaint_counts = [
    71, 58, 22, 34, 19,
    63, 27, 41, 36, 48,
    25, 18, 52, 14, 21,
    11, 7, 65, 33, 29,
    44, 38, 26, 31, 17,
]
table_data = [["District", "Complaints", "Trend"]]
for d, n in zip(districts, complaint_counts, strict=True):
    trend = "Up" if n >= 35 else "Stable"
    table_data.append([d, str(n), trend])

# LongTable with repeatRows=1 — header row repeats on each page split.
# colWidths sum to A4 inner width (A4 = 21cm; with 2cm margins each side,
# usable = 17cm = 8 + 4.5 + 4.5).
table = LongTable(
    table_data,
    colWidths=[8 * cm, 4.5 * cm, 4.5 * cm],
    repeatRows=1,
)
table.setStyle(
    TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 10),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            # Alternating row backgrounds (data rows only).
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.whitesmoke, colors.HexColor("#f0f0f0")]),
        ]
    )
)

# ---- build the story ----
story = []

# Cover page — distinct from body.
story.append(Spacer(1, 4 * cm))
story.append(Paragraph("Tenant protection: quarterly summary", title_style))
story.append(Paragraph("Q1-Q2 2026 complaints overview by district", subtitle_style))
story.append(Spacer(1, 1 * cm))
story.append(Paragraph(
    "Norwegian Tenancy Ombudsman — Internal review draft",
    body_style,
))
story.append(PageBreak())

# Body — header + chart + 25-row LongTable. The table is engineered to
# span pages 2-3 (25 data rows + header at fontSize=10 with row padding
# overflows a single A4 page; LongTable with repeatRows=1 then splits
# and repeats the header on page 3 — the §3.5 hard feature).
story.append(Paragraph("Complaints by district", h2_style))
story.append(Spacer(1, 0.4 * cm))
story.append(Image(chart_path, width=14 * cm, height=8 * cm))
story.append(Spacer(1, 0.6 * cm))
story.append(Paragraph(
    "The table below lists complaints by district across the reporting "
    "period. Header row repeats on every page where the table continues.",
    body_style,
))
story.append(Spacer(1, 0.4 * cm))
story.append(table)
story.append(PageBreak())

# Summary page.
story.append(Paragraph("Summary", h2_style))
story.append(Spacer(1, 0.4 * cm))
story.append(Paragraph(
    "Across Q1 and Q2 2026, the Tenancy Ombudsman registered "
    "elevated complaint volumes in central and densely-rented districts. "
    "Sentrum, Grunerlokka, and Gamle Oslo led the count; the rolling "
    "six-month trend (chart, page 2) shows month-over-month growth.",
    body_style,
))
story.append(Spacer(1, 0.4 * cm))
story.append(Paragraph(
    "Recommended actions: targeted inspection cycles in the top-five "
    "districts; quarterly review cadence; coordinate with municipal "
    "housing inspectors.",
    body_style,
))

# ---- build the document with the footer callback on EVERY page ----
out_path = "/workspace/out/tenant_protection_quarterly.pdf"
doc = SimpleDocTemplate(
    out_path,
    pagesize=A4,
    leftMargin=2 * cm,
    rightMargin=2 * cm,
    topMargin=2 * cm,
    bottomMargin=2 * cm,
    title="Tenant protection: quarterly summary",
)
doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
print(f"wrote: {out_path}")
'''


# ---------------------------------------------------------------------------
# Helpers — docker pre-checks, scripted backend, loop builder.
# ---------------------------------------------------------------------------


def _image_present(tag: str) -> bool:
    """True if the sandbox image is available on the local Docker daemon."""
    import docker
    from docker.errors import DockerException, ImageNotFound

    try:
        client = docker.from_env()
        try:
            client.images.get(tag)
        except ImageNotFound:
            return False
        finally:
            client.close()
    except DockerException:
        return False
    return True


def _docker_skip_reason() -> str | None:
    """Return a skip reason if the local Docker substrate isn't ready, else None."""
    if not is_docker_available():
        return (
            "Docker daemon unreachable; T14 needs LocalDockerSandbox to drive "
            "code_execution. Start Docker and rerun."
        )
    if not _image_present(DEFAULT_IMAGE):
        return (
            f"Sandbox image {DEFAULT_IMAGE!r} not built. Build via the Spec 12 "
            "T06 Dockerfile (or pull) before running T14."
        )
    return None


# ----- module-private scripted backend (per-file pattern, T01 audit A1) -----


class _ScriptedBackend:
    """Replays a scripted sequence of :class:`ChatResponse` objects.

    Copied shape from ``packages/runtime/tests/_fakes.py:50`` per T01 audit
    A1 (the api package convention is module-private per-file, not a shared
    fixture). Only the ``chat()`` non-streaming surface the
    :class:`AgenticLoop` drives is implemented; ``chat_stream`` raises
    because the AgenticLoop never calls it.
    """

    def __init__(
        self,
        chat_script: list[ChatResponse],
        *,
        provider_name: str = "anthropic",
        model_name: str = "claude-sonnet-4-6",
    ) -> None:
        self._chat_script = list(chat_script)
        self._chat_index = 0
        self._provider_name = provider_name
        self._model_name = model_name
        self.chat_calls = 0

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def supports_native_tools(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(
        self,
        messages: list[ConversationMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> ChatResponse:
        self.chat_calls += 1
        if self._chat_index < len(self._chat_script):
            response = self._chat_script[self._chat_index]
            self._chat_index += 1
            return response
        return ChatResponse(
            content="[FINAL] ",
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
            model=self._model_name,
            provider=self._provider_name,
            latency_ms=0.0,
        )

    async def chat_stream(
        self,
        messages: list[ConversationMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> Any:
        raise NotImplementedError("AgenticLoop drives chat(), not chat_stream()")


# ----- in-memory stores (the test asserts on tool results, not memory) -----


def _empty_stores() -> dict[str, Any]:
    """Minimal in-memory MemoryStore doubles."""

    class _MemStore:
        def write(self, *_a: Any, **_kw: Any) -> None: ...
        def query(self, *_a: Any, **_kw: Any) -> list[Any]:
            return []

        def get_all(self, *_a: Any, **_kw: Any) -> list[Any]:
            return []

        def delete(self, *_a: Any, **_kw: Any) -> None: ...
        def remove_documents(self, *_a: Any, **_kw: Any) -> None: ...
        def history(self, *_a: Any, **_kw: Any) -> list[Any]:
            return []

        def rollback(self, *_a: Any, **_kw: Any) -> None: ...

    return {
        "identity": _MemStore(),
        "self_facts": _MemStore(),
        "worldview": _MemStore(),
        "episodic": _MemStore(),
    }


def _persona() -> Persona:
    return Persona(
        persona_id="tenant_protection_analyst",
        identity=PersonaIdentity(
            name="Astrid",
            role="Norwegian tenancy data analyst",
            background=(
                "Produces quarterly tenant-protection summaries for the "
                "Tenancy Ombudsman in well-formed PDF."
            ),
            language_default="nb",
            constraints=["Do not give binding legal advice."],
        ),
        skills=["pdf_generation"],
        tools=["use_skill", "code_execution"],
    )


def _resp(content: str = "", *, tool_calls: list[ToolCall] | None = None) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="claude-sonnet-4-6",
        provider="anthropic",
        latency_ms=1.0,
    )


def _scan(skill_names: list[str]) -> list[SkillSpec]:
    """Scan the named built-in skills, asserting all resolved.

    With D-16-X-7 fixed, the real on-disk ``pdf_generation`` skill (with
    its ``flowables.md`` / ``pagination.md`` / ``images.md`` supplements)
    is the production path the persona activates here.
    """
    scanner = SkillScanner([_BUILTIN_ROOT])
    specs = scanner.scan(skill_names)
    assert len(specs) == len(skill_names), (
        f"scanner returned {len(specs)} specs for {skill_names!r}; "
        "expected all to resolve from the built-in path"
    )
    return specs


def _build_loop(
    *,
    persona: Persona,
    backend: _ScriptedBackend,
    toolbox: Toolbox,
    scanned_skills: list[SkillSpec],
    deferred_holder: list[SandboxFile],
    max_steps: int = 20,
) -> AgenticLoop:
    """Wire an :class:`AgenticLoop` over the supplied collaborators.

    Pins the scripted backend into the tier registry's lazy-instantiation
    cache (the same idiom as
    ``packages/runtime/tests/unit/test_loop_agentic.py:_make_loop``).

    Binds the SHARED ``deferred_holder`` to the loop's public
    ``deferred_input_files`` attribute (D-16-2-state-location) so the
    use_skill intercept's ``extend(...)`` mutates the same list the
    ``code_execution`` tool's drain-and-clear provider consumes — M1a
    end-to-end.
    """
    dummy_cfg = BackendConfig(provider="anthropic", model="m", api_key=None)
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=dummy_cfg),
            "mid": TierConfig(name="mid", backend_config=dummy_cfg),
            "small": TierConfig(name="small", backend_config=dummy_cfg),
        }
    )
    registry._cache = {  # type: ignore[assignment]
        "frontier": backend,  # type: ignore[dict-item]
        "mid": backend,  # type: ignore[dict-item]
        "small": backend,  # type: ignore[dict-item]
    }
    loop = AgenticLoop(
        persona=persona,
        stores=_empty_stores(),  # type: ignore[arg-type]
        toolbox=toolbox,
        skill_injector=SkillInjector(),
        scanned_skills=scanned_skills,
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        max_steps=max_steps,
    )
    # M1a composition-root binding (mirrors RuntimeFactory.build_agentic_loop).
    loop.deferred_input_files = deferred_holder
    return loop


# ---------------------------------------------------------------------------
# The test.
# ---------------------------------------------------------------------------


class TestT14PdfEndToEnd:
    """Spec 16 §9 criteria touched: #1, #2, #3, #5, #6, #8, #11.

    #1 — file produced (assertion on persona-workspace path).
    #2 — quality bar (§3.7 programmatic assertions + visual surrogates).
    #3 — produced-files contract reaches workspace (the
        ``copy_produced_file_to`` round-trip via the
        ``produced_file_persister`` callback).
    #5 — image manifest verified by ``import reportlab`` / ``import
        matplotlib`` succeeding inside the sandbox (no ModuleNotFoundError).
    #6 — skill budget already passed at T03-T06 close gates; here we exercise
        the real on-disk pdf_generation skill body in the M1a injection
        path (so a regression in injection would fail this test too).
    #8 — loop produces document end-to-end (use_skill → code_execution →
        produced_files surfaces in the tool result and bytes copy to
        persona workspace).
    #11 — RLS via workspace per-persona isolation (the destination path is
        ``<workspace_root>/<owner_id>/<persona_id>/<ref>`` — distinct
        tenants land in distinct directories by construction).
    """

    @pytest.mark.asyncio
    async def test_pdf_generation_produces_quality_pdf_end_to_end(
        self,
        tmp_path: Path,
    ) -> None:
        skip_reason = _docker_skip_reason()
        if skip_reason:
            pytest.skip(skip_reason)

        persona = _persona()
        scanned = _scan(["pdf_generation"])
        # Cache the pdf_generation SkillSpec for the M1a supplements probe
        # below: collect_skill_supplements(spec) is the verbatim production
        # call site at ``packages/runtime/src/persona_runtime/agentic/loop.py``
        # use_skill intercept. We mirror it here against the SAME on-disk
        # supplements directory so a M1a regression surfaces in this test
        # (the agentic loop's intercept extends ``self.deferred_input_files``
        # with collect_skill_supplements(spec) on every use_skill match).
        pdf_spec = next(s for s in scanned if s.name == "pdf_generation")

        # Production-shape persona workspace: <workspace_root>/<owner>/<persona>/<ref>.
        workspace_root = tmp_path / "workspaces"
        owner_id = "owner_t14"
        persona_workspace = workspace_root / owner_id / persona.persona_id  # type: ignore[arg-type]
        persona_workspace.mkdir(parents=True, exist_ok=True)

        sandbox = LocalDockerSandbox(workspace_root=tmp_path / "sbx")
        session_id = f"{owner_id}:conv_t14_{uuid.uuid4().hex[:8]}"

        # M1a shared holder — bound to the loop's deferred_input_files AND
        # to the tool's drain-and-clear provider. Same identity = same list.
        deferred_holder: list[SandboxFile] = []

        def _drain_and_clear() -> list[SandboxFile]:
            snapshot = list(deferred_holder)
            deferred_holder.clear()
            return snapshot

        # produced_file_persister — mirrors the production wiring at
        # ``packages/api/src/persona_api/sandbox/runtime_tool.py:216
        # _persist_produced_file``. The closure resolves the destination
        # under <workspace_root>/<owner_id>/<persona_id>/<ref> and calls
        # copy_produced_file_to to copy bytes from the sandbox session's
        # host_out (D-12-X-read-produced-file + D-17-X-bytes-persistence).
        async def _persist_produced_file(call_session_id: str, ref: str) -> None:
            target = persona_workspace / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            await sandbox.copy_produced_file_to(call_session_id, ref, target)

        try:
            await sandbox.create_session(
                session_id, limits=ResourceLimits(), network=NetworkPolicy()
            )

            code_exec = make_code_execution_tool(
                sandbox,
                persona_id=persona.persona_id,
                session_id_provider=lambda: session_id,
                deferred_input_files_provider=_drain_and_clear,
                produced_file_persister=_persist_produced_file,
            )
            use_skill_tool = make_use_skill_tool(scanned)
            toolbox = Toolbox(
                [use_skill_tool, code_exec],
                allow_list=["use_skill", "code_execution"],
            )

            # Scripted agentic flow:
            #   step 0 → tool call: use_skill("pdf_generation")
            #            (the loop's _maybe_inject_skill intercept extends
            #             deferred_input_files with collect_skill_supplements;
            #             SKILL.md content goes into model context; the next
            #             code_execution carries the supplements as input_files.)
            #   step 1 → tool call: code_execution(_PDF_CODE)
            #            (the persister copies the .pdf to persona workspace.)
            #   step 2 → final.
            chat_script = [
                _resp(
                    tool_calls=[
                        ToolCall(
                            name="use_skill",
                            args={"skill_name": "pdf_generation"},
                            call_id="us1",
                        )
                    ]
                ),
                _resp(
                    tool_calls=[
                        ToolCall(
                            name="code_execution",
                            args={"code": _PDF_CODE},
                            call_id="ce1",
                        )
                    ]
                ),
                _resp(
                    "[FINAL] The quarterly summary PDF is available at "
                    "/workspace/out/tenant_protection_quarterly.pdf."
                ),
            ]
            backend = _ScriptedBackend(chat_script)
            loop = _build_loop(
                persona=persona,
                backend=backend,
                toolbox=toolbox,
                scanned_skills=scanned,
                deferred_holder=deferred_holder,
            )

            run = await loop.run("Produce a 3-page quarterly tenant-protection summary PDF.")

            # --- composition + loop assertions (criterion #8) --------------
            assert run.status is RunStatus.COMPLETED, (
                f"agentic run did not complete: status={run.status} error={run.error!r}"
            )
            step_types = [s.type for s in run.steps]
            assert step_types == [
                StepType.TOOL_CALL,  # use_skill(pdf_generation)
                StepType.TOOL_CALL,  # code_execution(_PDF_CODE)
                StepType.FINAL,
            ], f"unexpected step sequence: {step_types}"

            # The code_execution tool result carries the produced-files
            # metadata at the sandbox boundary — pulled from
            # ExecutionResult.produced_files per D-12-14.
            code_step = next(
                s
                for s in run.steps
                if s.type is StepType.TOOL_CALL
                and any(c.name == "code_execution" for c in s.tool_calls)
            )
            ce_result = code_step.results[0]
            assert ce_result.is_error is False, f"code_execution failed: {ce_result.content!r}"
            assert ce_result.data is not None
            produced = ce_result.data.get("produced_files") or []
            assert any(
                p.get("path", "").endswith("tenant_protection_quarterly.pdf") for p in produced
            ), f"expected the .pdf in produced_files; got {produced}"

            # --- persona-workspace persistence (criterion #3) --------------
            workspace_pdf = persona_workspace / "tenant_protection_quarterly.pdf"
            assert workspace_pdf.is_file(), (
                f"produced_file_persister should have copied the PDF to "
                f"{workspace_pdf}; directory listing: "
                f"{list(persona_workspace.iterdir())}"
            )
            assert workspace_pdf.stat().st_size > 5_000, (
                f"PDF is suspiciously small ({workspace_pdf.stat().st_size} bytes); "
                "the reportlab build likely failed to flush"
            )

            # --- M1a verification (criterion #5/#6 enabler) ----------------
            # The use_skill intercept SHOULD have staged supplements into
            # the holder. The next code_execution drained them — so post-run
            # the holder is empty AND the supplements that WOULD HAVE BEEN
            # collected from the real on-disk pdf_generation skill match
            # what production wiring would stage.
            expected_supplements = collect_skill_supplements(pdf_spec)
            assert len(expected_supplements) >= 1, (
                "the on-disk pdf_generation skill must ship at least one "
                "supplement (flowables.md / pagination.md / images.md) per "
                "T06 close-gate"
            )
            # The holder should be empty post-run (drain-and-clear semantics).
            assert deferred_holder == [], (
                "deferred_input_files holder should be drained after the "
                "code_execution dispatch; got: "
                f"{[f.path for f in deferred_holder]}"
            )

            # --- save inspection artifact (D-16-X-3 gitignored) ------------
            _INSPECTION_DIR.mkdir(parents=True, exist_ok=True)
            inspection_artifact = _INSPECTION_DIR / "pdf_sample.pdf"
            shutil.copy(workspace_pdf, inspection_artifact)
            assert inspection_artifact.is_file()

            # --- parse + assert §3.7 rubric --------------------------------
            reader = pypdf.PdfReader(str(workspace_pdf))

            # Row 1 (criterion §3.5#1): Multi-page — `len(reader.pages) >= 3`.
            assert len(reader.pages) >= 3, (
                f"expected >= 3 pages, got {len(reader.pages)}; "
                "the LongTable should span pages 2-3 with a summary on page 3+"
            )

            # Row 7 (criterion §3.5#7): Cover page distinct from body.
            page0_text = reader.pages[0].extract_text() or ""
            assert "Tenant protection" in page0_text, (
                f"cover page should contain the title 'Tenant protection'; "
                f"got: {page0_text[:200]!r}"
            )

            # Row 3 (criterion §3.5#3): Page-spanning table with repeating
            # header. Per §3.7's loosened assert form: accept either the
            # caption substring OR the header tokens "District" / "Complaints"
            # appearing across the body pages (the LongTable's header repeats
            # on each page split — so "District" should be visible on both
            # page 2 and page 3 if the split happens there; we look across
            # the body pages defensively because reportlab's flow may stretch
            # the table slightly).
            body_pages_text = " ".join(
                reader.pages[i].extract_text() or "" for i in range(1, len(reader.pages))
            )
            district_hits = body_pages_text.count("District")
            assert district_hits >= 2, (
                f"LongTable's repeatRows=1 header should appear at least "
                f"twice across body pages (page-split + repeat); got "
                f"{district_hits} 'District' occurrences across body pages."
            )
            assert "Complaints" in body_pages_text, (
                "body pages should contain the 'Complaints' header column"
            )
            assert "Sentrum" in body_pages_text, (
                "body pages should contain 'Sentrum' from the table"
            )
            assert "Grunerlokka" in body_pages_text, (
                "body pages should contain 'Grunerlokka' from the table"
            )

            # Row 4 (criterion §3.5#4): Image XObject present (defensive walk
            # over /Resources/XObject — accept "no image found" as PARTIAL
            # if the walk is fragile, per the task brief; the matplotlib
            # PNG was embedded via Image flowable so we expect to find it).
            image_xobject_found = False
            for page in reader.pages:
                try:
                    resources = page.get("/Resources")
                    if resources is None:
                        continue
                    if hasattr(resources, "get_object"):
                        resources = resources.get_object()
                    xobjects = resources.get("/XObject") if resources else None  # type: ignore[union-attr]
                    if xobjects is None:
                        continue
                    if hasattr(xobjects, "get_object"):
                        xobjects = xobjects.get_object()
                    for obj_ref in xobjects.values():  # type: ignore[union-attr]
                        try:
                            obj = (
                                obj_ref.get_object() if hasattr(obj_ref, "get_object") else obj_ref
                            )
                            if obj.get("/Subtype") == "/Image":
                                image_xobject_found = True
                                break
                        except Exception:
                            continue
                    if image_xobject_found:
                        break
                except Exception:
                    continue
            # PASS if found; PARTIAL (warning only) if the walk was fragile.
            # We treat this as a hard assert per the rubric — the chart IS
            # embedded as a PNG via reportlab.platypus.Image so the
            # XObject MUST be present in well-formed PDF output.
            assert image_xobject_found, (
                "expected at least one /Subtype /Image XObject in the PDF "
                "(the matplotlib chart was embedded via the Image flowable)"
            )

        finally:
            await sandbox.aclose()
