"""Unit tests for sandbox boundary-crossing types (spec 12 T01).

Verifies the Phase-1 convention re-application (D-12-14): every type is
Pydantic v2 frozen with ``extra="forbid"``; tuple-typed collections (not
list — frozen-models-with-list-fields footgun); JSON round-trip survives
(needed by audit + SSE serialization).
"""

from __future__ import annotations

import pytest
from persona.sandbox import (
    ExecutionResult,
    NetworkPolicy,
    ResourceLimits,
    SandboxFile,
)
from pydantic import ValidationError


class TestResourceLimits:
    def test_defaults_match_spec(self) -> None:
        """Spec §4.1 defaults: CPU 1.0, memory 512 MiB, wall-clock 30s,
        disk 256 MiB, stdout cap 64k, 20 produced files at 50 MiB each."""
        limits = ResourceLimits()
        assert limits.cpu_cores == 1.0
        assert limits.memory_mb == 512
        assert limits.wall_clock_s == 30.0
        assert limits.disk_mb == 256
        assert limits.max_stdout_bytes == 64_000
        assert limits.max_produced_files == 20
        assert limits.max_produced_file_mb == 50

    def test_frozen_rejects_mutation(self) -> None:
        limits = ResourceLimits()
        with pytest.raises(ValidationError):
            limits.cpu_cores = 2.0  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ResourceLimits(cpu_cores=1.0, gpu_count=1)  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        ("field", "bad_value"),
        [
            ("cpu_cores", 0.0),
            ("cpu_cores", -1.0),
            ("memory_mb", 0),
            ("wall_clock_s", 0.0),
            ("disk_mb", 0),
            ("max_stdout_bytes", 0),
            ("max_produced_file_mb", 0),
        ],
    )
    def test_rejects_non_positive(self, field: str, bad_value: float) -> None:
        with pytest.raises(ValidationError):
            ResourceLimits(**{field: bad_value})

    def test_max_produced_files_allows_zero(self) -> None:
        """``max_produced_files`` is ``ge=0`` — zero produced files is valid
        (a useful "compute-only, no files" configuration)."""
        limits = ResourceLimits(max_produced_files=0)
        assert limits.max_produced_files == 0


class TestNetworkPolicy:
    def test_default_off(self) -> None:
        """**Critical security default:** network is OFF unless explicitly
        enabled. Spec §4.2 'The default is the safe one.'"""
        policy = NetworkPolicy()
        assert policy.enabled is False
        assert policy.allowed_hosts == ()

    def test_frozen(self) -> None:
        policy = NetworkPolicy()
        with pytest.raises(ValidationError):
            policy.enabled = True  # type: ignore[misc]

    def test_allow_list_is_tuple(self) -> None:
        """Tuple, not list — frozen-model-with-list footgun: a list's
        contents are still mutable even when the field is frozen."""
        policy = NetworkPolicy(enabled=True, allowed_hosts=("example.com",))
        assert isinstance(policy.allowed_hosts, tuple)
        assert policy.allowed_hosts == ("example.com",)

    def test_allow_list_coerces_from_list(self) -> None:
        """Pydantic coerces ``list[str]`` input into the declared ``tuple[str, ...]``;
        callers passing a list (the natural Python shape) shouldn't fail."""
        policy = NetworkPolicy(enabled=True, allowed_hosts=["a.com", "b.com"])  # type: ignore[arg-type]
        assert policy.allowed_hosts == ("a.com", "b.com")

    def test_extra_forbidden(self) -> None:
        """A model-passed ``allow_internet_access=True`` (the E2B SDK flag name)
        must not silently shadow our ``enabled``. ``extra="forbid"`` catches it."""
        with pytest.raises(ValidationError):
            NetworkPolicy(enabled=True, allow_internet_access=True)  # type: ignore[call-arg]


class TestSandboxFile:
    def test_minimal(self) -> None:
        f = SandboxFile(path="out/result.csv")
        assert f.path == "out/result.csv"
        assert f.content_bytes is None
        assert f.size_bytes == 0
        assert f.media_type == "application/octet-stream"

    def test_with_payload(self) -> None:
        payload = b"id,name\n1,a\n"
        f = SandboxFile(
            path="data.csv",
            content_bytes=payload,
            size_bytes=len(payload),
            media_type="text/csv",
        )
        assert f.content_bytes == payload
        assert f.size_bytes == len(payload)

    def test_frozen(self) -> None:
        f = SandboxFile(path="x")
        with pytest.raises(ValidationError):
            f.path = "y"  # type: ignore[misc]

    def test_negative_size_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SandboxFile(path="x", size_bytes=-1)


class TestExecutionResult:
    def test_minimal_success(self) -> None:
        """Acceptance §9 #1 (the core round-trip shape)."""
        result = ExecutionResult(
            stdout="4\n",
            stderr="",
            exit_status=0,
            outcome="ok",
        )
        assert result.stdout == "4\n"
        assert result.outcome == "ok"
        assert result.produced_files == ()
        assert result.duration_ms == 0.0
        assert result.truncated_stdout is False
        assert result.truncated_files is False

    @pytest.mark.parametrize("outcome", ["ok", "error", "timeout", "oom", "killed"])
    def test_outcome_literal_accepts_each(self, outcome: str) -> None:
        result = ExecutionResult(
            stdout="",
            stderr="",
            exit_status=0,
            outcome=outcome,  # type: ignore[arg-type]
        )
        assert result.outcome == outcome

    def test_outcome_literal_rejects_other(self) -> None:
        """Open-set ``outcome`` would let backends invent values; the Literal
        keeps the discriminator closed."""
        with pytest.raises(ValidationError):
            ExecutionResult(
                stdout="",
                stderr="",
                exit_status=0,
                outcome="success",  # type: ignore[arg-type]
            )

    def test_truncation_marker_fields(self) -> None:
        """Acceptance §9 #10 partial: ``truncated_stdout`` discriminates capped
        output. The marker text itself is the T03 tool factory's responsibility."""
        result = ExecutionResult(
            stdout="A" * 1000,
            stderr="",
            exit_status=0,
            outcome="ok",
            truncated_stdout=True,
        )
        assert result.truncated_stdout is True

    def test_produced_files_is_tuple(self) -> None:
        f = SandboxFile(path="chart.png", size_bytes=42, media_type="image/png")
        result = ExecutionResult(
            stdout="",
            stderr="",
            exit_status=0,
            outcome="ok",
            produced_files=(f,),
        )
        assert isinstance(result.produced_files, tuple)
        assert result.produced_files[0].path == "chart.png"

    def test_extra_forbidden(self) -> None:
        """Future-proofing: a backend can't sneak ``memory_peak_mb`` into the
        result type; if we want it we extend the model deliberately (D-03-3 pattern)."""
        with pytest.raises(ValidationError):
            ExecutionResult(
                stdout="",
                stderr="",
                exit_status=0,
                outcome="ok",
                memory_peak_mb=128,  # type: ignore[call-arg]
            )

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionResult(
                stdout="",
                stderr="",
                exit_status=0,
                outcome="ok",
                duration_ms=-1.0,
            )

    def test_frozen(self) -> None:
        result = ExecutionResult(stdout="", stderr="", exit_status=0, outcome="ok")
        with pytest.raises(ValidationError):
            result.stdout = "x"  # type: ignore[misc]

    def test_json_round_trip(self) -> None:
        """Boundary types must survive JSON serialization (audit log + SSE).

        D-12-14's whole justification — ``@dataclass`` doesn't give us this for
        free; Pydantic does."""
        result = ExecutionResult(
            stdout="hello",
            stderr="",
            exit_status=0,
            outcome="ok",
            produced_files=(SandboxFile(path="a.txt", size_bytes=5),),
            duration_ms=123.4,
        )
        payload = result.model_dump_json()
        roundtrip = ExecutionResult.model_validate_json(payload)
        assert roundtrip == result

    def test_json_round_trip_with_truncation_flags(self) -> None:
        """Truncation booleans survive JSON — needed because the T03 tool
        factory writes them into ``ToolResult.data`` for programmatic consumers."""
        result = ExecutionResult(
            stdout="A" * 64_000,
            stderr="",
            exit_status=0,
            outcome="ok",
            truncated_stdout=True,
            truncated_files=True,
        )
        roundtrip = ExecutionResult.model_validate_json(result.model_dump_json())
        assert roundtrip.truncated_stdout is True
        assert roundtrip.truncated_files is True
