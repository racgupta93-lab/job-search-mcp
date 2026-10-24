"""Ashby public Job Posting API (no key needed for reading).

Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
List: GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
"""

from __future__ import annotations

from typing import Any

from job_search_mcp.models import Job
from job_search_mcp.sanitize import clean_text, html_to_text
from job_search_mcp.sources.base import BoardSource
from job_search_mcp.sources.common import (
    normalize_country,
    parse_iso_time,
    period_from_interval,
    salary_fields,
)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"
_WORKPLACE = {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}


def _salary(compensation: Any) -> dict[str, Any]:
    """Salary from Ashby's structured tiers.

    A posting may list several tiers (for example by city). We report the
    overall low-to-high span of the 'Salary' components that share the first
    component's currency and period. Equity and bonus are ignored.
    """
    if not isinstance(compensation, dict):
        return {}
    found: list[tuple[Any, Any, Any, str | None]] = []
    for tier in compensation.get("compensationTiers") or []:
        for comp in (tier.get("components") or []) if isinstance(tier, dict) else []:
            if isinstance(comp, dict) and comp.get("compensationType") == "Salary":
                found.append(
                    (
                        comp.get("minValue"),
                        comp.get("maxValue"),
                        comp.get("currencyCode"),
                        period_from_interval(comp.get("interval")),
                    )
                )
    if not found:
        return {}
    currency, period = found[0][2], found[0][3]
    same = [f for f in found if f[2] == currency and f[3] == period]
    lows = [f[0] for f in same if isinstance(f[0], int | float)]
    highs = [f[1] for f in same if isinstance(f[1], int | float)]
    return salary_fields(
        min(lows) if lows else None, max(highs) if highs else None, currency, period, "employer"
    )


def parse_job(board: str, raw: Any) -> Job | None:
    if not isinstance(raw, dict) or raw.get("isListed") is False:
        return None
    job_id, url = raw.get("id"), raw.get("jobUrl")
    title = clean_text(raw.get("title"), 300)
    if not isinstance(job_id, str) or not isinstance(url, str) or not title:
        return None
    if not url.startswith("https://"):
        return None
    address = raw.get("address") if isinstance(raw.get("address"), dict) else {}
    postal = address.get("postalAddress") if isinstance(address.get("postalAddress"), dict) else {}
    workplace = raw.get("workplaceType")
    departments = [clean_text(raw.get(k), 100) for k in ("department", "team")]
    try:
        return Job(
            id=f"ashby:{board}:{clean_text(job_id, 100)}",
            source="ashby",
            company=board,
            title=title,
            location=clean_text(raw.get("location"), 300) or None,
            country=normalize_country(postal.get("addressCountry")),
            workplace_type=_WORKPLACE.get(workplace) if isinstance(workplace, str) else None,
            url=url[:2000],
            posted_at=parse_iso_time(raw.get("publishedAt")),
            departments=[d for d in departments if d],
            description=html_to_text(raw.get("descriptionHtml"), 20_000),
            **_salary(raw.get("compensation")),
        )
    except ValueError:
        return None


class AshbySource(BoardSource):
    name = "ashby"
    config_hint = "add Ashby company names under 'ashby:' in companies.yaml"

    def _list_url(self, board: str) -> str:
        return f"{BASE_URL}/{board}?includeCompensation=true"

    def _check_url(self, board: str) -> str:
        return f"{BASE_URL}/{board}"

    def _extract(self, data: Any) -> list[Any] | None:
        jobs = data.get("jobs") if isinstance(data, dict) else None
        return jobs if isinstance(jobs, list) else None

    def _parse(self, board: str, raw: Any) -> Job | None:
        return parse_job(board, raw)
