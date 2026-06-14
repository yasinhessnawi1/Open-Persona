"""Tests for the calculator built-in tool (spec 26 T01)."""

from __future__ import annotations

import math

import pytest
from persona.errors import CalculatorError
from persona.tools.builtin.calculator import (
    _MAX_EXPONENT,
    _MAX_EXPR_LEN,
    _MAX_FACTORIAL,
    evaluate,
    make_calculator_tool,
)
from persona.tools.protocol import AsyncTool


class TestEvaluateHappyPath:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("2 + 2", 4),
            ("2 + 2 * 10", 22),
            ("(1 + 5) / 3", 2.0),
            ("7 // 2", 3),
            ("7 % 3", 1),
            ("2 ** 8", 256),
            ("-5 + 3", -2),
            ("+5", 5),
            ("10 / 4", 2.5),
        ],
    )
    def test_arithmetic(self, expr: str, expected: float) -> None:
        assert evaluate(expr) == expected

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("sqrt(2)", math.sqrt(2)),
            ("factorial(5)", 120),
            ("abs(-7)", 7),
            ("floor(3.7)", 3),
            ("ceil(3.2)", 4),
            ("gcd(12, 8)", 4),
            ("pow(2, 10)", 1024),
            ("log10(1000)", 3.0),
        ],
    )
    def test_functions(self, expr: str, expected: float) -> None:
        assert evaluate(expr) == pytest.approx(expected)

    def test_constants(self) -> None:
        assert evaluate("pi") == pytest.approx(math.pi)
        assert evaluate("2 * pi") == pytest.approx(2 * math.pi)


class TestEvaluateRejectsUnsafe:
    @pytest.mark.parametrize(
        "expr",
        [
            "__import__('os').system('ls')",
            "(1).__class__",
            "os.system('ls')",
            "().__class__.__bases__",
            "[x for x in range(10)]",
            "lambda: 1",
            "x := 5",
            "open('/etc/passwd')",
            "globals()",
            "eval('1+1')",
            "'a' * 5",
            "f'{1}'",
            "{1: 2}[1]",
        ],
    )
    def test_rejects_dangerous_expressions(self, expr: str) -> None:
        with pytest.raises(CalculatorError):
            evaluate(expr)

    def test_rejects_unknown_function(self) -> None:
        with pytest.raises(CalculatorError, match="disallowed function"):
            evaluate("dangerous(1)")

    def test_rejects_unknown_name(self) -> None:
        with pytest.raises(CalculatorError, match="disallowed name"):
            evaluate("foo + 1")

    def test_rejects_keyword_args(self) -> None:
        with pytest.raises(CalculatorError, match="keyword arguments"):
            evaluate("pow(2, exp=3)")

    def test_rejects_non_numeric_constant(self) -> None:
        with pytest.raises(CalculatorError, match="numeric"):
            evaluate("True + 1")


class TestEvaluateDoSGuards:
    def test_rejects_overlong_expression(self) -> None:
        with pytest.raises(CalculatorError, match="too long"):
            evaluate("1+" * (_MAX_EXPR_LEN) + "1")

    def test_rejects_large_exponent_operator(self) -> None:
        with pytest.raises(CalculatorError, match="exponent too large"):
            evaluate(f"2 ** {_MAX_EXPONENT + 1}")

    def test_rejects_nested_exponent_bomb(self) -> None:
        # 10 ** 10 = 1e10 as the outer exponent → caught by the exponent cap.
        with pytest.raises(CalculatorError, match="exponent too large"):
            evaluate("10 ** (10 ** 10)")

    def test_rejects_large_exponent_via_pow(self) -> None:
        with pytest.raises(CalculatorError, match="exponent too large"):
            evaluate(f"pow(2, {_MAX_EXPONENT + 1})")

    def test_rejects_large_factorial(self) -> None:
        with pytest.raises(CalculatorError, match="factorial argument out of range"):
            evaluate(f"factorial({_MAX_FACTORIAL + 1})")

    def test_rejects_negative_factorial(self) -> None:
        with pytest.raises(CalculatorError, match="factorial argument out of range"):
            evaluate("factorial(-1)")

    def test_rejects_deeply_nested(self) -> None:
        # Genuinely nested operations (parens alone are grouping syntax, not
        # AST nodes) — 25 levels exceeds the depth cap (20).
        nested = "1"
        for _ in range(25):
            nested = f"(1+{nested})"
        with pytest.raises(CalculatorError, match="nested too deeply"):
            evaluate(nested)


class TestEvaluateParseErrors:
    def test_syntax_error(self) -> None:
        with pytest.raises(CalculatorError, match="could not parse"):
            evaluate("2 +")


class TestCalculatorTool:
    def test_is_async_tool(self) -> None:
        assert isinstance(make_calculator_tool(), AsyncTool)
        assert make_calculator_tool().name == "calculator"

    @pytest.mark.asyncio
    async def test_happy_path_returns_result(self) -> None:
        tool_inst = make_calculator_tool()
        result = await tool_inst.execute(expression="2 + 2 * 10")
        assert result.is_error is False
        assert result.content == "22"
        assert result.data == {"expression": "2 + 2 * 10", "result": 22}

    @pytest.mark.asyncio
    async def test_invalid_expression_returns_error_not_raises(self) -> None:
        tool_inst = make_calculator_tool()
        result = await tool_inst.execute(expression="__import__('os')")
        assert result.is_error is True
        assert "Cannot evaluate" in result.content

    @pytest.mark.asyncio
    async def test_division_by_zero_returns_error(self) -> None:
        tool_inst = make_calculator_tool()
        result = await tool_inst.execute(expression="1 / 0")
        assert result.is_error is True
        assert "ZeroDivisionError" in result.content

    @pytest.mark.asyncio
    async def test_dos_guard_returns_error(self) -> None:
        tool_inst = make_calculator_tool()
        result = await tool_inst.execute(expression="10 ** 99999")
        assert result.is_error is True
        assert "exponent too large" in result.content
