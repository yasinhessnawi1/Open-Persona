"""Spec 17 T11 — Magika v0.1 fallback recovery mechanic.

§6 criterion #10 ("analysis errors surface as tool errors; the persona
recovers"): when a Spec 17 sandbox dispatch fails because of a parser
error (the `.xlsx-but-actually-csv` case that Magika WOULD catch at
upload time in v0.2; D-17-X-magika-deferred-v0.2), the Spec 06 agentic
loop's tool-error-recovery surfaces the error to the model and the model
writes corrective code on the next step.

**This is the v0.1 Magika substitute.** No Magika dependency added. The
recovery mechanic IS the fallback. T11 pins the mechanic so a future
loop change doesn't silently break it.

The test uses the scripted-backend pattern from
``test_loop_agentic.py::TestErrorRecovery::test_tool_failure_is_fed_back_and_model_recovers``,
specialised to the parser-error shape Spec 17's `data_analysis` skill
produces. A fake ``code_execution`` tool simulates:

  1. **Turn 1.** Model writes ``pd.read_excel("uploads/data.xlsx")``. The
     simulated parser raises (the file is actually CSV). Tool returns
     ``ToolResult(is_error=True, content="ValueError: not an xlsx; ...")``.
  2. **Turn 2.** Model sees the error, writes corrective
     ``pd.read_csv(...)``. Tool returns success.
  3. **Final.** Model explains the finding.

Tests the **mechanic**, not the model's judgement (deterministic
scripted backend; the scripted second turn IS the corrective code).

**Why this is a unit test, not an integration test.** The recovery loop
is the Spec 06 agentic-loop body; testing it doesn't require Docker, a
real sandbox, or a real model. The scripted backend + scripted tool
prove the loop's behaviour.
"""

# ruff: noqa: SLF001 — tests reach into the registry cache.

from __future__ import annotations

import pytest
from _fakes import FakeStore, ScriptedBackend  # type: ignore[import-not-found]
from persona.backends import BackendConfig, ChatResponse
from persona.backends.types import TokenUsage
from persona.schema.persona import Persona, PersonaIdentity
from persona.schema.tools import ToolCall, ToolResult
from persona.skills import SkillInjector
from persona.tools import Toolbox
from persona.tools.protocol import tool
from persona_runtime.agentic.loop import AgenticLoop
from persona_runtime.agentic.run import RunStatus
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import TierConfig, TierRegistry

_DUMMY_CFG = BackendConfig(provider="anthropic", model="m", api_key=None)  # type: ignore[arg-type]


def _persona() -> Persona:
    return Persona(
        persona_id="data_analyst",
        identity=PersonaIdentity(
            name="Astrid",
            role="data analyst",
            background="Analyses uploaded datasets.",
            constraints=[],
        ),
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


# A fake ``code_execution`` tool that simulates the Spec 17 sandbox
# dispatch path. It branches on the code string's content: an xlsx
# read_excel call raises (the parser-error path Magika would catch);
# a csv read_csv call succeeds (the corrective path the model writes
# after seeing the error).


_calls_made: list[str] = []


@tool(name="code_execution", description="Run Python in the sandbox.")
async def _fake_code_execution(code: str) -> ToolResult:
    """Simulates the Spec 17 sandbox dispatch for the Magika fallback test.

    NOT a real sandbox — branches on substrings in the code to model
    the parser-raise vs corrective-succeed path. Tracks each call so the
    test can assert the recovery order.
    """
    _calls_made.append(code)
    if "read_excel" in code and "uploads/data.xlsx" in code:
        # Simulated parser failure: file says .xlsx but contents are CSV.
        # pd.read_excel raises a ValueError; the Spec 06 catch-and-convert
        # surfaces it as a ToolResult(is_error=True).
        return ToolResult(
            tool_name="code_execution",
            content=(
                "ValueError: File 'uploads/data.xlsx' is not a valid Excel file "
                "(openpyxl could not read it; file may be CSV with wrong extension)"
            ),
            is_error=True,
            data={"outcome": "error", "error_type": "ValueError"},
        )
    if "read_csv" in code and "uploads/data.xlsx" in code:
        # Corrective code: model sees the error and tries read_csv instead.
        return ToolResult(
            tool_name="code_execution",
            content="shape: (1000, 4)\noutcome=ok exit_status=0 duration_ms=12.3",
            is_error=False,
            data={"outcome": "ok", "exit_status": 0},
        )
    return ToolResult(
        tool_name="code_execution",
        content="(no-op)",
        is_error=False,
        data={"outcome": "ok", "exit_status": 0},
    )


def _make_loop(script: list[ChatResponse]) -> AgenticLoop:
    stores = {
        "identity": FakeStore(),
        "self_facts": FakeStore(),
        "worldview": FakeStore(),
        "episodic": FakeStore(),
    }
    backend = ScriptedBackend([], chat_script=script)
    toolbox = Toolbox([_fake_code_execution], allow_list=["code_execution"])
    registry = TierRegistry(
        {
            "frontier": TierConfig(name="frontier", backend_config=_DUMMY_CFG),
            "mid": TierConfig(name="mid", backend_config=_DUMMY_CFG),
            "small": TierConfig(name="small", backend_config=_DUMMY_CFG),
        }
    )
    registry._cache = {"frontier": backend, "mid": backend, "small": backend}  # type: ignore[assignment]
    return AgenticLoop(
        persona=_persona(),
        stores=stores,  # type: ignore[arg-type]
        toolbox=toolbox,
        skill_injector=SkillInjector(),
        scanned_skills=[],
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        max_steps=20,
    )


class TestMagikaFallbackRecoveryMechanic:
    """D-17-X-magika-deferred-v0.2 contract: the recovery path IS the v0.1
    fallback. A parser raise on the wrong extension surfaces as an error
    the model can correct from."""

    @pytest.mark.asyncio
    async def test_parser_raise_then_corrective_code_completes_run(self) -> None:
        """The end-to-end recovery: turn 1 wrong-format raise → turn 2
        corrective code → final explanation."""
        _calls_made.clear()
        script = [
            # Turn 1: model writes read_excel for a wrongly-named CSV.
            _resp(
                tool_calls=[
                    ToolCall(
                        name="code_execution",
                        args={
                            "code": (
                                "import pandas as pd\n"
                                'df = pd.read_excel("uploads/data.xlsx")\n'
                                "print(df.head())"
                            )
                        },
                        call_id="c1",
                    )
                ]
            ),
            # Turn 2: model sees the parser error + corrects to read_csv.
            _resp(
                tool_calls=[
                    ToolCall(
                        name="code_execution",
                        args={
                            "code": (
                                "import pandas as pd\n"
                                'df = pd.read_csv("uploads/data.xlsx")\n'
                                "print(df.shape)"
                            )
                        },
                        call_id="c2",
                    )
                ]
            ),
            # Turn 3: model explains the finding.
            _resp(
                "[FINAL] The file extension said .xlsx but the contents were CSV. "
                "I re-loaded with pd.read_csv and the dataset has 1000 rows × 4 columns."
            ),
        ]
        loop = _make_loop(script)
        run = await loop.run("Analyse uploads/data.xlsx")
        assert run.status is RunStatus.COMPLETED, run.output
        # Both code paths were exercised in the right order.
        assert len(_calls_made) == 2
        assert "read_excel" in _calls_made[0]
        assert "read_csv" in _calls_made[1]

    @pytest.mark.asyncio
    async def test_parser_raise_surfaces_as_structured_is_error(self) -> None:
        """The parser failure flows through Spec 06's catch-and-convert as
        ``ToolResult(is_error=True)`` so the model gets a structured
        message it can reason about — never a crashed stream.
        """
        _calls_made.clear()
        script = [
            _resp(
                tool_calls=[
                    ToolCall(
                        name="code_execution",
                        args={"code": 'pd.read_excel("uploads/data.xlsx")'},
                        call_id="c1",
                    )
                ]
            ),
            _resp("[FINAL] gave up on the xlsx"),
        ]
        loop = _make_loop(script)
        run = await loop.run("try")
        # The error was surfaced as is_error=True (the recovery affordance).
        first_step_result = run.steps[0].results[0]
        assert first_step_result.is_error is True
        assert "ValueError" in first_step_result.content
        assert "Excel" in first_step_result.content

    @pytest.mark.asyncio
    async def test_corrective_code_after_error_succeeds(self) -> None:
        """The model's corrective turn (read_csv after read_excel raise)
        returns ``is_error=False`` — the recovery path completes cleanly."""
        _calls_made.clear()
        script = [
            _resp(
                tool_calls=[
                    ToolCall(
                        name="code_execution",
                        args={"code": 'pd.read_excel("uploads/data.xlsx")'},
                        call_id="c1",
                    )
                ]
            ),
            _resp(
                tool_calls=[
                    ToolCall(
                        name="code_execution",
                        args={"code": 'pd.read_csv("uploads/data.xlsx")'},
                        call_id="c2",
                    )
                ]
            ),
            _resp("[FINAL] success"),
        ]
        loop = _make_loop(script)
        run = await loop.run("try")
        assert run.status is RunStatus.COMPLETED
        # Step 0 had is_error=True; step 1 had is_error=False.
        assert run.steps[0].results[0].is_error is True
        assert run.steps[1].results[0].is_error is False


class TestMagikaDeferredV02InvariantsHold:
    """No Magika dependency, no upload-time MIME verification at v0.1.
    The recovery path bears the slack. This test pins the invariants the
    D-17-X-magika-deferred-v0.2 decision rests on.
    """

    def test_no_magika_dependency_in_persona_core(self) -> None:
        """``magika`` is NOT a dependency of persona-core at v0.1.

        If a future change inadvertently adds it, surface the cross-spec
        impact (license-stack review per D-13-X-pillow + D-14-X-pdf-library-license
        + D-17-X-magika-deferred-v0.2). v0.2 is the right place; v0.1 is not.
        """
        try:
            import magika  # type: ignore[import-not-found]  # noqa: F401, PLC0415
        except ImportError:
            return
        # If the import succeeds, fail with a hint pointing at the v0.2 path.
        pytest.fail(
            "magika is installed in the test env — at v0.1 per "
            "D-17-X-magika-deferred-v0.2 it should NOT be a dependency. "
            "If a recent change added it intentionally, update the decision."
        )
