"""A small sample Python file for the document parser fixtures."""

from __future__ import annotations


def calculate_monthly_rent(annual_rent: int, months: int = 12) -> float:
    """Return the monthly rent given an annual amount and number of months."""
    if months <= 0:
        raise ValueError("months must be positive")
    return annual_rent / months
