import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from job_search_mcp.app import App
from job_search_mcp.config import Settings
from job_search_mcp.filters import SearchFilters
from job_search_mcp.search import check_link, get_details, run_search
from job_search_mcp.sources.base import SourceQuery
from job_search_mcp.sources.smartrecruiters import (
    MAX_DETAILS_PER_BOARD,
    SmartRecruitersSource,
    parse_description,
    parse_posting,
)
from tests.helpers import make_http

NOW = datetime.now(UTC)


def item(n, name="Senior Data Analyst", days_ago=2, **loc):
    return {
        "id": f"7440001{n:08d}",
        "name": name,
        "releasedDate": (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"),
        "company": {"identifier": "Acme", "name": "Acme Corp"},
        "location": {
            "city": "Austin",
            "region": "TX",
            "country": "us",
            "fullLocation": "Austin, TX, United States",
            "remote": False,
            "hybrid": True,
            **loc,
        },
        "department": {"label": "Data"},
        "function": {},
        "experienceLevel": {"id": "associate", "label": "Associate"},
    }


DETAIL = {
    "jobAd": {
        "sections": {
            "companyDescription": {"title": "Company", "text": "<p>We build things.</p>"},
            "jobDescription": {"title": "Job", "text": "<p>Analyse <b>data</b>.</p>"},
            "qualifications": {"title": "You", "text": "<ul><li>SQL</li></ul>"},
            "additionalInformation": {
                "title": "More",
                "text": "<p>We do not offer visa sponsorship.</p>",
            },
        }
    }
}


class FakeSR:
    """A fake SmartRecruiters API that records requests."""

    def __init__(self, total=3, detail_fails=False):
        self.requests: list[httpx.URL] = []
        self.total, self.detail_fails = total, detail_fails

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        parts = request.url.path.strip("/").split("/")  # v1 companies <co> postings [<id>]
        if parts[2] == "ghost":
            return httpx.Response(200, json={"totalFound": 0, "content": []})
        if len(parts) == 5:  # a single posting
            return httpx.Response(500) if self.detail_fails else httpx.Response(200, json=DETAIL)
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 100))
        end = min(self.total, offset + limit)
        content = [item(i, f"Role {i}") for i in range(offset, end)]
        return httpx.Response(200, json={"totalFound": self.total, "content": content})

    @property
    def lists(self):
        return [u for u in self.requests if len(u.path.strip("/").split("/")) == 4]

    @property
    def details(self):
        return [u for u in self.requests if len(u.path.strip("/").split("/")) == 5]


def source(api, boards=("acme",)):
    return SmartRecruitersSource(make_http(api), list(boards))


def fetch(src, **query):
    return asyncio.run(src.fetch_jobs(SourceQuery(**query)))


# ---- parsing -----------------------------------------------------------------------


def test_parse_posting():
    job = parse_posting("acme", item(5))
    assert job.id == "smartrecruiters:acme:744000100000005"
    assert job.source == "smartrecruiters" and job.company == "Acme Corp"
    assert job.location == "Austin, TX, United States" and job.country == "US"
    assert job.workplace_type == "hybrid"
    assert job.url == "https://jobs.smartrecruiters.com/Acme/744000100000005"
    assert job.level_hint == "entry" and job.departments == ["Data"]
    assert job.posted_at is not None and job.description == ""
    assert job.salary_min is None  # the API has no pay field: never invented


def test_workplace_flags():
    assert parse_posting("a", item(1, remote=True, hybrid=False)).workplace_type == "remote"
    assert parse_posting("a", item(1, remote=False, hybrid=False)).workplace_type == "onsite"
    assert parse_posting("a", item(1, remote=None, hybrid=None)).workplace_type is None


def test_parse_rejects_bad_records():
    assert parse_posting("a", None) is None
    assert parse_posting("a", {**item(1), "id": 5}) is None
    assert parse_posting("a", {**item(1), "id": "../x"}) is None
    assert parse_posting("a", {**item(1), "name": "  "}) is None


def test_parse_survives_hostile_values():
    raw = {**item(1), "location": "x", "company": 5, "department": "x", "experienceLevel": 3}
    job = parse_posting("acme", raw)
    assert job is not None and job.location is None and job.company == "acme"


def test_description_sections():
    text = parse_description(DETAIL)
    assert "Analyse data." in text and "- SQL" in text and "visa sponsorship" in text
    assert "<" not in text
    assert parse_description(None) == "" and parse_description({"jobAd": {"sections": 5}}) == ""


# ---- fetching ---------------------------------------------------------------------------


def test_requests_only_us_postings_in_pages_of_100():
    api = FakeSR(total=250)
    result = fetch(source(api))
    assert len(result.jobs) == 250
    assert [u.params["offset"] for u in api.lists] == ["0", "100", "200"]
    assert all(u.params["country"] == "us" and u.params["limit"] == "100" for u in api.lists)


def test_reads_at_most_the_newest_500_and_says_so():
    api = FakeSR(total=1200)
    result = fetch(source(api))
    assert len(result.jobs) == 500 and len(api.lists) == 5
    assert any("newest 500 of 1200" in w for w in result.warnings)


def test_unknown_company_returns_a_clear_warning_not_a_crash():
    api = FakeSR()
    result = fetch(source(api, ("ghost",)))
    assert result.jobs == [] and "no open US postings" in result.warnings[0]


def test_full_text_only_for_candidates():
    api = FakeSR(total=5)
    pre = lambda job: job.title in {"Role 1", "Role 3"}  # noqa: E731
    result = fetch(source(api), prefilter=pre)
    assert len(api.lists) == 1 and len(api.details) == 2
    assert {j.title for j in result.jobs} == {"Role 1", "Role 3"}
    assert all("not offer visa sponsorship" in j.description for j in result.jobs)
    assert result.seen == 5


def test_no_candidates_means_no_detail_requests():
    api = FakeSR(total=4)
    result = fetch(source(api), prefilter=lambda job: False)
    assert api.details == [] and result.jobs == []


def test_details_are_cached():
    api = FakeSR(total=2)
    src = source(api)
    fetch(src, prefilter=lambda j: True)
    before = len(api.requests)
    fetch(src, prefilter=lambda j: True)
    assert len(api.requests) == before


def test_detail_cap_per_company():
    api = FakeSR(total=MAX_DETAILS_PER_BOARD + 10)
    result = fetch(source(api), prefilter=lambda j: True)
    assert len(api.details) == MAX_DETAILS_PER_BOARD
    assert len(result.jobs) == MAX_DETAILS_PER_BOARD + 10
    flagged = [j for j in result.jobs if not j.description_complete]
    assert len(flagged) == 10
    assert any("narrow the search" in w for w in result.warnings)


def test_failed_detail_keeps_the_candidate_and_marks_it_incomplete():
    api = FakeSR(total=2, detail_fails=True)
    result = fetch(source(api), prefilter=lambda j: True)
    assert len(result.jobs) == 2
    assert all(j.description_complete is False and j.description == "" for j in result.jobs)


def test_complete_job_for_the_details_tool():
    api = FakeSR(total=1)
    src = source(api)
    light = asyncio.run(src.fetch_board("acme"))[0]
    assert light.description == ""
    full = asyncio.run(src.complete_job(light))
    assert "not offer visa sponsorship" in full.description and full.description_complete


# ---- inside a real search -------------------------------------------------------------


def make_app(src):
    settings = Settings(home=Path("."), companies_file=Path("c.yaml"))
    return App(settings=settings, http=src._http, sources=[src])


def test_search_classifies_work_authorization_from_the_fetched_text():
    api = FakeSR(total=3)
    app = make_app(source(api))
    result = asyncio.run(
        run_search(app, SearchFilters(job_titles=["role 2"], work_authorization=["no_sponsorship"]))
    )
    assert [j.title for j in result.jobs] == ["Role 2"]
    assert result.jobs[0].work_authorization.classification == "no_sponsorship"
    assert len(api.details) == 1  # only the one matching title needed full text


def test_details_tool_fetches_full_text_when_the_search_skipped_it():
    api = FakeSR(total=1)
    app = make_app(source(api))
    asyncio.run(run_search(app, SearchFilters(job_titles=["role 0"], posted_within_days=30)))
    app.index._jobs.clear()  # as if the session had been restarted
    details = asyncio.run(get_details(app, "smartrecruiters:acme:744000100000000", False))
    assert "visa sponsorship" in details.description
    assert details.work_authorization.classification == "no_sponsorship"


def test_link_check():
    api = FakeSR()
    app = make_app(source(api))
    job = parse_posting("acme", item(1))
    assert asyncio.run(check_link(app, job)) == "live"
    gone = App(
        settings=app.settings,
        http=make_http(lambda r: httpx.Response(404)),
        sources=[],
    )
    assert asyncio.run(check_link(gone, job)) == "gone"
