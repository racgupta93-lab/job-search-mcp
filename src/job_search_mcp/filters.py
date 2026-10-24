"""The search filters: small, pure functions so each rule can be tested alone.

Every filter that depends on information a posting may not have (salary, level,
location, workplace type) takes an explicit "include the unknown ones?" switch,
so the caller always decides what a missing value means. Nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from job_search_mcp.models import Job

_WORD = re.compile(r"[a-z0-9+#]+")
_HOURS_PER_YEAR = 2080  # 40 hours x 52 weeks
_PER_YEAR = {"year": 1, "month": 12, "hour": _HOURS_PER_YEAR}


@dataclass
class SearchFilters:
    job_titles: list[str] = field(default_factory=list)
    search_description: bool = False
    levels: list[str] = field(default_factory=list)
    include_unspecified_level: bool | None = None  # None: automatic (see level_matches)
    salary_min: int | None = None
    salary_max: int | None = None
    include_no_salary: bool = True
    include_estimated_salaries: bool = False
    location: str | None = None
    include_remote_with_location: bool = True
    workplace: list[str] = field(default_factory=list)
    include_unspecified_workplace: bool = False
    us_only: bool = True
    include_unknown_location: bool = True
    posted_within_days: int = 30
    include_undated: bool = True
    work_authorization: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    sort_by: str = "newest"
    limit: int = 20
    offset: int = 0
    verify_links: bool = False


def _words(text: str) -> set[str]:
    """Lower-case words with a trailing plural 's' removed, so 'analysts' == 'analyst'."""
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _WORD.findall(text.lower())}


def title_matches(job: Job, phrases: list[str], search_description: bool = False) -> bool:
    """True if ANY phrase has ALL its words in the title (or, optionally, the description).

    The department is deliberately NOT used: a source's broad category (such as The Muse's
    "Data and Analytics") would let any "...Engineer" match "analytics engineer".
    """
    if not phrases:
        return True
    haystack = _words(job.title)
    if search_description:
        haystack |= _words(job.description)
    return any((need := _words(phrase)) and need <= haystack for phrase in phrases)


def location_matches(job: Job, location: str | None, include_remote: bool = True) -> bool:
    """Match "Austin", "Austin, TX", "TX" or "California" against the posting's location."""
    if not location or not location.strip():
        return True
    if include_remote and job.workplace_type == "remote":
        return True
    text = job.location or ""
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if not parts:
        return True
    for part in parts:
        if len(part) == 2 and part.isalpha():
            # State codes are matched case-sensitively so "OR" is never the word "or".
            pattern, flags = re.escape(part.upper()), 0
        else:
            pattern, flags = re.escape(part), re.IGNORECASE
        if not re.search(rf"(?<![A-Za-z]){pattern}(?![A-Za-z])", text, flags):
            return False
    return True


def annual_usd_range(job: Job) -> tuple[float, float] | None:
    """The posting's pay as a yearly USD range, or None if it can't be compared.

    Monthly pay is x12 and hourly pay is x2080. Other currencies are not converted
    (that would be a guess), so they count as "no comparable salary".
    """
    if job.salary_min is None and job.salary_max is None:
        return None
    if job.salary_currency != "USD" or job.salary_period not in _PER_YEAR:
        return None
    factor = _PER_YEAR[job.salary_period]
    low = job.salary_min if job.salary_min is not None else job.salary_max
    high = job.salary_max if job.salary_max is not None else job.salary_min
    return low * factor, high * factor  # type: ignore[operator]


def salary_matches(
    job: Job,
    salary_min: int | None,
    salary_max: int | None,
    include_no_salary: bool = True,
    include_estimated: bool = False,
) -> bool:
    """Yearly pay ranges must overlap the requested range."""
    if salary_min is None and salary_max is None:
        return True
    comparable = annual_usd_range(job)
    if comparable is None or (job.salary_basis == "estimated" and not include_estimated):
        return include_no_salary
    low, high = comparable
    if salary_min is not None and high < salary_min:
        return False
    return not (salary_max is not None and low > salary_max)


def level_matches(job: Job, levels: list[str], include_unspecified: bool | None = None) -> bool:
    """Match the title-based level guess.

    A bare title such as "Data Analyst" has no level. By default those are kept only
    when "mid" is requested (plain titles usually are mid-level) and dropped otherwise.
    """
    if not levels:
        return True
    if job.level is None:
        return include_unspecified if include_unspecified is not None else "mid" in levels
    return job.level in levels


def workplace_matches(job: Job, workplaces: list[str], include_unspecified: bool = False) -> bool:
    if not workplaces:
        return True
    if job.workplace_type is None:
        return include_unspecified
    return job.workplace_type in workplaces


def us_matches(job: Job, us_only: bool, include_unknown: bool = True) -> bool:
    if not us_only or job.us_location is True:
        return True
    return include_unknown if job.us_location is None else False
