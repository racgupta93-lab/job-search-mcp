"""Workday-hosted career sites. UNOFFICIAL: read this before relying on it.

Workday has no documented public API for job postings. This source talks to the same JSON
address that each company's own public career page uses in your browser:

  POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs   (search the list)
  GET  https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}  (one posting)

It exists because the user asked for it, knowing that nothing says this use is permitted and that
Workday may change it without notice. To keep the footprint small and respectful it:
  * only contacts tenants that the user listed, each checked against its own robots.txt first
    (a site that disallows automated access, or whose robots.txt cannot be read, is skipped);
  * uses its own HTTP client with a lower request rate and exact per-tenant host allowlisting;
  * asks each company's server to search for the user's job titles instead of downloading
    everything, and reads at most 5 pages of 20 results per search;
  * downloads full text only for candidate postings (at most 25 per company per search).

What the list returns (learned by testing): title, a relative date ("Posted 3 Days Ago",
"Posted 30+ Days Ago"), a location that is often just "2 Locations", and a requisition id. The
exact date, locations and country arrive only with the full text, so list dates are approximate.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.robotparser import RobotFileParser

from job_search_mcp.http_client import FetchError, NotFoundError, SafeHttpClient
from job_search_mcp.models import Job, LinkStatus, SourceStatus
from job_search_mcp.sanitize import clean_text, html_to_text
from job_search_mcp.sources.base import FetchResult, JobSource, SourceQuery, _quiet
from job_search_mcp.sources.common import normalize_country, parse_iso_time

# "tenant.wdN.Site", for example "nvidia.wd5.NVIDIAExternalCareerSite"
ENTRY = re.compile(r"^([a-z0-9][a-z0-9-]{0,39})\.(wd\d{1,3})\.([A-Za-z0-9][A-Za-z0-9_-]{0,79})$")
_PATH = re.compile(r"^/job/[A-Za-z0-9_\-./%()+,~]{1,300}$")
_REQ = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ROBOTS_AGENT = "job-search-mcp"
PAGE_SIZE = 20  # Workday rejects larger pages
MAX_PAGES = 5
MAX_DETAILS_PER_SITE = 25
CACHE_SECONDS = 1800
DETAIL_CACHE_SIZE = 2000
CONCURRENT = 12
_AGO = re.compile(r"(\d+)(\+?)\s+days?\s+ago", re.IGNORECASE)


@dataclass(frozen=True)
class Site:
    tenant: str
    wd: str
    site: str

    @property
    def host(self) -> str:
        return f"{self.tenant}.{self.wd}.myworkdayjobs.com"

    @property
    def key(self) -> str:
        return f"{self.tenant}.{self.wd}.{self.site}"

    @property
    def api(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    @staticmethod
    def parse(entry: str) -> Site | None:
        match = ENTRY.fullmatch(entry)
        return Site(*match.groups()) if match else None


def hosts_for(entries: list[str]) -> frozenset[str]:
    """The exact hosts to allow for these entries (nothing broader)."""
    return frozenset(s.host for e in entries if (s := Site.parse(e)))


def parse_posted(text: Any, now: datetime) -> datetime | None:
    """ "Posted Today" / "Posted Yesterday" / "Posted 5 Days Ago" / "Posted 30+ Days Ago"."""
    if not isinstance(text, str):
        return None
    low = text.lower()
    if "today" in low:
        return now
    if "yesterday" in low:
        return now - timedelta(days=1)
    match = _AGO.search(low)
    if match:
        days = int(match.group(1)) + (1 if match.group(2) else 0)  # "30+" is shown as 31
        return now - timedelta(days=min(days, 3650))
    return None


def parse_posting(site: Site, raw: Any, now: datetime) -> Job | None:
    """One item of the list -> a Job without a description."""
    if not isinstance(raw, dict):
        return None
    path = raw.get("externalPath")
    title = clean_text(raw.get("title"), 300)
    if not isinstance(path, str) or not _PATH.fullmatch(path) or ".." in path or not title:
        return None
    bullets = raw.get("bulletFields")
    req = (
        next((b for b in bullets if isinstance(b, str) and _REQ.fullmatch(b)), None)
        if isinstance(bullets, list)
        else None
    )
    if req is None:  # fall back to the id at the end of the path ("..._JR2019010-1")
        tail = path.rsplit("_", 1)[-1] if "_" in path else ""
        req = tail if _REQ.fullmatch(tail) else None
    if req is None:
        return None
    location = clean_text(raw.get("locationsText"), 300) or None
    try:
        return Job(
            id=f"workday:{site.key}:{req}",
            source="workday",
            company=site.tenant.replace("-", " ").title(),
            title=title,
            location=location,
            workplace_type="remote" if location and "remote" in location.lower() else None,
            url=f"https://{site.host}/{site.site}{path}",
            posted_at=parse_posted(raw.get("postedOn"), now),
            description_complete=False,
        )
    except ValueError:
        return None


@dataclass
class _Detail:
    description: str
    posted_at: datetime | None
    location: str | None
    country: str | None
    workplace: str | None


def parse_detail(data: Any) -> _Detail | None:
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return None
    extra = [
        clean_text(x, 100) for x in info.get("additionalLocations") or [] if isinstance(x, str)
    ]
    places = [p for p in [clean_text(info.get("location"), 100), *extra][:5] if p]
    country = info.get("country")
    remote = str(info.get("remoteType") or "").lower()
    return _Detail(
        description=html_to_text(info.get("jobDescription"), 20_000),
        posted_at=parse_iso_time(info.get("startDate")),
        location="; ".join(places) or None,
        # With several locations the primary country says little, so rely on the text instead.
        country=None
        if extra
        else normalize_country(country.get("descriptor") if isinstance(country, dict) else None),
        workplace="remote" if "remote" in remote else "hybrid" if "hybrid" in remote else None,
    )


def _summarize(warnings: list[str]) -> list[str]:
    """One line instead of dozens when many companies have more matches than we read."""
    truncated = [w for w in warnings if "matches at '" in w and "read the first" in w]
    capped = [w for w in warnings if "candidates at '" in w and "full text loaded for" in w]
    rest = [w for w in warnings if w not in truncated and w not in capped]

    def companies(n: int) -> str:
        return f"{n} {'company' if n == 1 else 'companies'}"

    if truncated:
        rest.append(
            f"workday: for {companies(len(truncated))} only the first "
            f"{PAGE_SIZE * MAX_PAGES} matches were read (Workday ranks by relevance, not date)"
        )
    if capped:
        rest.append(
            f"workday: full text was loaded for at most {MAX_DETAILS_PER_SITE} candidates at "
            f"{companies(len(capped))}; narrow the search to see the rest"
        )
    return rest


class WorkdaySource(JobSource):
    name = "workday"
    config_hint = (
        "optional, unofficial: add 'tenant.wdN.Site' entries under 'workday:' in companies.yaml"
    )

    def __init__(self, http: SafeHttpClient, entries: list[str]) -> None:
        super().__init__(http)
        self._sites = [s for e in entries if (s := Site.parse(e)) is not None]
        self._by_key = {s.key: s for s in self._sites}
        self._lists: dict[tuple[str, str], tuple[float, list[Job], list[str]]] = {}
        self._details: OrderedDict[str, tuple[float, _Detail | None]] = OrderedDict()
        self._robots: dict[str, tuple[float, bool | None]] = {}
        self._inflight: dict[Any, asyncio.Task[Any]] = {}
        self._gate_loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def company_count(self) -> int:
        return len(self._sites)

    @property
    def is_configured(self) -> bool:
        return bool(self._sites)

    def _gate(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._gate_loop is not loop or self._semaphore is None:
            self._gate_loop, self._semaphore, self._inflight = (
                loop,
                asyncio.Semaphore(CONCURRENT),
                {},
            )
        return self._semaphore

    async def _shared(self, key: Any, make: Callable[[], Any]) -> Any:
        """Run `make()` once per key at a time; every caller gets the same result."""
        self._gate()
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(make())
            task.add_done_callback(_quiet)
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
            self._inflight[key] = task
        return await asyncio.shield(task)

    # ---- robots.txt: skip any site that does not allow automated access -------------------
    async def _permitted(self, site: Site) -> bool | None:
        hit = self._robots.get(site.key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        verdict: bool | None
        try:
            # robots.txt is plain text; some servers answer 406 if it is requested as JSON.
            raw = await self._http.get_bytes(
                f"https://{site.host}/robots.txt", headers={"Accept": "text/plain, */*;q=0.5"}
            )
            parser = RobotFileParser()
            parser.parse(raw.decode("utf-8", errors="replace").splitlines())
            base = f"/wday/cxs/{site.tenant}/{site.site}"
            verdict = parser.can_fetch(ROBOTS_AGENT, f"{base}/jobs") and parser.can_fetch(
                ROBOTS_AGENT, f"{base}/job/US/Example_JR1"
            )
        except NotFoundError:
            verdict = True  # no robots.txt means no restrictions were published
        except FetchError:
            verdict = None  # cannot confirm: do not proceed
        ttl = 6 * 3600 if verdict is not None else 300
        self._robots[site.key] = (time.monotonic() + ttl, verdict)
        return verdict

    # ---- stage 1: the list, searched by the user's job title ---------------------------------
    async def _fetch_list(self, site: Site, phrase: str) -> tuple[list[Job], list[str]]:
        jobs: list[Job] = []
        warnings: list[str] = []
        now = datetime.now(UTC)
        total = 0
        for page in range(MAX_PAGES):
            body = {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": page * PAGE_SIZE,
                "searchText": phrase,
            }
            try:
                # The gate is held per request, so many companies progress at once; each company
                # is a different host and the client still limits the rate per host.
                async with self._gate():
                    data = await self._http.post_json(f"{site.api}/jobs", body)
            except NotFoundError:
                return [], [f"{self.name}: site '{site.key}' not found"]
            except FetchError as exc:
                if jobs:
                    warnings.append(f"{self.name}: {exc}")
                    break
                return [], [f"{self.name}: {exc}"]
            items = data.get("jobPostings") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return jobs, [*warnings, f"{self.name}: unexpected response from '{site.key}'"]
            if page == 0 and isinstance(data.get("total"), int):
                total = data["total"]  # later pages report 0, so keep the first one
            jobs += [j for raw in items if (j := parse_posting(site, raw, now)) is not None]
            if len(items) < PAGE_SIZE or (total and (page + 1) * PAGE_SIZE >= total):
                break
        if total > len(jobs):
            warnings.append(
                f"{self.name}: read the first {len(jobs)} of {total} matches at '{site.key}'"
            )
        return jobs, warnings

    async def _list(self, site: Site, phrase: str) -> tuple[list[Job], list[str]]:
        key = (site.key, phrase.lower())
        hit = self._lists.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1], hit[2]
        jobs, warnings = await self._shared(("list", *key), lambda: self._fetch_list(site, phrase))
        self._lists[key] = (
            time.monotonic() + (CACHE_SECONDS if jobs or not warnings else 300),
            jobs,
            warnings,
        )
        return jobs, warnings

    # ---- stage 2: one posting's full text ---------------------------------------------------
    def _path_of(self, job: Job, site: Site) -> str | None:
        prefix = f"https://{site.host}/{site.site}"
        path = job.url[len(prefix) :] if job.url.startswith(prefix) else ""
        return path if _PATH.fullmatch(path) and ".." not in path else None

    async def _fetch_detail(self, site: Site, path: str) -> _Detail | None:
        async with self._gate():
            try:
                data = await self._http.get_json(f"{site.api}{path}")
            except FetchError:
                return None
        return parse_detail(data)

    async def _detail(self, site: Site, path: str) -> _Detail | None:
        key = f"{site.key}{path}"
        hit = self._details.get(key)
        if hit is not None and hit[0] > time.monotonic():
            self._details.move_to_end(key)
            return hit[1]
        detail = await self._shared(("detail", key), lambda: self._fetch_detail(site, path))
        if detail is not None:
            self._details[key] = (time.monotonic() + CACHE_SECONDS, detail)
            while len(self._details) > DETAIL_CACHE_SIZE:
                self._details.popitem(last=False)
        return detail

    async def _with_detail(self, job: Job) -> Job:
        site = self._by_key.get(job.id.split(":")[1]) if job.id.count(":") == 2 else None
        path = self._path_of(job, site) if site else None
        detail = await self._detail(site, path) if site and path else None
        if detail is None:
            return job.model_copy(update={"description_complete": False})
        update: dict[str, Any] = {"description": detail.description, "description_complete": True}
        if detail.posted_at:
            update["posted_at"] = detail.posted_at
        if detail.location:
            update["location"] = detail.location
            if detail.workplace is None and "remote" in detail.location.lower():
                update["workplace_type"] = "remote"
        if detail.country:
            update["country"] = detail.country
        if detail.workplace:
            update["workplace_type"] = detail.workplace
        return job.model_copy(update=update)

    async def complete_job(self, job: Job) -> Job:
        return job if job.description and job.description_complete else await self._with_detail(job)

    async def check_live(self, job: Job) -> LinkStatus:
        site = self._by_key.get(job.id.split(":")[1]) if job.id.count(":") == 2 else None
        path = self._path_of(job, site) if site else None
        if site is None or path is None or await self._permitted(site) is not True:
            return "unverified"
        try:
            await self._http.get_bytes(f"{site.api}{path}")
        except NotFoundError:
            return "gone"
        except FetchError:
            return "unverified"
        return "live"

    # ---- the source interface -------------------------------------------------------------------
    async def _site_jobs(
        self, site: Site, phrase: str, prefilter: Callable[[Job], bool] | None
    ) -> FetchResult:
        allowed = await self._permitted(site)
        if allowed is not True:
            why = (
                "its robots.txt does not allow automated access"
                if allowed is False
                else "its robots.txt could not be read"
            )
            return FetchResult(
                warnings=[f"{self.name}: skipped '{site.key}' because {why}"], seen=0
            )
        listed, warnings = await self._list(site, phrase)
        survivors = listed if prefilter is None else [j for j in listed if prefilter(j)]
        wanted, rest = survivors[:MAX_DETAILS_PER_SITE], survivors[MAX_DETAILS_PER_SITE:]
        jobs = list(await asyncio.gather(*(self._with_detail(j) for j in wanted)))
        jobs += [j.model_copy(update={"description_complete": False}) for j in rest]
        if rest:
            warnings = [
                *warnings,
                f"{self.name}: full text loaded for {len(wanted)} of {len(survivors)} "
                f"candidates at '{site.key}'; narrow the search to see the rest",
            ]
        return FetchResult(jobs, list(warnings), seen=len(listed))

    async def fetch_jobs(self, query: SourceQuery | None = None) -> FetchResult:
        phrase = (query.keywords or "").strip() if query else ""
        prefilter = query.prefilter if query else None
        budget = query.time_budget if query else None
        tasks = [asyncio.ensure_future(self._site_jobs(s, phrase, prefilter)) for s in self._sites]
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
                    f"{self.name}: a site failed unexpectedly ({type(exc).__name__})"
                )
                continue
            result = task.result()
            merged.jobs.extend(result.jobs)
            merged.warnings.extend(w for w in result.warnings if w not in merged.warnings)
            merged.seen = (merged.seen or 0) + (result.seen or 0)
        merged.warnings = _summarize(merged.warnings)
        if pending:
            merged.pending = len(pending)
            merged.warnings.append(
                f"{self.name}: {len(done)} of {len(tasks)} sites answered within the time limit; "
                "repeat the search for the rest"
            )
        return merged

    async def check(self) -> SourceStatus:
        if not self._sites:
            return SourceStatus(
                name=self.name, companies=0, status="not_configured", detail=self.config_hint
            )
        site = self._sites[0]
        if await self._permitted(site) is not True:
            return SourceStatus(
                name=self.name,
                companies=len(self._sites),
                status="error",
                detail=f"robots.txt of '{site.key}' does not allow it or could not be read",
            )
        try:
            await self._http.post_json(
                f"{site.api}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
            )
        except FetchError as exc:
            return SourceStatus(
                name=self.name, companies=len(self._sites), status="error", detail=str(exc)
            )
        return SourceStatus(
            name=self.name,
            companies=len(self._sites),
            status="ok",
            detail=f"reachable (checked '{site.key}'); unofficial endpoint, see README",
        )
