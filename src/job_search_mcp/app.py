"""Builds the long-lived objects (settings, HTTP client, sources) once."""

from __future__ import annotations

from dataclasses import dataclass, field

from job_search_mcp.config import Settings, load_settings
from job_search_mcp.http_client import SafeHttpClient
from job_search_mcp.job_index import JobIndex
from job_search_mcp.models import ListSourcesResult, SourceStatus
from job_search_mcp.sources.adzuna import AdzunaSource
from job_search_mcp.sources.ashby import AshbySource
from job_search_mcp.sources.base import JobSource
from job_search_mcp.sources.greenhouse import GreenhouseSource
from job_search_mcp.sources.lever import LeverSource
from job_search_mcp.sources.muse import MuseSource
from job_search_mcp.sources.smartrecruiters import SmartRecruitersSource


@dataclass
class App:
    settings: Settings
    http: SafeHttpClient
    sources: list[JobSource]
    index: JobIndex = field(default_factory=JobIndex)

    async def list_sources(self, check_connectivity: bool) -> ListSourcesResult:
        statuses: list[SourceStatus] = []
        for source in self.sources:
            if check_connectivity:
                statuses.append(await source.check())
            else:
                statuses.append(
                    SourceStatus(
                        name=source.name,
                        companies=source.company_count,
                        status="not_checked" if source.is_configured else "not_configured",
                    )
                )
        notes = [self.settings.companies_note] if self.settings.companies_note else []
        return ListSourcesResult(sources=statuses, notes=notes)


def build_app(settings: Settings | None = None) -> App:
    settings = settings or load_settings()
    http = SafeHttpClient(
        max_response_bytes=settings.max_response_bytes,
        requests_per_second=settings.requests_per_second,
    )
    c = settings.companies
    sources: list[JobSource] = [
        GreenhouseSource(http, c.greenhouse),
        LeverSource(http, c.lever),
        LeverSource(http, c.lever_eu, eu=True),
        AshbySource(http, c.ashby),
        SmartRecruitersSource(http, c.smartrecruiters),
        MuseSource(http, settings.muse_api_key),
        AdzunaSource(http, settings.adzuna_app_id, settings.adzuna_app_key),
    ]
    return App(settings=settings, http=http, sources=sources)
