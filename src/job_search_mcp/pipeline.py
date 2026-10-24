"""Turn raw postings from the sources into clean, enriched, de-duplicated ones.

Order matters: enrich first (so duplicates can be compared on clean fields),
drop old postings, then remove duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from job_search_mcp.dedupe import dedupe
from job_search_mcp.injection import FLAG as INJECTION_FLAG
from job_search_mcp.injection import looks_like_injection
from job_search_mcp.levels import detect_level
from job_search_mcp.location import us_location
from job_search_mcp.models import Job
from job_search_mcp.salary_text import extract_salary


@dataclass
class PrepareResult:
    jobs: list[Job]
    dropped_old: int = 0
    dropped_duplicates: int = 0
    undated: int = 0


def enrich(job: Job) -> Job:
    """Add the computed fields. Never overwrites a salary the source gave us."""
    updates: dict[str, object] = {}
    if job.salary_min is None and job.salary_max is None:
        updates.update(extract_salary(job.description))
    level, basis = detect_level(job.title, job.level_hint)
    updates["level"] = level
    updates["level_basis"] = basis
    updates["us_location"] = us_location(job.country, job.location)
    if looks_like_injection(job.title, job.description) and INJECTION_FLAG not in job.flags:
        updates["flags"] = [*job.flags, INJECTION_FLAG]
    return job.model_copy(update=updates)


def prepare_jobs(
    raw_jobs: list[Job],
    *,
    max_age_days: int,
    now: datetime | None = None,
    include_undated: bool = True,
) -> PrepareResult:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=max_age_days)
    fresh: list[Job] = []
    dropped_old = undated = 0
    for raw in raw_jobs:
        # Age is checked BEFORE the (comparatively slow) enrichment: most postings are old.
        if raw.posted_at is None:
            undated += 1
            if not include_undated:
                continue
        elif raw.posted_at < cutoff:
            dropped_old += 1
            continue
        fresh.append(enrich(raw))
    unique, duplicates = dedupe(fresh)
    return PrepareResult(
        jobs=unique, dropped_old=dropped_old, dropped_duplicates=duplicates, undated=undated
    )
