"""``datetime`` built-in tool — current time, timezone math, date arithmetic.

Spec 26 T02. Pure stdlib (`datetime` + `zoneinfo`); IANA timezone data comes
from the `tzdata` dependency so behaviour is identical across macOS / Linux /
Windows / slim-Docker (D-26-X-launch-deps). No `eval`, no network.

Four operations, selected by the ``operation`` argument:

- ``now`` — current date-time in ``timezone`` (default UTC).
- ``convert`` — convert ``datetime`` (ISO 8601) into ``to_timezone``.
- ``add`` — add ``amount`` × ``unit`` (weeks/days/hours/minutes/seconds) to
  ``datetime`` (signed; negative subtracts).
- ``diff`` — the signed difference between ``datetime`` and ``end``.

Bad timezones, unparseable datetimes, and bad arguments are returned as
``ToolResult(is_error=True, content=...)`` — never raised (D-03-5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from persona.logging import get_logger
from persona.schema.tools import ToolResult
from persona.tools.protocol import AsyncTool, tool

__all__ = ["make_datetime_tool"]

_logger = get_logger("tools.datetime")

_UNIT_TO_KWARG: dict[str, str] = {
    "weeks": "weeks",
    "days": "days",
    "hours": "hours",
    "minutes": "minutes",
    "seconds": "seconds",
}


def _resolve_zone(name: str) -> ZoneInfo:
    """Resolve an IANA timezone name, raising ``ZoneInfoNotFoundError`` if unknown."""
    return ZoneInfo(name)


def _parse_dt(value: str, default_zone: ZoneInfo) -> datetime:
    """Parse an ISO 8601 string; naive inputs are assigned ``default_zone``."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_zone)
    return parsed


def _err(content: str) -> ToolResult:
    return ToolResult(tool_name="datetime", content=content, is_error=True)


def make_datetime_tool() -> AsyncTool:
    """Build the ``datetime`` :class:`AsyncTool`.

    Returns:
        An :class:`AsyncTool` named ``datetime``. Invalid timezones, datetimes,
        and arguments are returned as ``ToolResult(is_error=True)`` — never
        raised.
    """

    @tool(
        name="datetime",
        description=(
            "YOU CAN work with dates and times precisely. Use this tool instead "
            "of guessing the current time or doing date math in your head. "
            "operation='now' gives the current time in a timezone; "
            "operation='convert' converts a datetime between timezones; "
            "operation='add' adds/subtracts time (amount + unit); "
            "operation='diff' gives the difference between two datetimes. "
            "Timezones are IANA names like 'UTC', 'Europe/Oslo', "
            "'America/New_York'. Datetimes are ISO 8601 (e.g. "
            "'2026-06-14T12:00:00')."
        ),
    )
    async def datetime_tool(
        operation: Literal["now", "convert", "add", "diff"] = "now",
        timezone: str = "UTC",
        datetime: str | None = None,  # noqa: A002 — model-facing arg name
        to_timezone: str | None = None,
        amount: int | None = None,
        unit: Literal["weeks", "days", "hours", "minutes", "seconds"] | None = None,
        end: str | None = None,
    ) -> ToolResult:
        # Resolve the base timezone up front (shared by every operation).
        try:
            base_zone = _resolve_zone(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return _err(
                f"Unknown timezone: {timezone!r}. Use an IANA name like "
                "'UTC', 'Europe/Oslo', or 'America/New_York'."
            )

        if operation == "now":
            current = _datetime_now(base_zone)
            return ToolResult(
                tool_name="datetime",
                content=f"{current.isoformat()} ({timezone})",
                data={"datetime": current.isoformat(), "timezone": timezone},
            )

        if operation == "convert":
            if datetime is None or to_timezone is None:
                return _err("'convert' requires both 'datetime' and 'to_timezone'.")
            try:
                target_zone = _resolve_zone(to_timezone)
            except (ZoneInfoNotFoundError, ValueError):
                return _err(f"Unknown timezone: {to_timezone!r}. Use an IANA name.")
            try:
                source = _parse_dt(datetime, base_zone)
            except ValueError:
                return _err(f"Could not parse datetime: {datetime!r}. Use ISO 8601.")
            converted = source.astimezone(target_zone)
            return ToolResult(
                tool_name="datetime",
                content=f"{converted.isoformat()} ({to_timezone})",
                data={
                    "datetime": converted.isoformat(),
                    "timezone": to_timezone,
                    "source": source.isoformat(),
                },
            )

        if operation == "add":
            if datetime is None or amount is None or unit is None:
                return _err("'add' requires 'datetime', 'amount', and 'unit'.")
            try:
                source = _parse_dt(datetime, base_zone)
            except ValueError:
                return _err(f"Could not parse datetime: {datetime!r}. Use ISO 8601.")
            delta = timedelta(**{_UNIT_TO_KWARG[unit]: amount})
            result = source + delta
            return ToolResult(
                tool_name="datetime",
                content=f"{result.isoformat()} ({amount:+d} {unit} from {source.isoformat()})",
                data={"datetime": result.isoformat(), "source": source.isoformat()},
            )

        # Remaining operation is "diff".
        if datetime is None or end is None:
            return _err("'diff' requires both 'datetime' and 'end'.")
        try:
            start = _parse_dt(datetime, base_zone)
            finish = _parse_dt(end, base_zone)
        except ValueError:
            return _err("Could not parse 'datetime' or 'end'. Use ISO 8601.")
        diff = finish - start
        total_seconds = diff.total_seconds()
        return ToolResult(
            tool_name="datetime",
            content=(
                f"{total_seconds:g} seconds ({diff.days} days, {diff.seconds} seconds remainder)"
            ),
            data={
                "total_seconds": total_seconds,
                "days": diff.days,
                "start": start.isoformat(),
                "end": finish.isoformat(),
            },
        )

    return datetime_tool


def _datetime_now(zone: ZoneInfo) -> datetime:
    """Current aware datetime in ``zone``. Isolated for test seams."""
    return datetime.now(UTC).astimezone(zone)
