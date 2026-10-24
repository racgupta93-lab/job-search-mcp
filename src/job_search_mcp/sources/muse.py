"""The Muse public jobs API (free; a free key raises the rate limit).

Docs: https://www.themuse.com/developers/api/v2
List: GET https://www.themuse.com/api/public/jobs?page=N&category=..&level=..

What we learned by testing the live API (the docs page is thin):
  * pages are 0-based (page_count 31 means pages 0..30);
  * there is NO keyword search, only category / level / location / company;
  * results are not sorted by date and many are old (about half are over
    six months old), so we scan several pages and filter by date afterwards;
  * the location filter is coarse and inconsistent, so it is only a hint;
  * there is no structured salary field (pay text sometimes appears in the
    description, and a later stage extracts it).
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import SecretStr

from job_search_mcp.http_client import FetchError, SafeHttpClient
from job_search_mcp.models import Job, SourceStatus
from job_search_mcp.sanitize import clean_text, html_to_text
from job_search_mcp.sources.base import FetchResult, JobSource, SourceQuery
from job_search_mcp.sources.common import parse_iso_time

JOBS_URL = "https://www.themuse.com/api/public/jobs"
PAGE_SIZE = 20
MAX_PAGES = 15  # 300 postings per search; ~8 seconds at our politeness limit
CACHE_TTL_SECONDS = 600

# Muse category names we verified against the live API.
_CATEGORY_WORDS: list[tuple[tuple[str, ...], list[str]]] = [
    (("data", "analytic", "analyst", "business intelligence", "bi ", "sql", "machine learning"),
     ["Data and Analytics"]),
    (("software", "developer", "backend", "frontend", "full stack", "fullstack", "devops",
      "engineer"),
     ["Software Engineering"]),
    (("it ", "sysadmin", "security", "network", "helpdesk", "cloud"), ["Computer and IT"]),
    (("product manager", "product owner", "product"), ["Product Management"]),
    (("design", "ux", "ui "), ["Design and UX"]),
    (("marketing", "growth", "seo", "content", "brand"), ["Advertising and Marketing"]),
    (("project manager", "program manager", "scrum"), ["Project Management"]),
    (("sales", "account executive", "business development"), ["Sales"]),
    (("recruit", "talent", "human resources", "hr "), ["Human Resources and Recruitment"]),
    (("operations", "strategy", "chief of staff"), ["Business Operations"]),
    (("customer support", "customer success", "support specialist"), ["Customer Service"]),
    (("scientist", "research", "biolog", "chemist"), ["Science and Engineering"]),
]  # fmt: skip
_LEVELS = {"junior": "Entry Level", "mid": "Mid Level", "senior": "Senior Level"}


def categories_for(keywords: str | None) -> list[str]:
    """Pick Muse categories for free-text keywords (empty list: no match)."""
    text = f" {keywords or ''} ".lower()
    found: list[str] = []
    for words, categories in _CATEGORY_WORDS:
        if any(w in text for w in words):
            found.extend(c for c in categories if c not in found)
    return found[:3]


def parse_job(raw: Any) -> Job | None:
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id")
    refs = raw.get("refs") if isinstance(raw.get("refs"), dict) else {}
    url = refs.get("landing_page")
    title = clean_text(raw.get("name"), 300)
    if not isinstance(job_id, int) or not isinstance(url, str) or not title:
        return None
    if not url.startswith("https://"):
        return None
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    names = [
        clean_text(loc.get("name"), 100)
        for loc in raw.get("locations") or []
        if isinstance(loc, dict) and loc.get("name")
    ][:5]
    names = [n for n in names if n]
    levels = [lv for lv in raw.get("levels") or [] if isinstance(lv, dict)]
    categories = [
        clean_text(c.get("name"), 100)
        for c in raw.get("categories") or []
        if isinstance(c, dict) and c.get("name")
    ][:5]
    try:
        return Job(
            id=f"muse:{job_id}",
            source="muse",
            company=clean_text(company.get("name"), 200) or "(not listed)",
            title=title,
            location="; ".join(names) or None,
            workplace_type="remote" if any("remote" in n.lower() for n in names) else None,
            url=url[:2000],
            posted_at=parse_iso_time(raw.get("publication_date")),
            departments=[c for c in categories if c],
            description=html_to_text(raw.get("contents"), 20_000),
            level_hint=clean_text(levels[0].get("short_name"), 40) or None if levels else None,
        )
    except ValueError:
        return None


class MuseSource(JobSource):
    name = "muse"

    def __init__(self, http: SafeHttpClient, api_key: SecretStr | None) -> None:
        super().__init__(http)
        self._api_key = api_key
        self._cache: dict[tuple, tuple[float, list[Job], list[str]]] = {}

    @property
    def company_count(self) -> int:
        return 0

    @property
    def is_configured(self) -> bool:
        return True  # works without a key (lower rate limit)

    def _base_params(self) -> dict[str, Any]:
        return {"api_key": self._api_key.get_secret_value()} if self._api_key else {}

    async def fetch_jobs(self, query: SourceQuery | None = None) -> FetchResult:
        query = query or SourceQuery()
        categories = categories_for(query.keywords)
        level = _LEVELS.get(query.level or "")
        key = (tuple(categories), level, query.location)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return FetchResult(jobs=list(cached[1]), warnings=list(cached[2]))

        params: dict[str, Any] = {
            **self._base_params(),
            "location": query.location or "United States",
        }
        if categories:
            params["category"] = categories
        if level:
            params["level"] = level

        result = FetchResult()
        if not categories:
            result.warnings.append(
                "muse: no category matched your keywords, so a broad sample was scanned; "
                "results may be sparse"
            )
        pages_wanted = max(1, min(MAX_PAGES, -(-query.max_results // PAGE_SIZE)))
        total_pages = pages_wanted
        page = 0
        failed = False
        while page < min(pages_wanted, total_pages):
            try:
                data = await self._http.get_json(JOBS_URL, params={**params, "page": page})
            except FetchError as exc:
                result.warnings.append(f"muse: {exc}")
                failed = True
                break
            raw_results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(raw_results, list):
                result.warnings.append("muse: unexpected response")
                failed = True
                break
            result.jobs.extend(j for raw in raw_results if (j := parse_job(raw)) is not None)
            page_count = data.get("page_count")
            if isinstance(page_count, int) and page_count >= 0:
                total_pages = page_count
            page += 1
        if not failed and total_pages > page:
            result.warnings.append(
                f"muse: scanned {page} of {total_pages} pages; narrow your search to see more"
            )
        if not failed:
            self._cache[key] = (time.monotonic(), list(result.jobs), list(result.warnings))
        return result

    async def check(self) -> SourceStatus:
        try:
            await self._http.get_json(JOBS_URL, params={**self._base_params(), "page": 0})
        except FetchError as exc:
            hint = " (check MUSE_API_KEY)" if "403" in str(exc) and self._api_key else ""
            return SourceStatus(name=self.name, companies=0, status="error", detail=f"{exc}{hint}")
        detail = (
            "reachable; API key in use"
            if self._api_key
            else "reachable; no API key (500 requests/hour limit)"
        )
        return SourceStatus(name=self.name, companies=0, status="ok", detail=detail)
