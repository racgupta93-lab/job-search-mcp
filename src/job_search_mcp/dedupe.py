"""Remove duplicate postings.

Two kinds of duplicate:
  1. the same posting repeated within a source (same id or same link);
  2. the same job seen through two sources (for example a company's own
     Greenhouse board and The Muse): same company, same title, and overlapping
     or unstated location.

Postings with the same title in *different cities* at one company are separate
jobs, and the same title twice within one source is never merged by (2).
The copy kept is the one from the most direct source (the employer's own board).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from job_search_mcp.models import Job

# Lower number = preferred (the employer's own board, then aggregators).
_SOURCE_RANK = {"greenhouse": 0, "lever": 0, "lever_eu": 0, "ashby": 0, "muse": 1, "adzuna": 2}
_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh|the)\b\.?", re.I)
_BRACKETS = re.compile(r"[\(\[].*?[\)\]]")


def _norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _COMPANY_SUFFIX.sub(" ", name.lower()))


def _norm_title(title: str) -> str:
    text = _BRACKETS.sub(" ", title.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _cities(location: str | None) -> set[str]:
    if not location:
        return set()
    parts = re.split(r"[;|•/]", location.lower())
    return {p.split(",")[0].strip() for p in parts if p.strip()} - {""}


def _locations_overlap(a: Job, b: Job) -> bool:
    ca, cb = _cities(a.location), _cities(b.location)
    unspecific = {"remote", "flexible", "anywhere", "united states", "us", "usa"}
    if not ca or not cb or (ca | cb) & unspecific:
        return True  # an unstated or generic location can't tell the jobs apart
    return bool(ca & cb)


_TRACKING = re.compile(
    r"^(utm_.*|gh_src|src|source|ref|referrer|lever-source.*|fbclid|gclid|se)$", re.I
)


def _canonical_url(url: str) -> str:
    """host + path + the query parameters that identify the job.

    Many career sites serve every job from one path and tell them apart only by
    a query parameter (for example https://example.com/jobs?gh_jid=123), so the
    query must be kept. Tracking parameters are dropped.
    """
    parts = urlsplit(url)
    query = sorted(
        (k.lower(), v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(k)
    )
    tail = "&".join(f"{k}={v}" for k, v in query)
    base = f"{(parts.hostname or '').lower()}{parts.path.rstrip('/')}"
    return f"{base}?{tail}" if tail else base


def _rank(job: Job) -> int:
    return _SOURCE_RANK.get(job.source, 5)


def dedupe(jobs: list[Job]) -> tuple[list[Job], int]:
    """Return (unique jobs in original order, number removed)."""
    kept: list[Job] = []
    seen_ids: dict[str, int] = {}
    seen_urls: dict[str, int] = {}
    groups: dict[tuple[str, str], list[int]] = {}
    removed = 0

    for job in jobs:
        url_key = _canonical_url(job.url)
        # (1) exact repeats within the feed
        if job.id in seen_ids or url_key in seen_urls:
            removed += 1
            continue
        # (2) same job through a different source
        key = (_norm_company(job.company), _norm_title(job.title))
        match = next(
            (
                i
                for i in groups.get(key, [])
                if kept[i].source != job.source and _locations_overlap(kept[i], job)
            ),
            None,
        )
        if match is not None:
            removed += 1
            existing = kept[match]
            winner, loser = (job, existing) if _rank(job) < _rank(existing) else (existing, job)
            also = sorted({*winner.also_on, *loser.also_on, loser.source} - {winner.source})
            kept[match] = winner.model_copy(update={"also_on": also[:6]})
            continue
        seen_ids[job.id] = len(kept)
        seen_urls[url_key] = len(kept)
        groups.setdefault(key, []).append(len(kept))
        kept.append(job)
    return kept, removed
