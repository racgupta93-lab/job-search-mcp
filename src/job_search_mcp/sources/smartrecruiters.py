"""SmartRecruiters public Posting API (no key needed for reading public postings).

Docs: https://developers.smartrecruiters.com/docs/posting-api
List: GET https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=N&country=us
Item: GET https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}

Notes learned by testing the live API:
  * identifiers are case-insensitive; results come newest first; limit is at most 100;
  * an unknown company returns an empty list (HTTP 200), not a 404;
  * the list has title, date, structured location (with remote/hybrid flags) and level,
    but no description, so full text is fetched posting by posting, only for candidates;
  * there is no pay field (salary comes from text in the description, when present);
  * only US postings are requested (country=us), and at most the newest 500 per company.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

from job_search_mcp.http_client import FetchError, NotFoundError
from job_search_mcp.models import Job
from job_search_mcp.sanitize import clean_text, html_to_text
from job_search_mcp.sources.base import BoardSource, _Loaded, _quiet
from job_search_mcp.sources.common import normalize_country, parse_iso_time

BASE_URL = "https://api.smartrecruiters.com/v1/companies"
PAGE_SIZE = 100
MAX_PAGES = 5  # the newest 500 US postings per company
MAX_DETAILS_PER_BOARD = 60  # full text is fetched for at most this many candidates per search
DETAIL_CACHE_SIZE = 3000
_LEVEL_HINTS = {
    "internship": "internship",
    "entry_level": "entry",
    "associate": "entry",
    "director": "management",
    "executive": "management",
}


def _location(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, dict):
        return None, None
    text = clean_text(raw.get("fullLocation"), 300)
    if not text:
        parts = [clean_text(raw.get(k), 80) for k in ("city", "region", "country")]
        text = ", ".join(p for p in parts if p)
    return text or None, normalize_country(raw.get("country"))


def _workplace(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("remote") is True:
        return "remote"
    if raw.get("hybrid") is True:
        return "hybrid"
    if raw.get("remote") is False and raw.get("hybrid") is False:
        return "onsite"
    return None


def parse_posting(board: str, raw: Any) -> Job | None:
    """One item of the postings list -> a Job without a description."""
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id")
    title = clean_text(raw.get("name"), 300)
    if not isinstance(job_id, str) or not title or not job_id.isalnum():
        return None
    company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
    identifier = company.get("identifier") if isinstance(company.get("identifier"), str) else board
    level = raw.get("experienceLevel") if isinstance(raw.get("experienceLevel"), dict) else {}
    labels = [
        clean_text(raw.get(key, {}).get("label"), 100)
        for key in ("department", "function")
        if isinstance(raw.get(key), dict)
    ]
    location, country = _location(raw.get("location"))
    slug = identifier if identifier.isalnum() else board
    try:
        return Job(
            id=f"smartrecruiters:{board}:{job_id}",
            source="smartrecruiters",
            company=clean_text(company.get("name"), 200) or board,
            title=title,
            location=location,
            country=country,
            workplace_type=_workplace(raw.get("location")),  # type: ignore[arg-type]
            url=f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
            posted_at=parse_iso_time(raw.get("releasedDate")),
            departments=[label for label in labels if label],
            level_hint=_LEVEL_HINTS.get(str(level.get("id") or "")),
        )
    except ValueError:
        return None


def parse_description(detail: Any) -> str:
    """The four sections of a posting's ad, as plain text."""
    ad = detail.get("jobAd") if isinstance(detail, dict) else None
    sections = ad.get("sections") if isinstance(ad, dict) else None
    if not isinstance(sections, dict):
        return ""
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
        section = sections.get(key)
        if isinstance(section, dict) and isinstance(section.get("text"), str):
            title = clean_text(section.get("title"), 100)
            body = html_to_text(section["text"], 12_000)
            if body:
                parts.append(f"{title}\n{body}" if title else body)
    return "\n\n".join(parts)[:20_000]


class SmartRecruitersSource(BoardSource):
    """Two-stage, but the second stage is per posting rather than per company."""

    name = "smartrecruiters"
    two_stage = True
    whole_board_content = False
    config_hint = "optional: add company names under 'smartrecruiters:' in companies.yaml"

    def __init__(self, http, boards: list[str]) -> None:
        super().__init__(http, boards)
        self._details: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._detail_tasks: dict[str, asyncio.Task[str | None]] = {}

    def _list_url(self, board: str) -> str:
        return f"{BASE_URL}/{board}/postings"

    def _check_url(self, board: str) -> str:
        return f"{BASE_URL}/{board}/postings?limit=1"

    def _extract(self, data: Any) -> list[Any] | None:
        items = data.get("content") if isinstance(data, dict) else None
        return items if isinstance(items, list) else None

    def _parse(self, board: str, raw: Any) -> Job | None:
        return parse_posting(board, raw)

    # ---- stage 1: the list (paged, newest first, US only) --------------------------------
    async def _download(self, kind: str, board: str) -> _Loaded:
        jobs: list[Job] = []
        warnings: list[str] = []
        total = 0
        async with self._gate():
            for page in range(MAX_PAGES):
                try:
                    data = await self._http.get_json(
                        self._list_url(board),
                        params={"limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "country": "us"},
                    )
                except NotFoundError:
                    return _Loaded([], [f"{self.name}: company '{board}' not found"], ok=False)
                except FetchError as exc:
                    if jobs:
                        warnings.append(f"{self.name}: {exc}")
                        break
                    return _Loaded([], [f"{self.name}: {exc}"], ok=False)
                items = self._extract(data)
                if items is None:
                    return _Loaded(
                        [], [f"{self.name}: unexpected response for '{board}'"], ok=False
                    )
                total = data.get("totalFound") if isinstance(data.get("totalFound"), int) else 0
                jobs += [j for raw in items if (j := parse_posting(board, raw)) is not None]
                if len(items) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= total:
                    break
        if not jobs and total == 0:
            warnings.append(f"{self.name}: no open US postings found for '{board}' (or wrong name)")
        elif total > len(jobs):
            warnings.append(
                f"{self.name}: read the newest {len(jobs)} of {total} US postings at '{board}'"
            )
        return _Loaded(jobs, warnings)

    async def fetch_board(self, board: str) -> list[Job]:
        return list((await self._shared("list", board)).jobs)

    # ---- stage 2: full text, one posting at a time ---------------------------------------
    async def _detail_text(self, board: str, native: str) -> str | None:
        key = f"{board}:{native}"
        hit = self._details.get(key)
        if hit is not None and hit[0] > time.monotonic():
            self._details.move_to_end(key)
            return hit[1]
        self._gate()  # keeps in-flight bookkeeping tied to the running loop
        task = self._detail_tasks.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._fetch_detail(board, native, key))
            task.add_done_callback(_quiet)
            task.add_done_callback(lambda _t, k=key: self._detail_tasks.pop(k, None))
            self._detail_tasks[key] = task
        return await asyncio.shield(task)

    async def _fetch_detail(self, board: str, native: str, key: str) -> str | None:
        async with self._gate():
            try:
                data = await self._http.get_json(f"{BASE_URL}/{board}/postings/{native}")
            except FetchError:
                return None
        text = parse_description(data)
        self._details[key] = (time.monotonic() + self.cache_ttl_seconds, text)
        self._details.move_to_end(key)
        while len(self._details) > DETAIL_CACHE_SIZE:
            self._details.popitem(last=False)
        return text

    async def _with_text(self, job: Job) -> Job:
        parts = job.id.split(":")
        text = await self._detail_text(parts[1], parts[2]) if len(parts) == 3 else None
        if text is None:
            return job.model_copy(update={"description_complete": False})
        return job.model_copy(update={"description": text, "description_complete": True})

    async def _complete(self, board: str, survivors: list[Job]) -> tuple[list[Job], list[str]]:
        wanted, rest = survivors[:MAX_DETAILS_PER_BOARD], survivors[MAX_DETAILS_PER_BOARD:]
        jobs = list(await asyncio.gather(*(self._with_text(j) for j in wanted)))
        jobs += [j.model_copy(update={"description_complete": False}) for j in rest]
        warnings = []
        if rest:
            warnings.append(
                f"{self.name}: full text loaded for {len(wanted)} of {len(survivors)} "
                f"candidates at '{board}'; narrow the search to see the rest"
            )
        return jobs, warnings

    async def complete_job(self, job: Job) -> Job:
        return job if job.description_complete and job.description else await self._with_text(job)
