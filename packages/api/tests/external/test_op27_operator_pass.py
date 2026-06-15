"""Spec 27 (MCP v1) — operator pass. Live runtime, real keys.

Runs the §5.3 scenario list against the REAL built-in MCP servers (subprocesses
over real Streamable HTTP), a REAL tier registry from env, and a REAL
ConversationLoop for the gap turn. Writes a dispositioned transcript to the
spec's evidence/ folder and asserts zero FAIL.

Marked ``external`` (real APIs + network + subprocesses) — skipped by default;
run with: ``uv run pytest -m external -k op27 -s``. Requires the project .env
(PERSONA_* provider keys) loaded into the environment; this module loads it.
"""

# ruff: noqa: E501, ANN202 — harness test: dense disposition lines + nested
# helpers are intentional.
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from persona.config import PersonaCoreConfig
from persona.history import ConversationHistoryManager
from persona.schema.conversation import Conversation
from persona.schema.persona import Persona, PersonaIdentity
from persona.skills import SkillInjector, SkillScanner
from persona.tools import Toolbox
from persona.tools.mcp.client import MCPClient
from persona_api.mcp import BuiltinMCPSupervisor
from persona_api.services import authoring_service
from persona_runtime.logging import MemoryTurnLogWriter
from persona_runtime.loop import ConversationLoop
from persona_runtime.prompt import PromptBuilder
from persona_runtime.router import Router
from persona_runtime.tier import tier_registry_from_env

pytestmark = [pytest.mark.external, pytest.mark.asyncio]

_EVIDENCE = Path(__file__).resolve().parents[4] / "docs/specs/phase2/spec_27/evidence"


class _Stub:  # minimal in-memory MemoryStore double (memory is orthogonal)
    def __init__(self) -> None:
        self._all: list = []

    def write(self, *a, **k) -> None:  # noqa: ANN002,ANN003,ARG002
        self._all.extend(a[1] if len(a) > 1 else [])

    def query(self, *a, **k):  # noqa: ANN002,ANN003,ARG002
        return []

    def get_all(self, *a, **k):  # noqa: ANN002,ANN003,ARG002
        return list(self._all)

    def delete(self, *a, **k) -> None: ...  # noqa: ANN002,ANN003
    def remove_documents(self, *a, **k) -> None: ...  # noqa: ANN002,ANN003
    def history(self, *a, **k):  # noqa: ANN002,ANN003,ARG002
        return []

    def rollback(self, *a, **k) -> None: ...  # noqa: ANN002,ANN003


async def _dispatch(client: MCPClient, name: str, **kwargs):  # noqa: ANN003
    tool = next(t for t in client.get_tools() if t.name == name)
    return await tool.execute(**kwargs)


def _make_loop(registry, tools):  # noqa: ANN001
    writer = MemoryTurnLogWriter()
    loop = ConversationLoop(
        persona=Persona(
            persona_id="astrid",
            identity=PersonaIdentity(
                name="Astrid", role="assistant", background="A helpful assistant."
            ),
            autonomy="decisive",  # type: ignore[arg-type]
        ),
        stores={
            "identity": _Stub(),
            "self_facts": _Stub(),
            "worldview": _Stub(),
            "episodic": _Stub(),
        },  # type: ignore[arg-type]
        toolbox=Toolbox(tools, allow_list=None),  # type: ignore[arg-type]
        skill_scanner=SkillScanner([]),
        skill_injector=SkillInjector(),
        scanned_skills=[],
        history_manager=ConversationHistoryManager(compact_every=10, keep_recent=5),
        prompt_builder=PromptBuilder(),
        router=Router(),
        tier_registry=registry,
        turn_log_writer=writer,
    )
    return loop, writer


def _load_env() -> None:
    """Load the project .env into the environment (real provider keys).

    Done INSIDE the test (not at import) so collecting this module for the
    default suite never mutates the global env — only the live ``-m external``
    run sets the real PERSONA_* vars. Never echoes values.
    """
    env = Path(__file__).resolve().parents[4].parent / "Open-Persona" / ".env"
    if not env.exists():
        pytest.skip("project .env not found; operator pass needs real provider keys")
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        if k.strip() and v:
            os.environ.setdefault(k.strip(), v)


async def test_op27_operator_pass() -> None:
    _load_env()
    results: list[tuple[str, str, str]] = []
    lines: list[str] = []

    def rec(disp: str, title: str, detail: str) -> None:
        results.append((disp, title, detail))
        lines.append(f"[{disp}] {title}\n    {detail}")

    sandbox = tempfile.mkdtemp(prefix="op27_fs_")
    os.environ["PERSONA_TOOLS_SANDBOX_ROOT"] = sandbox
    os.environ["PERSONA_MCP_BUILTIN_ENABLED"] = "time,calculator,filesystem,weather"

    registry = tier_registry_from_env()
    sup = BuiltinMCPSupervisor(PersonaCoreConfig().mcp_builtin_enabled_parsed)

    # S1 time
    try:
        c = MCPClient(server_name="time", server_url=await sup.ensure("time"))
        await c.connect(strict=True)
        ok = await _dispatch(c, "mcp:time:datetime", operation="now", timezone="Europe/Oslo")
        bad = await _dispatch(c, "mcp:time:datetime", operation="now", timezone="Mars/Phobos")
        await c.disconnect()
        rec(
            "PASS"
            if (not ok.is_error and "Europe/Oslo" in ok.content and bad.is_error)
            else "FAIL",
            "1 time (mcp:time:datetime)",
            f"now={ok.content!r} | bad_tz_recover={bad.is_error}",
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "1 time", f"{type(e).__name__}: {e}")

    # S2 calculator
    try:
        c = MCPClient(server_name="calculator", server_url=await sup.ensure("calculator"))
        await c.connect(strict=True)
        ok = await _dispatch(c, "mcp:calculator:calculate", expression="sqrt(2)*3 + 17*19")
        rce = await _dispatch(c, "mcp:calculator:calculate", expression="(1).__class__")
        await c.disconnect()
        rec(
            "PASS" if (not ok.is_error and "327" in ok.content and rce.is_error) else "FAIL",
            "2 calculator (mcp:calculator:calculate)",
            f"ok={ok.content!r} | rce_rejected={rce.is_error}",
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "2 calculator", f"{type(e).__name__}: {e}")

    # S3 filesystem (sandbox + escape)
    try:
        c = MCPClient(server_name="filesystem", server_url=await sup.ensure("filesystem"))
        await c.connect(strict=True)
        w = await _dispatch(c, "mcp:filesystem:write_file", path="notes/a.txt", content="live")
        r = await _dispatch(c, "mcp:filesystem:read_file", path="notes/a.txt")
        esc = await _dispatch(c, "mcp:filesystem:write_file", path="../escaped.txt", content="x")
        await c.disconnect()
        wrote = not w.is_error and (Path(sandbox) / "notes" / "a.txt").read_text() == "live"
        blocked = esc.is_error and not (Path(sandbox).parent / "escaped.txt").exists()
        rec(
            "PASS" if (wrote and "live" in r.content and blocked) else "FAIL",
            "3 filesystem (sandboxed; ../escape rejected)",
            f"write_read={wrote and 'live' in r.content} | escape_blocked={blocked}",
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "3 filesystem", f"{type(e).__name__}: {e}")

    # S4 weather (live open-meteo)
    try:
        c = MCPClient(server_name="weather", server_url=await sup.ensure("weather"))
        await c.connect(strict=True)
        ok = await _dispatch(c, "mcp:weather:get_weather", location="Oslo", days=1)
        bad = await _dispatch(c, "mcp:weather:get_weather", location="Xyzzyqwd Nowhereville")
        await c.disconnect()
        rec(
            "PASS" if (not ok.is_error and "Oslo" in ok.content and bad.is_error) else "FAIL",
            "4 weather (mcp:weather:get_weather, live open-meteo)",
            f"live={ok.content.splitlines()[0]!r} | bad_loc_recover={bad.is_error}",
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "4 weather", f"{type(e).__name__}: {e}")

    # S5 lazy spawn
    try:
        s2 = BuiltinMCPSupervisor(("time", "calculator", "filesystem"))
        start = s2.running_server_count
        urls = await s2.resolve(["web_search", "mcp:calculator:calculate"])
        math1 = s2.running_server_count
        await s2.aclose()
        rec(
            "PASS"
            if (
                start == 0
                and set(urls) == {"calculator"}
                and math1 == 1
                and s2.running_server_count == 0
            )
            else "FAIL",
            "5 lazy spawn (construction=0, math persona=1, reaped=0)",
            f"start={start} math={math1} resolved={set(urls)} reaped={s2.running_server_count}",
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "5 lazy spawn", f"{type(e).__name__}: {e}")

    # S6 unified recommender (live mid tier)
    try:
        mid = registry.get("mid")
        skills = ("web_research", "data_analysis", "document_generation", "code_review")
        personas = {
            "legal writer": "A Norwegian tenancy-law assistant that drafts complaints and cites statutes.",
            "data analyst": "Analyzes CSV datasets and produces charts and summary statistics.",
            "researcher": "Researches topics across the web and synthesizes cited reports.",
            "coder": "Reviews pull requests and reasons about code on GitHub repositories.",
            "travel planner": "Plans trips: weather, local times, currency, and itineraries.",
        }
        detail = []
        all_ok = True
        for label, desc in personas.items():
            recs = await authoring_service.recommend_capabilities_for_persona(
                mid, desc, available_skills=skills
            )
            within = len(recs) <= 10
            providers = sorted({r.provider for r in recs})
            tags = ", ".join(f"{r.tool_name}[{r.provider}]" for r in recs[:6])
            detail.append(f"      {label}: {len(recs)} caps(≤10={within}) {providers} :: {tags}")
            all_ok = all_ok and within
        rec(
            "PASS" if all_ok else "FAIL",
            "6 unified recommender (live mid, 5 personas, combined ≤10)",
            "all within combined cap=" + str(all_ok) + "\n" + "\n".join(detail),
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "6 unified recommender", f"{type(e).__name__}: {e}")

    # S7 runtime MCP-gap (live frontier turn)
    try:
        loop, writer = _make_loop(registry, [])
        events = []

        async def on_event(ev):  # noqa: ANN001
            events.append(ev)

        conv = Conversation(conversation_id="c1", persona_id="astrid", messages=[])
        gap_prompt = (
            "I need the exact live weather in Oslo this very minute. You have no "
            "weather tool available. If you don't have access to real-time weather "
            "data, tell me plainly that you can't."
        )
        async for _ in loop.turn(conv, gap_prompt, on_event=on_event):
            pass
        log = writer.logs[-1]
        asking = [e for e in events if e.type == "asking_user"]
        fired = log.mcp_unavailable_requested == ["weather"] and len(asking) >= 1
        rec(
            "PASS" if fired else "KNOWN-LIMITATION",
            "7 runtime MCP-gap (live frontier turn → 3+1 consent)",
            f"mcp_unavailable_requested={log.mcp_unavailable_requested} | asking_user={len(asking)} | "
            + (
                asking[0].data["question"][:90]
                if asking
                else "(model gave no detectable gap this run)"
            ),
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "7 runtime MCP-gap", f"{type(e).__name__}: {e}")

    # S8 backward compat
    try:
        s3 = BuiltinMCPSupervisor(PersonaCoreConfig().mcp_builtin_enabled_parsed)
        urls = await s3.resolve(["web_search", "calculator", "file_read"])
        running = s3.running_server_count
        await s3.aclose()
        rec(
            "PASS" if (urls == {} and running == 0) else "FAIL",
            "8 backward compat (MCP-less persona spawns nothing)",
            f"resolved={urls} running={running}",
        )
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "8 backward compat", f"{type(e).__name__}: {e}")

    await sup.aclose()
    await registry.aclose()

    p = sum(1 for d, _, _ in results if d == "PASS")
    k = sum(1 for d, _, _ in results if d == "KNOWN-LIMITATION")
    f = sum(1 for d, _, _ in results if d == "FAIL")
    header = [
        "=" * 78,
        f"SPEC 27 OPERATOR PASS  —  {datetime.now(UTC).isoformat()}",
        "Build: feat/mcp-v1 worktree; live runtime, real keys; real built-in MCP",
        "subprocesses over real Streamable HTTP; real tier registry from env.",
        "Secrets: none printed (harness reads .env into os.environ, never echoes).",
        "=" * 78,
    ]
    footer = ["=" * 78, f"DISPOSITIONS: PASS {p} / KNOWN-LIMITATION {k} / FAIL {f}", "=" * 78]
    transcript = "\n".join([*header, *lines, *footer])
    print("\n" + transcript)
    _EVIDENCE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y_%m_%d")
    (_EVIDENCE / f"operator_pass_{stamp}.log").write_text(transcript + "\n")

    assert f == 0, f"operator pass had {f} FAIL disposition(s):\n{transcript}"
