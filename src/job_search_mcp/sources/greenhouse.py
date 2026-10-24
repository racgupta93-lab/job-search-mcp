"""Greenhouse public Job Board API (no key needed for reading).

Docs: https://developers.greenhouse.io/job-board.html
List: GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs            (light)
      GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true   (full text)
Note: the list has no pay field; pay ranges exist only on the single-job
      endpoint (?pay_transparency=true), so salary is left empty here.
"""

from __future__ import annotations

import re
from typing import Any

from job_search_mcp.http_client import FetchError, SafeHttpClient
from job_search_mcp.models import Job
from job_search_mcp.sanitize import clean_text, html_to_text
from job_search_mcp.sources.base import BoardSource
from job_search_mcp.sources.common import parse_iso_time, salary_fields

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def parse_job(board: str, raw: Any) -> Job | None:
    """Turn one raw Greenhouse job into a Job, or None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    job_id, url = raw.get("id"), raw.get("absolute_url")
    title = clean_text(raw.get("title"), 300)
    if not isinstance(job_id, int) or not isinstance(url, str) or not title:
        return None
    if url.startswith("http://"):
        # Some companies publish plain-http links. Never hand those on: use the job's page on
        # Greenhouse's own https site instead of discarding the posting.
        url = f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    if not url.startswith("https://"):
        return None
    location_raw = raw.get("location")
    location = clean_text(location_raw.get("name") if isinstance(location_raw, dict) else None, 300)
    departments = [
        clean_text(d.get("name"), 100)
        for d in raw.get("departments") or []
        if isinstance(d, dict) and d.get("name")
    ][:10]
    try:
        return Job(
            id=f"greenhouse:{board}:{job_id}",
            source="greenhouse",
            company=clean_text(raw.get("company_name"), 200) or board,
            title=title,
            location=location or None,
            workplace_type="remote" if "remote" in location.lower() else None,
            url=url[:2000],
            posted_at=parse_iso_time(raw.get("first_published"))
            or parse_iso_time(raw.get("updated_at")),
            departments=[d for d in departments if d],
            description=html_to_text(raw.get("content"), 20_000),
        )
    except ValueError:
        return None


class GreenhouseSource(BoardSource):
    """Two-stage: the plain list is ~18x smaller than the list with full text, and it
    still has title, location and dates, so full text is fetched only when needed."""

    name = "greenhouse"
    two_stage = True
    config_hint = "add Greenhouse company names under 'greenhouse:' in companies.yaml"

    def _list_url(self, board: str) -> str:
        return f"{BASE_URL}/{board}/jobs"

    def _content_url(self, board: str) -> str:
        return f"{BASE_URL}/{board}/jobs?content=true"

    def _check_url(self, board: str) -> str:
        return f"{BASE_URL}/{board}"

    def _extract(self, data: Any) -> list[Any] | None:
        jobs = data.get("jobs") if isinstance(data, dict) else None
        return jobs if isinstance(jobs, list) else None

    def _parse(self, board: str, raw: Any) -> Job | None:
        return parse_job(board, raw)


_NOT_BASE_PAY = re.compile(r"bonus|equity|commission|stock|rsu|incentive|sign", re.IGNORECASE)
_HOURLY_TITLE = re.compile(r"hour", re.IGNORECASE)
_HOURLY_BLURB = re.compile(r"per\s+hour|hourly|/\s?hr\b", re.IGNORECASE)
_MONTHLY_TITLE = re.compile(r"month", re.IGNORECASE)
_MONTHLY_BLURB = re.compile(r"per\s+month|monthly", re.IGNORECASE)


def parse_pay_ranges(ranges: Any) -> dict[str, Any]:
    """Salary fields from Greenhouse `pay_input_ranges` (amounts are in cents).

    Several ranges (for example per location) are reported as one overall span.
    Bonus and equity ranges are skipped. Anything unclear gives no salary.
    """
    if not isinstance(ranges, list):
        return {}
    found: list[tuple[float, float, str, str | None]] = []
    for item in ranges:
        if not isinstance(item, dict):
            continue
        title, blurb = str(item.get("title") or ""), str(item.get("blurb") or "")
        low, high = item.get("min_cents"), item.get("max_cents")
        if _NOT_BASE_PAY.search(title):
            continue
        if isinstance(low, bool) or isinstance(high, bool):
            continue
        if not isinstance(low, int | float) or not isinstance(high, int | float):
            continue
        low, high = low / 100, high / 100
        # Unit from the label, or explicit "per hour" wording; "40 hours a week" is not a unit.
        if _HOURLY_TITLE.search(title) or _HOURLY_BLURB.search(blurb):
            period = "hour"
        elif _MONTHLY_TITLE.search(title) or _MONTHLY_BLURB.search(blurb):
            period = "month"
        else:
            period = "year" if low >= 20_000 else None
        if period:
            found.append((low, high, period, item.get("currency_type")))
    if not found:
        return {}
    period, currency = found[0][2], found[0][3]
    same = [f for f in found if f[2] == period and f[3] == currency]
    return salary_fields(
        min(f[0] for f in same), max(f[1] for f in same), currency, period, "employer"
    )


async def fetch_pay(http: SafeHttpClient, board: str, native_id: str) -> dict[str, Any]:
    """Official pay range for one Greenhouse job, or {} if none is published."""
    try:
        data = await http.get_json(f"{BASE_URL}/{board}/jobs/{native_id}?pay_transparency=true")
    except FetchError:
        return {}
    return parse_pay_ranges(data.get("pay_input_ranges")) if isinstance(data, dict) else {}
