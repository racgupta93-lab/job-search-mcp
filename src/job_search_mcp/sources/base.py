"""What every job source must provide, plus the shared logic for company boards."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from job_search_mcp.http_client import FetchError, NotFoundError, SafeHttpClient
from job_search_mcp.models import Job, SourceStatus


@dataclass
class FetchResult:
    jobs: list[Job] = field(default_factory=list)
    # One bad company board must not break the whole search, so problems are
    # collected here and shown to the user instead of raised.
    warnings: list[str] = field(default_factory=list)


@dataclass
class SourceQuery:
    """What the user asked for. Company-board sources ignore it (they return
    everything and we filter afterwards); search-style sources like Adzuna use it."""

    keywords: str | None = None
    location: str | None = None
    max_age_days: int = 30
    salary_min: int | None = None
    level: str | None = None  # junior | mid | senior | lead | manager
    max_results: int = 100


class JobSource(ABC):
    name: str

    def __init__(self, http: SafeHttpClient) -> None:
        self._http = http

    @property
    @abstractmethod
    def company_count(self) -> int:
        """How many company boards are configured (0 for search-style sources)."""

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    async def fetch_jobs(self, query: SourceQuery | None = None) -> FetchResult:
        """Fetch current postings."""

    @abstractmethod
    async def check(self) -> SourceStatus:
        """Cheaply verify the source is configured and reachable."""


class BoardSource(JobSource):
    """A source where you list companies and each company has a public board."""

    cache_ttl_seconds = 900  # boards are big and change slowly
    concurrent_boards = 3
    config_hint = ""

    def __init__(self, http: SafeHttpClient, boards: list[str]) -> None:
        super().__init__(http)
        self._boards = boards
        self._cache: dict[str, tuple[float, list[Job]]] = {}

    @property
    def company_count(self) -> int:
        return len(self._boards)

    @property
    def is_configured(self) -> bool:
        return bool(self._boards)

    @abstractmethod
    def _list_url(self, board: str) -> str: ...

    @abstractmethod
    def _check_url(self, board: str) -> str: ...

    @abstractmethod
    def _extract(self, data: Any) -> list[Any] | None:
        """Pull the list of raw postings out of the response (None if malformed)."""

    @abstractmethod
    def _parse(self, board: str, raw: Any) -> Job | None: ...

    async def fetch_jobs(self, query: SourceQuery | None = None) -> FetchResult:
        gate = asyncio.Semaphore(self.concurrent_boards)

        async def one(board: str) -> FetchResult:
            async with gate:
                return await self._fetch_board(board)

        results = await asyncio.gather(*(one(b) for b in self._boards))
        merged = FetchResult()
        for result in results:
            merged.jobs.extend(result.jobs)
            merged.warnings.extend(result.warnings)
        return merged

    async def _fetch_board(self, board: str) -> FetchResult:
        cached = self._cache.get(board)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_seconds:
            return FetchResult(jobs=list(cached[1]))
        try:
            data = await self._http.get_json(self._list_url(board))
        except NotFoundError:
            return FetchResult(warnings=[f"{self.name}: board '{board}' not found"])
        except FetchError as exc:
            return FetchResult(warnings=[f"{self.name}: {exc}"])
        raw_jobs = self._extract(data)
        if raw_jobs is None:
            return FetchResult(warnings=[f"{self.name}: unexpected response for '{board}'"])
        jobs = [j for raw in raw_jobs if (j := self._parse(board, raw)) is not None]
        warnings = []
        if len(jobs) < len(raw_jobs):
            skipped = len(raw_jobs) - len(jobs)
            warnings.append(f"{self.name}: skipped {skipped} unusable posting(s) from '{board}'")
        self._cache[board] = (time.monotonic(), jobs)
        return FetchResult(jobs=list(jobs), warnings=warnings)

    async def check(self) -> SourceStatus:
        if not self._boards:
            return SourceStatus(
                name=self.name,
                companies=0,
                status="not_configured",
                detail=self.config_hint or "add company names to companies.yaml",
            )
        board = self._boards[0]
        try:
            await self._http.get_json(self._check_url(board))
        except FetchError as exc:
            return SourceStatus(
                name=self.name, companies=len(self._boards), status="error", detail=str(exc)
            )
        return SourceStatus(
            name=self.name,
            companies=len(self._boards),
            status="ok",
            detail=f"reachable (checked board '{board}')",
        )
