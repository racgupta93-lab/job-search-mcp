"""Find a pay range written in posting text, e.g. "$150,000 - $200,000 per year".

This is a last resort for postings whose source has no structured salary
(most Greenhouse and Lever postings). It is deliberately strict: it only
accepts a *range* of two dollar amounts, near a pay-related word, with a
believable size, and it labels the result "posting_text". When in doubt it
returns nothing. It never guesses.

Limits: dollar-sign amounts only (USD unless a currency code such as CAD or MXN
sits beside the range), ignores single figures like "up to $X",
and may take the wrong range if a posting lists several unrelated ranges.
"""

from __future__ import annotations

import re
from typing import Any

from job_search_mcp.sources.common import salary_fields

_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?([kK])?"
_RANGE = re.compile(
    rf"\$\s?{_AMOUNT}\s*(?:-|\u2013|\u2014|to|and)\s*\$?\s?{_AMOUNT}",
)
_PAY_WORDS = re.compile(
    r"salary|pay|compensation|base|range|wage|rate|earn|ote\b|annual|hourly|per year|per hour",
    re.IGNORECASE,
)
# Money that is *not* pay: if one of these sits right next to the range, skip it.
_NOT_PAY_BEFORE = re.compile(
    r"(equity|stock|rsu|signing|sign-on|bonus of|stipend|relocation|reimburse|"
    r"budget|funding|raised|revenue|valuation)\W*$",
    re.IGNORECASE,
)
_NOT_PAY_AFTER = re.compile(
    r"^\W*(million|billion|m\b|b\b|in (funding|equity|stock|revenue)|equity|stipend|bonus)",
    re.IGNORECASE,
)
_PERIODS = (
    (re.compile(r"^\W*(?:usd\W*)?(?:per\s+hour|/\s?hr|/\s?hour|an\s+hour|hourly)", re.I), "hour"),
    (re.compile(r"^\W*(?:usd\W*)?(?:per\s+year|/\s?yr|/\s?year|a\s+year|annual)", re.I), "year"),
    (re.compile(r"^\W*(?:usd\W*)?(?:per\s+month|/\s?mo\b|monthly)", re.I), "month"),
)
_CODES = frozenset(
    "USD CAD MXN EUR GBP AUD NZD INR BRL JPY CNY SGD CHF SEK NOK DKK PLN CZK HUF RON ILS AED "
    "ZAR KRW HKD TWD PHP THB IDR MYR COP ARS CLP PEN TRY UAH".split()
)
_CODE_AFTER = re.compile(r"^\s*([A-Z]{3})\b")
_CODE_BEFORE = re.compile(r"\b([A-Z]{3})\s*$")
_PERIOD_BEFORE = (
    (re.compile(r"\b(monthly|per month)\b", re.I), "month"),
    (re.compile(r"\b(hourly|per hour)\b", re.I), "hour"),
)
_LIMITS = {"year": (20_000, 2_000_000), "hour": (10, 500), "month": (1_500, 150_000)}
_MAX_SPREAD = 3.0  # a range wider than 3x is almost certainly not a salary


def _to_number(digits: str, k: str | None) -> float:
    value = float(digits.replace(",", ""))
    return value * 1000 if k else value


def _period(before: str, after: str, low: float) -> str | None:
    for pattern, name in _PERIODS:
        if pattern.search(after[:40]):
            return name
    # A heading just before the range may carry the unit ("Monthly Pay Range").
    for pattern, name in _PERIOD_BEFORE:
        if pattern.search(before[-50:]):
            return name
    # No unit written: a big number is an annual salary, a small one is unclear.
    return "year" if low >= _LIMITS["year"][0] else None


def _currency(before: str, after: str) -> str:
    """The ISO code written next to the range, else USD (we search US postings)."""
    for match in (_CODE_AFTER.match(after), _CODE_BEFORE.search(before[-8:])):
        if match and match.group(1) in _CODES:
            return match.group(1)
    return "USD"


def extract_salary(text: str) -> dict[str, Any]:
    """Return Job salary fields found in `text`, or {} when nothing credible."""
    found: list[tuple[float, float, str, str]] = []
    for match in _RANGE.finditer(text):
        low = _to_number(match.group(1), match.group(2))
        high = _to_number(match.group(3), match.group(4))
        if low > high:
            continue
        before = text[max(0, match.start() - 200) : match.start()]
        after = text[match.end() : match.end() + 40]
        if _NOT_PAY_BEFORE.search(before[-40:]) or _NOT_PAY_AFTER.search(after):
            continue
        if not (_PAY_WORDS.search(before) or _PAY_WORDS.search(after[:30])):
            continue
        period = _period(before, after, low)
        if period is None:
            continue
        lo_lim, hi_lim = _LIMITS[period]
        if not (lo_lim <= low and high <= hi_lim) or high > low * _MAX_SPREAD:
            continue
        found.append((low, high, period, _currency(before, after)))
    if not found:
        return {}
    period, currency = found[0][2], found[0][3]
    same = [f for f in found if f[2] == period and f[3] == currency]
    low, high = min(f[0] for f in same), max(f[1] for f in same)
    if high > low * _MAX_SPREAD:  # several tiers that disagree wildly: use the first
        low, high = same[0][0], same[0][1]
    return salary_fields(low, high, currency, period, "posting_text")
