"""``calculator`` built-in tool — safe arithmetic evaluation (spec 26 T01).

Evaluates a mathematical expression by parsing it with :func:`ast.parse`
(``mode="eval"``) and walking the tree against a strict **node whitelist**
(D-26-X-calculator-ast-scope). There is **no** ``eval`` and **no** third-party
dependency — the model gets recognizable, cheap, deterministic math without the
``code_execution`` sandbox.

Security model (whitelist, never blacklist):

- Allowed nodes: ``Expression``, numeric ``Constant``, ``BinOp``
  (``+ - * / // % **``), ``UnaryOp`` (``+x`` / ``-x``), and ``Call`` **only** to
  an explicit ``math.*`` function allow-list with positional numeric args.
- Allowed names: a tiny constant allow-list (``pi``/``e``/``tau``) and the
  function names above as call targets. Every other ``Name`` is rejected.
- Denied on sight: attribute access (kills ``(1).__class__`` / ``os.system``),
  subscripting, comprehensions, lambdas, f-strings, walrus, and any free name
  (kills ``__import__`` / globals).

DoS guards (the real residual risk once RCE is closed — Python ints are
arbitrary-precision, so ``10 ** 10 ** 9`` is a memory bomb):

- expression length cap (:data:`_MAX_EXPR_LEN`),
- AST node-count + depth caps (:data:`_MAX_NODES` / :data:`_MAX_DEPTH`),
- exponent magnitude cap on ``**`` / ``pow`` (:data:`_MAX_EXPONENT`),
- ``factorial`` argument cap (:data:`_MAX_FACTORIAL`).

All rejections raise :class:`persona.errors.CalculatorError`; the ``@tool``
decorator (and an explicit catch in the body) turn every failure into
``ToolResult(is_error=True, content=...)`` — the tool never raises past its
boundary (D-03-5).
"""

from __future__ import annotations

import ast
import math
from typing import TYPE_CHECKING

from persona.errors import CalculatorError
from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["make_calculator_tool"]

_logger = get_logger("tools.calculator")

# -- DoS guards -------------------------------------------------------------
_MAX_EXPR_LEN = 256
_MAX_NODES = 100
_MAX_DEPTH = 20
_MAX_EXPONENT = 1000
_MAX_FACTORIAL = 1000

# -- whitelists -------------------------------------------------------------
_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    # Pow is handled specially (exponent cap) — see _eval.
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}
# Named constants the expression may reference (resolved to numeric values).
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}
# Allow-listed callables. ``pow``/``factorial`` are guarded in _eval_call.
_FUNCS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "abs": abs,
    "round": round,
    "hypot": math.hypot,
    "degrees": math.degrees,
    "radians": math.radians,
    "gcd": math.gcd,
    "factorial": math.factorial,
    "pow": pow,
}

_Number = int | float


def _reject(node: ast.AST) -> CalculatorError:
    return CalculatorError(
        "expression contains an unsupported operation",
        context={"node": type(node).__name__},
    )


def _depth(node: ast.AST, current: int = 0) -> int:
    """Maximum child-nesting depth of ``node`` (DoS guard)."""
    children = list(ast.iter_child_nodes(node))
    if not children:
        return current
    return max(_depth(child, current + 1) for child in children)


def _eval_call(node: ast.Call) -> _Number:
    """Evaluate a whitelisted ``math.*`` call with DoS guards."""
    if not isinstance(node.func, ast.Name):
        raise _reject(node.func)
    fname = node.func.id
    if fname not in _FUNCS:
        raise CalculatorError(
            "unknown or disallowed function",
            context={"function": fname},
        )
    if node.keywords:
        raise CalculatorError("keyword arguments are not supported", context={"function": fname})
    args = [_eval(arg) for arg in node.args]
    if fname == "factorial" and (len(args) != 1 or args[0] > _MAX_FACTORIAL or args[0] < 0):
        raise CalculatorError(
            "factorial argument out of range",
            context={"max": str(_MAX_FACTORIAL)},
        )
    if fname == "pow" and len(args) >= 2 and abs(args[1]) > _MAX_EXPONENT:
        raise CalculatorError("exponent too large", context={"max": str(_MAX_EXPONENT)})
    return _FUNCS[fname](*args)


def _eval(node: ast.AST) -> _Number:
    """Recursively evaluate a whitelisted arithmetic node."""
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        # Accept only real numbers; reject str/bytes/bool-as-text/None.
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError(
                "only numeric literals are allowed",
                context={"value": repr(node.value)[:40]},
            )
        return node.value
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            base = _eval(node.left)
            exponent = _eval(node.right)
            if abs(exponent) > _MAX_EXPONENT:
                raise CalculatorError("exponent too large", context={"max": str(_MAX_EXPONENT)})
            return base**exponent
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise _reject(node.op)
        return op(_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        uop = _UNARY_OPS.get(type(node.op))
        if uop is None:
            raise _reject(node.op)
        return uop(_eval(node.operand))
    if isinstance(node, ast.Call):
        return _eval_call(node)
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise CalculatorError("unknown or disallowed name", context={"name": node.id})
    raise _reject(node)


def evaluate(expression: str) -> _Number:
    """Safely evaluate an arithmetic ``expression`` (no ``eval``).

    Args:
        expression: A mathematical expression (e.g. ``"2 + 2 * 10"``,
            ``"sqrt(2)"``, ``"factorial(5)"``).

    Returns:
        The numeric result (``int`` or ``float``).

    Raises:
        CalculatorError: The expression is too long, fails to parse, contains a
            non-whitelisted node, or trips a DoS guard.
    """
    if len(expression) > _MAX_EXPR_LEN:
        raise CalculatorError(
            "expression too long",
            context={"length": str(len(expression)), "max": str(_MAX_EXPR_LEN)},
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise CalculatorError("could not parse expression", context={"detail": str(e)}) from e

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > _MAX_NODES:
        raise CalculatorError(
            "expression too complex", context={"nodes": str(node_count), "max": str(_MAX_NODES)}
        )
    if _depth(tree) > _MAX_DEPTH:
        raise CalculatorError("expression nested too deeply", context={"max": str(_MAX_DEPTH)})

    return _eval(tree)


def make_calculator_tool() -> AsyncTool:
    """Build the ``calculator`` :class:`AsyncTool`.

    Returns:
        An :class:`AsyncTool` named ``calculator``. Invalid expressions and
        DoS-guard violations are returned as ``ToolResult(is_error=True,
        content=...)`` — never raised.
    """

    @tool(
        name="calculator",
        description=(
            "YOU CAN do exact arithmetic. Use this tool to evaluate a math "
            "expression precisely instead of computing in your head — e.g. "
            "'2 + 2 * 10', '(1+5)/3', 'sqrt(2)', 'factorial(5)', '2 ** 16'. "
            "Supports + - * / // % **, parentheses, and math functions "
            "(sqrt, sin, cos, tan, log, log10, log2, exp, floor, ceil, abs, "
            "round, pow, factorial, gcd, hypot, degrees, radians) plus the "
            "constants pi, e, tau."
        ),
    )
    async def calculator(expression: str) -> ToolResult:
        try:
            result = evaluate(expression)
        except CalculatorError as e:
            _logger.debug("calculator rejected expression", detail=str(e))
            return ToolResult(
                tool_name="calculator",
                content=f"Cannot evaluate {expression!r}: {e}",
                is_error=True,
            )
        except (ZeroDivisionError, OverflowError, ValueError) as e:
            return ToolResult(
                tool_name="calculator",
                content=f"Cannot evaluate {expression!r}: {type(e).__name__}: {e}",
                is_error=True,
            )

        return ToolResult(
            tool_name="calculator",
            content=str(result),
            data={"expression": expression, "result": result},
        )

    return calculator
