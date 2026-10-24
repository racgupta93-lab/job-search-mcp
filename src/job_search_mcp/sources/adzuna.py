"""Adzuna job search API (free key from https://developer.adzuna.com/signup).

Search: GET https://api.adzuna.com/v1/api/jobs/us/search/{page}?app_id=..&app_key=..
Terms:  personal job-search use; show Adzuna attribution; salaries marked
        "predicted" are Adzuna's own estimates, not the employer's.

The key is sent in the query string (Adzuna's design). SafeHttpClient never
puts query strings in error messages or logs.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import SecretStr

from job_search_mcp.http_client import FetchError, SafeHttpClient
from job_search_mcp.models import Job, SourceStatus
from job_search_mcp.sanitize import clean_text, html_to_text
from job_search_mcp.sources.base import FetchResult, JobSource, SourceQuery
from job_search_mcp.sources.common import parse_iso_time, salary_fields

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/us/search"
PAGE_SIZE = 50
MAX_PAGES = 3  # keeps us well inside any free-tier quota
CACHE_TTL_SECONDS = 600


def _truthy(value: Any) -> bool:
    return value in (1, "1", True, "true")


def parse_result(raw: Any) -> Job | None:
    if not isinstance(raw, dict):
        return None
    job_id, url = raw.get("id"), raw.get("redirect_url")
    # Adzuna wraps matched keywords in <strong> tags, even in titles.
    title = clean_text(html_to_text(raw.get("title"), 300), 300)
    if job_id is None or not isinstance(url, str) or not url.startswith("https://") or not title:
        return None
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    category = raw.get("category") if isinstance(raw.get("category"), dict) else {}
    predicted = _truthy(raw.get("salary_is_predicted"))
    try:
        return Job(
            id=f"adzuna:us:{clean_text(str(job_id), 100)}",
            source="adzuna",
            company=clean_text(company.get("display_name"), 200) or "(not listed)",
            title=title,
            location=clean_text(location.get("display_name"), 300) or None,
            country="US",
            url=url[:2000],
            posted_at=parse_iso_time(raw.get("created")),
            departments=[d for d in [clean_text(category.get("label"), 100)] if d],
            description=html_to_text(raw.get("description"), 20_000),
            description_complete=False,  # Adzuna returns only a short preview
            **salary_fields(
                raw.get("salary_min"),
                raw.get("salary_max"),
                "USD",
                "year",
                "estimated" if predicted else "employer",
            ),
        )
    except ValueError:
        return None


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(
        self, http: SafeHttpClient, app_id: SecretStr | None, app_key: SecretStr | None
    ) -> None:
        super().__init__(http)
        self._app_id = app_id
        self._app_key = app_key
        self._cache: dict[tuple, tuple[float, list[Job]]] = {}

    @property
    def company_count(self) -> int:
        return 0

    @property
    def is_configured(self) -> bool:
        return self._app_id is not None and self._app_key is not None

    def _auth(self) -> dict[str, str]:
        if self._app_id is None or self._app_key is None:  # callers check is_configured first
            raise RuntimeError("Adzuna keys are not configured")
        return {
            "app_id": self._app_id.get_secret_value(),
            "app_key": self._app_key.get_secret_value(),
        }

    async def fetch_jobs(self, query: SourceQuery | None = None) -> FetchResult:
        if not self.is_configured:
            return FetchResult(
                warnings=["adzuna: skipped (set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env)"]
            )
        query = query or SourceQuery()
        key = (query.keywords, query.location, query.max_age_days, query.salary_min)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return FetchResult(jobs=list(cached[1]))

        params: dict[str, Any] = {
            **self._auth(),
            "results_per_page": PAGE_SIZE,
            "max_days_old": query.max_age_days,
            "sort_by": "date",
            "content-type": "application/json",
        }
        if query.keywords:
            params["what"] = query.keywords
        if query.location:
            params["where"] = query.location
        if query.salary_min:
            params["salary_min"] = query.salary_min

        pages = max(1, min(MAX_PAGES, -(-query.max_results // PAGE_SIZE)))
        result = FetchResult()
        for page in range(1, pages + 1):
            try:
                data = await self._http.get_json(f"{SEARCH_URL}/{page}", params=params)
            except FetchError as exc:
                result.warnings.append(f"adzuna: {exc}")
                break
            raw_results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(raw_results, list):
                result.warnings.append("adzuna: unexpected response")
                break
            result.jobs.extend(j for raw in raw_results if (j := parse_result(raw)) is not None)
            if len(raw_results) < PAGE_SIZE:
                break
        if not result.warnings:
            self._cache[key] = (time.monotonic(), list(result.jobs))
        return result

    async def check(self) -> SourceStatus:
        if not self.is_configured:
            return SourceStatus(
                name=self.name,
                companies=0,
                status="not_configured",
                detail="set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env (see README)",
            )
        try:
            await self._http.get_json(
                f"{SEARCH_URL}/1", params={**self._auth(), "results_per_page": 1}
            )
        except FetchError as exc:
            hint = " (check your Adzuna keys)" if ("401" in str(exc) or "403" in str(exc)) else ""
            return SourceStatus(name=self.name, companies=0, status="error", detail=f"{exc}{hint}")
        return SourceStatus(
            name=self.name, companies=0, status="ok", detail="reachable; API key accepted"
        )
