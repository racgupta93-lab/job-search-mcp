"""What every job source must provide, plus the shared logic for company boards.

Company-board sources can cover hundreds of companies, so they are built to stay fast:

* downloads are shared and cached per company (a slow board is never fetched twice);
* a source may be *two-stage* (Greenhouse): first download the small list of titles,
  locations and dates, apply the caller's cheap filters, and download full text only for
  companies that still have candidates;
* a search may carry a time budget: whatever has loaded in time is used, the rest keeps
  loading in the background, and the caller is told how many boards are still pending.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from job_search_mcp.http_client import FetchError, NotFoundError, SafeHttpClient
from job_search_mcp.models import Job, SourceStatus

FAILURE_CACHE_SECONDS = 300  # do not hammer a board that just failed or does not exist


@dataclass
class FetchResult:
    jobs: list[Job] = field(default_factory=list)
    # One bad company board must not break the whole search, so problems are
    # collected here and shown to the user instead of raised.
    warnings: list[str] = field(default_factory=list)
    seen: int | None = None  # postings looked at before cheap filtering (None: len(jobs))
    pending: int = 0  # boards that did not finish within the time budget


@dataclass
class SourceQuery:
    """What the user asked for.

    Search-style sources (Adzuna, The Muse) use the keywords, location and so on.
    Company-board sources use only `prefilter` and `time_budget`.
    """

    keywords: str | None = None
    location: str | None = None
    max_age_days: int = 30
    salary_min: int | None = None
    level: str | None = None  # junior | mid | senior | lead | manager
    max_results: int = 100
    # Cheap, conservative test on fields known before full text is downloaded.
    prefilter: Callable[[Job], bool] | None = None
    time_budget: float | None = None  # seconds; None means wait for everything


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


@dataclass
class _Loaded:
    jobs: list[Job]
    warnings: list[str]
    ok: bool = True


def _quiet(task: asyncio.Future) -> None:
    """Mark a background task's exception as seen so asyncio does not log it as lost."""
    if not task.cancelled():
        task.exception()


class BoardSource(JobSource):
    """A source where you list companies and each company has a public board."""

    cache_ttl_seconds = 1800  # postings change slowly; searches within 30 min reuse the data
    concurrent_boards = 8
    content_cache_boards = 25  # full-text boards kept at once (they are large)
    two_stage = False  # True: _list_url is a light list and the full text comes separately
    whole_board_content = True  # two-stage only: False when text is fetched posting by posting
    config_hint = ""

    def __init__(self, http: SafeHttpClient, boards: list[str]) -> None:
        super().__init__(http)
        self._boards = boards
        self._lists: dict[str, tuple[float, _Loaded]] = {}
        self._contents: OrderedDict[str, tuple[float, _Loaded]] = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Task[_Loaded]] = {}
        self._gate_loop: asyncio.AbstractEventLoop | None = None
        self._gate_semaphore: asyncio.Semaphore | None = None

    @property
    def company_count(self) -> int:
        return len(self._boards)

    @property
    def is_configured(self) -> bool:
        return bool(self._boards)

    def has_board(self, board: str) -> bool:
        return board in self._boards

    @abstractmethod
    def _list_url(self, board: str) -> str: ...

    def _content_url(self, board: str) -> str:
        return self._list_url(board)

    @abstractmethod
    def _check_url(self, board: str) -> str: ...

    @abstractmethod
    def _extract(self, data: Any) -> list[Any] | None:
        """Pull the list of raw postings out of the response (None if malformed)."""

    @abstractmethod
    def _parse(self, board: str, raw: Any) -> Job | None: ...

    # ---- downloading, caching, sharing -------------------------------------------------
    def _gate(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._gate_semaphore
        if self._gate_loop is not loop or semaphore is None:  # first use, or a new event loop
            semaphore = asyncio.Semaphore(self.concurrent_boards)
            self._gate_loop, self._gate_semaphore, self._inflight = loop, semaphore, {}
        return semaphore

    def _cached(self, kind: str, board: str) -> _Loaded | None:
        store = self._contents if kind == "content" else self._lists
        entry = store.get(board)
        if entry is None or entry[0] < time.monotonic():
            return None
        if kind == "content":
            self._contents.move_to_end(board)
        return entry[1]

    def _remember(self, kind: str, board: str, loaded: _Loaded) -> None:
        ttl = self.cache_ttl_seconds if loaded.ok else FAILURE_CACHE_SECONDS
        entry = (time.monotonic() + ttl, loaded)
        if kind == "content":
            self._contents[board] = entry
            self._contents.move_to_end(board)
            while len(self._contents) > self.content_cache_boards:
                self._contents.popitem(last=False)
        else:
            self._lists[board] = entry

    async def _download(self, kind: str, board: str) -> _Loaded:
        url = self._content_url(board) if kind == "content" else self._list_url(board)
        async with self._gate():
            try:
                data = await self._http.get_json(url)
            except NotFoundError:
                return _Loaded([], [f"{self.name}: board '{board}' not found"], ok=False)
            except FetchError as exc:
                return _Loaded([], [f"{self.name}: {exc}"], ok=False)
        raw_jobs = self._extract(data)
        if raw_jobs is None:
            return _Loaded([], [f"{self.name}: unexpected response for '{board}'"], ok=False)
        jobs = [j for raw in raw_jobs if (j := self._parse(board, raw)) is not None]
        warnings = []
        if len(jobs) < len(raw_jobs):
            skipped = len(raw_jobs) - len(jobs)
            warnings.append(f"{self.name}: skipped {skipped} unusable posting(s) from '{board}'")
        return _Loaded(jobs, warnings)

    async def _fetch_and_store(self, kind: str, board: str) -> _Loaded:
        loaded = await self._download(kind, board)
        self._remember(kind, board, loaded)
        return loaded

    async def _shared(self, kind: str, board: str) -> _Loaded:
        """Cached data, or the one download in flight for this board (never two)."""
        cached = self._cached(kind, board)
        if cached is not None:
            return cached
        self._gate()  # makes sure in-flight bookkeeping belongs to the running loop
        key = (kind, board)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._fetch_and_store(kind, board))
            task.add_done_callback(_quiet)
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
            self._inflight[key] = task
        # shield: a search that gives up waiting must not cancel a download others will reuse
        return await asyncio.shield(task)

    # ---- the public API ----------------------------------------------------------------
    async def fetch_board(self, board: str) -> list[Job]:
        """Every posting of one company, with full text."""
        loaded = await self._shared("content" if self.two_stage else "list", board)
        return list(loaded.jobs)

    async def _complete(self, board: str, survivors: list[Job]) -> tuple[list[Job], list[str]]:
        """Add full text to candidate postings. Default: one download of the whole board.

        Sources whose full text must be fetched posting by posting override this.
        """
        full = await self._shared("content", board)
        if full.jobs:
            by_id = {job.id: job for job in full.jobs}
            return [by_id.get(job.id, job) for job in survivors], []
        # Could not get the full text: keep the candidates, and say the text is missing.
        jobs = [job.model_copy(update={"description_complete": False}) for job in survivors]
        return jobs, full.warnings or [f"{self.name}: full text unavailable for '{board}'"]

    async def complete_job(self, job: Job) -> Job:
        """Full text for one posting (used by get_job_details). Default: already complete."""
        return job

    async def _board_for_query(
        self, board: str, prefilter: Callable[[Job], bool] | None
    ) -> FetchResult:
        per_posting = self.two_stage and not self.whole_board_content
        if not (self.two_stage and (prefilter is not None or per_posting)):
            loaded = await self._shared("content" if self.two_stage else "list", board)
            return FetchResult(list(loaded.jobs), list(loaded.warnings), seen=len(loaded.jobs))

        light = await self._shared("list", board)
        survivors = light.jobs if prefilter is None else [j for j in light.jobs if prefilter(j)]
        warnings = list(light.warnings)
        if not survivors:
            return FetchResult([], warnings, seen=len(light.jobs))
        jobs, more = await self._complete(board, survivors)
        return FetchResult(jobs, warnings + more, seen=len(light.jobs))

    async def fetch_jobs(self, query: SourceQuery | None = None) -> FetchResult:
        prefilter = query.prefilter if query else None
        budget = query.time_budget if query else None
        tasks = [asyncio.ensure_future(self._board_for_query(b, prefilter)) for b in self._boards]
        for task in tasks:
            task.add_done_callback(_quiet)
        merged = FetchResult(seen=0)
        if not tasks:
            return merged
        done, pending = await asyncio.wait(tasks, timeout=budget)
        for task in tasks:
            if task not in done or task.cancelled():
                continue
            if (exc := task.exception()) is not None:
                merged.warnings.append(
                    f"{self.name}: a board failed unexpectedly ({type(exc).__name__})"
                )
                continue
            result = task.result()
            merged.jobs.extend(result.jobs)
            merged.warnings.extend(w for w in result.warnings if w not in merged.warnings)
            merged.seen = (merged.seen or 0) + (result.seen or 0)
        if pending:
            merged.pending = len(pending)
            merged.warnings.append(
                f"{self.name}: {len(done)} of {len(tasks)} company boards loaded within the time "
                "limit; the rest keep loading in the background, so repeat the search in a minute "
                "for complete results"
            )
        return merged

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
