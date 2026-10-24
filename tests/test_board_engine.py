"""The company-board engine: two-stage fetching, caching, sharing and the time budget."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import job_search_mcp.search as search_module
from job_search_mcp.app import App
from job_search_mcp.config import ConfigError, Settings, load_settings
from job_search_mcp.filters import (
    SearchFilters,
    level_matches,
    location_matches,
    title_matches,
    us_matches,
    workplace_matches,
)
from job_search_mcp.http_client import SafeHttpClient
from job_search_mcp.models import Job
from job_search_mcp.pipeline import enrich
from job_search_mcp.search import _make_prefilter, run_search
from job_search_mcp.sources.base import SourceQuery
from job_search_mcp.sources.greenhouse import GreenhouseSource
from tests.helpers import make_http, no_sleep

NOW = datetime.now(UTC)


def iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def raw(job_id, title, location="Austin, TX", days_ago=2, content=None):
    out = {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://careers.example.com/{job_id}",
        "company_name": "Acme",
        "location": {"name": location},
        "first_published": iso(days_ago),
        "updated_at": iso(days_ago),
    }
    if content is not None:
        out["content"] = content
    return out


JOBS = [
    ("Senior Data Analyst", "Austin, TX", 2, "We do not sponsor visas."),
    ("Account Executive", "Austin, TX", 3, "Sell things."),
    ("Data Analyst", "London, United Kingdom", 4, "Data work."),
    ("Data Analyst", "Austin, TX", 90, "Old posting."),
]


class Recorder:
    """A fake Greenhouse that records every request it receives."""

    def __init__(self, boards=None, fail_content=False):
        self.requests: list[str] = []
        self.boards = boards or {"acme": JOBS}
        self.fail_content = fail_content

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        board = request.url.path.split("/")[3]
        if board not in self.boards:
            return httpx.Response(404)
        full = "content=true" in str(request.url)
        if full and self.fail_content:
            return httpx.Response(500)
        items = [
            raw(i + 1, title, loc, days, content=text if full else None)
            for i, (title, loc, days, text) in enumerate(self.boards[board])
        ]
        return httpx.Response(200, json={"jobs": items})

    @property
    def light(self):
        return [u for u in self.requests if "content=true" not in u]

    @property
    def full(self):
        return [u for u in self.requests if "content=true" in u]


def source(rec, boards=("acme",)):
    return GreenhouseSource(make_http(rec), list(boards))


def prefilter_for(**kw):
    return _make_prefilter(SearchFilters(**kw), {})


def fetch(src, **query):
    return asyncio.run(src.fetch_jobs(SourceQuery(**query)))


# ---- two-stage fetching --------------------------------------------------------------


def test_light_list_first_then_full_text_only_for_survivors():
    rec = Recorder()
    result = fetch(source(rec), prefilter=prefilter_for(job_titles=["data analyst"]))
    assert len(rec.light) == 1 and len(rec.full) == 1
    assert [j.title for j in result.jobs] == ["Senior Data Analyst"]  # old/UK/other title gone
    assert result.jobs[0].description == "We do not sponsor visas."  # full text is there
    assert result.seen == 4


def test_boards_without_candidates_never_download_full_text():
    rec = Recorder(boards={"acme": JOBS, "other": [("Chef", "Austin, TX", 1, "Cook.")]})
    result = fetch(
        source(rec, ("acme", "other")), prefilter=prefilter_for(job_titles=["data analyst"])
    )
    assert len(rec.light) == 2
    assert len(rec.full) == 1 and "/boards/acme/" in rec.full[0]
    assert result.seen == 5


def test_without_a_prefilter_it_goes_straight_to_full_text():
    rec = Recorder()
    result = fetch(source(rec))
    assert rec.light == [] and len(rec.full) == 1
    assert len(result.jobs) == 4


def test_if_full_text_cannot_be_loaded_candidates_are_kept_and_flagged():
    rec = Recorder(fail_content=True)
    result = fetch(source(rec), prefilter=prefilter_for(job_titles=["data analyst"]))
    assert [j.title for j in result.jobs] == ["Senior Data Analyst"]
    assert result.jobs[0].description_complete is False
    assert result.jobs[0].description == ""
    assert any("500" in w or "failed" in w for w in result.warnings)


# ---- caching and sharing ---------------------------------------------------------------


def test_second_search_makes_no_requests():
    rec = Recorder()
    src = source(rec)
    pre = prefilter_for(job_titles=["data analyst"])
    fetch(src, prefilter=pre)
    before = len(rec.requests)
    result = fetch(src, prefilter=pre)
    assert len(rec.requests) == before
    assert len(result.jobs) == 1


def test_a_different_search_reuses_the_cached_light_list():
    rec = Recorder()
    src = source(rec)
    fetch(src, prefilter=prefilter_for(job_titles=["data analyst"]))
    fetch(src, prefilter=prefilter_for(job_titles=["account executive"]))
    assert len(rec.light) == 1  # the list was downloaded once
    assert len(rec.full) == 1  # and the full text (same board) once


def test_missing_boards_are_remembered_for_a_while_not_re_requested():
    rec = Recorder()
    src = source(rec, ("ghost",))
    first = fetch(src, prefilter=prefilter_for())
    second = fetch(src, prefilter=prefilter_for())
    assert len(rec.requests) == 1
    assert "'ghost' not found" in first.warnings[0] and second.warnings == first.warnings


def test_concurrent_searches_share_one_download_per_board():
    rec = Recorder()
    src = source(rec)

    async def both():
        pre = prefilter_for(job_titles=["data analyst"])
        return await asyncio.gather(
            src.fetch_jobs(SourceQuery(prefilter=pre)), src.fetch_jobs(SourceQuery(prefilter=pre))
        )

    first, second = asyncio.run(both())
    assert len(rec.light) == 1 and len(rec.full) == 1
    assert len(first.jobs) == len(second.jobs) == 1


def test_full_text_cache_is_bounded():
    boards = {f"b{i}": [("Data Analyst", "Austin, TX", 1, "x")] for i in range(5)}
    rec = Recorder(boards=boards)
    src = source(rec, tuple(boards))
    src.content_cache_boards = 2
    fetch(src, prefilter=prefilter_for(job_titles=["data analyst"]))
    assert len(src._contents) == 2  # the 3 oldest were dropped
    assert len(src._lists) == 5  # the small lists stay


def test_fetch_board_and_has_board():
    rec = Recorder()
    src = source(rec)
    assert src.has_board("acme") and not src.has_board("evil")
    jobs = asyncio.run(src.fetch_board("acme"))
    assert len(jobs) == 4 and jobs[0].description


# ---- the time budget -----------------------------------------------------------------


class SlowTransport(httpx.AsyncBaseTransport):
    def __init__(self, recorder, slow_boards, delay):
        self.recorder, self.slow, self.delay = recorder, slow_boards, delay

    async def handle_async_request(self, request):
        if request.url.path.split("/")[3] in self.slow:
            await asyncio.sleep(self.delay)
        return self.recorder(request)


def slow_source(rec, boards, slow, delay):
    http = SafeHttpClient(
        max_response_bytes=10**7,
        requests_per_second=1000,
        transport=SlowTransport(rec, slow, delay),
        sleep=no_sleep,
    )
    return GreenhouseSource(http, list(boards))


def test_budget_returns_what_has_loaded_and_the_rest_finishes_in_the_background():
    boards = {"fast": JOBS, "slow": JOBS}
    rec = Recorder(boards=boards)
    src = slow_source(rec, boards, slow={"slow"}, delay=0.4)

    async def scenario():
        pre = prefilter_for(job_titles=["data analyst"])
        first = await src.fetch_jobs(SourceQuery(prefilter=pre, time_budget=0.1))
        await asyncio.sleep(1.0)  # the slow board finishes meanwhile
        before = len(rec.requests)
        second = await src.fetch_jobs(SourceQuery(prefilter=pre, time_budget=0.1))
        return first, second, before, len(rec.requests)

    first, second, before, after = asyncio.run(scenario())
    assert first.pending == 1 and len(first.jobs) == 1
    assert any("1 of 2 company boards loaded" in w for w in first.warnings)
    assert second.pending == 0 and len(second.jobs) == 2  # now complete, from the cache
    assert before == after  # and nothing was downloaded twice


def test_no_budget_means_wait_for_everything():
    boards = {"a": JOBS, "b": JOBS}
    rec = Recorder(boards=boards)
    src = slow_source(rec, boards, slow={"a", "b"}, delay=0.15)
    result = asyncio.run(src.fetch_jobs(SourceQuery(prefilter=prefilter_for(job_titles=["data"]))))
    assert result.pending == 0 and len(result.jobs) == 2


# ---- the prefilter must NEVER drop what the final filters would keep ---------------------


def _job(title, location, days_ago, **kw):
    return Job(
        id=f"greenhouse:acme:{abs(hash((title, location, days_ago))) % 10**6}",
        source="greenhouse",
        company="Acme",
        title=title,
        location=location,
        workplace_type="remote" if location and "remote" in location.lower() else None,
        url="https://x.example/1",
        posted_at=None if days_ago is None else NOW - timedelta(days=days_ago),
        **kw,
    )


TITLES = [
    "Senior Data Analyst",
    "Data Analyst II",
    "Junior Analytics Engineer",
    "Staff Engineer",
    "Director of Analytics",
    "Account Executive",
    "Data Analyst Intern",
    "Analyst",
    "Sr. Data Scientist",
]
LOCATIONS = [
    "Austin, TX",
    "Remote - US",
    "London, United Kingdom",
    "Flexible / Remote",
    None,
    "New York, NY",
    "Berlin, Germany",
    "Portland, OR",
]
AGES = [1, 10, 29, 31, 200, None]
FILTERS = [
    {},
    {"job_titles": ["data analyst"]},
    {"job_titles": ["data analyst", "analytics engineer"], "levels": ["senior", "mid"]},
    {"levels": ["junior"], "include_unspecified_level": True},
    {"location": "Austin"},
    {"location": "TX", "include_remote_with_location": False},
    {"workplace": ["remote"]},
    {"workplace": ["remote"], "include_unspecified_workplace": True},
    {"us_only": False},
    {"include_unknown_location": False},
    {"include_undated": False},
    {"posted_within_days": 7},
    {"job_titles": ["data"], "search_description": True},
]


def _final_keeps(job, f):
    """What run_search decides for a posting once everything is known (no salary/auth)."""
    if job.posted_at is None:
        if not f.include_undated:
            return False
    elif job.posted_at < NOW - timedelta(days=f.posted_within_days):
        return False
    j = enrich(job)
    return (
        us_matches(j, f.us_only, f.include_unknown_location)
        and title_matches(j, f.job_titles, f.search_description)
        and level_matches(j, f.levels, f.include_unspecified_level)
        and workplace_matches(j, f.workplace, f.include_unspecified_workplace)
        and location_matches(j, f.location, f.include_remote_with_location)
    )


@pytest.mark.parametrize("kwargs", FILTERS)
def test_prefilter_never_drops_a_posting_the_final_filters_keep(kwargs):
    f = SearchFilters(**kwargs)
    prefilter = _make_prefilter(f, {})
    for title in TITLES:
        for location in LOCATIONS:
            for age in AGES:
                job = _job(title, location, age)
                if _final_keeps(job, f):
                    assert prefilter(job), (kwargs, title, location, age)


def test_prefilter_counts_old_postings():
    stats: dict[str, int] = {}
    keep = _make_prefilter(SearchFilters(posted_within_days=30), stats)
    keep(_job("Data Analyst", "Austin, TX", 100))
    keep(_job("Data Analyst", "Austin, TX", 5))
    assert stats["old"] == 1


# ---- end to end through run_search with the real Greenhouse source --------------------


def make_app(src):
    settings = Settings(home=".", companies_file="c.yaml")  # type: ignore[arg-type]
    return App(settings=settings, http=src._http, sources=[src])


def test_search_funnel_counts_early_filtered_postings():
    rec = Recorder()
    app = make_app(source(rec))
    result = asyncio.run(
        run_search(app, SearchFilters(job_titles=["data analyst"], posted_within_days=30))
    )
    assert [j.title for j in result.jobs] == ["Senior Data Analyst"]
    f = result.funnel
    assert f.scanned == 4 and f.removed_too_old == 1 and f.matched_filters == 1
    assert result.jobs[0].work_authorization.classification == "no_sponsorship"


def test_search_reports_boards_that_were_still_loading(monkeypatch):
    boards = {"fast": JOBS, "slow": JOBS}
    rec = Recorder(boards=boards)
    src = slow_source(rec, boards, slow={"slow"}, delay=0.5)
    monkeypatch.setattr(search_module, "SEARCH_TIME_BUDGET_SECONDS", 0.1)
    result = asyncio.run(run_search(make_app(src), SearchFilters(job_titles=["data analyst"])))
    assert len(result.jobs) == 1
    assert any("keep loading in the background" in w for w in result.warnings)


# ---- configuration -------------------------------------------------------------------


def test_request_rate_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_HOME", str(tmp_path))
    assert load_settings().requests_per_second == 5.0
    monkeypatch.setenv("JOB_SEARCH_REQUESTS_PER_SECOND", "8")
    assert load_settings().requests_per_second == 8.0
    for bad in ("0", "50", "fast"):
        monkeypatch.setenv("JOB_SEARCH_REQUESTS_PER_SECOND", bad)
        with pytest.raises(ConfigError):
            load_settings()


def test_a_few_hundred_companies_are_allowed_but_not_unlimited(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_HOME", str(tmp_path))
    (tmp_path / "companies.yaml").write_text(
        "greenhouse: [" + ",".join(f"c{i}" for i in range(500)) + "]"
    )
    assert len(load_settings().companies.greenhouse) == 500
    (tmp_path / "companies.yaml").write_text(
        "greenhouse: [" + ",".join(f"c{i}" for i in range(1001)) + "]"
    )
    with pytest.raises(ConfigError):
        load_settings()
