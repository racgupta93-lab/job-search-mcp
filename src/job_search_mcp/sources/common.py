"""Small parsing helpers shared by all sources. Everything here is defensive:
input comes from the internet, so any value may be missing or the wrong type."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MAX_SALARY = 10_000_000
_US_NAMES = {"us", "usa", "united states", "united states of america"}


def parse_iso_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_epoch_ms(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def normalize_country(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.lower() in _US_NAMES:
        return "US"
    return text[:56]


def period_from_interval(value: Any) -> str | None:
    """Map vendor words like 'per-year-salary' or '1 YEAR' to year/month/hour."""
    if not isinstance(value, str):
        return None
    text = value.lower()
    for word, period in (
        ("year", "year"),
        ("annual", "year"),
        ("month", "month"),
        ("hour", "hour"),
    ):
        if word in text:
            return period
    return None  # weekly, daily, one-time...: not comparable, so not used


def _amount(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not 0 < value <= MAX_SALARY:
        return None
    return round(value)


def salary_fields(
    low: Any, high: Any, currency: Any, period: str | None, basis: str
) -> dict[str, Any]:
    """Build the salary part of a Job from source-provided numbers only.

    Returns {} (no salary) rather than guessing when anything looks off.
    """
    lo, hi = _amount(low), _amount(high)
    if period is None or (lo is None and hi is None):
        return {}
    if lo is not None and hi is not None and lo > hi:
        return {}
    code = currency.upper() if isinstance(currency, str) and len(currency) == 3 else None
    return {
        "salary_min": lo,
        "salary_max": hi,
        "salary_currency": code if code and code.isalpha() else None,
        "salary_period": period,
        "salary_basis": basis,
    }
