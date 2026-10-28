import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from job_search_mcp.app import App
from job_search_mcp.config import ConfigError, Settings, load_settings
from job_search_mcp.filters import SearchFilters
from job_search_mcp.http_client import ALLOWED_HOSTS, BlockedURLError, SafeHttpClient, validate_url
from job_search_mcp.search import check_link, get_details, parse_job_id, run_search, valid_job_id
from job_search_mcp.sources.base import SourceQuery
from job_search_mcp.sources.workday import (
    MAX_DETAILS_PER_SITE,
    MAX_PAGES,
    PAGE_SIZE,
    Site,
    WorkdaySource,
    hosts_for,
    parse_detail,
    parse_posted,
    parse_posting,
)
from tests.helpers import no_sleep

ENTRY = "acme.wd5.AcmeCareers"
SITE = Site.parse(ENTRY)
NOW = datetime(2026, 10, 27, tzinfo=UTC)

DETAIL = {
    "jobPostingInfo": {
        "jobDescription": "<p>Analyse data. We do not offer visa sponsorship.</p>",
        "startDate": "2026-10-20",
        "location": "US, CA, Santa Clara",
        "additionalLocations": [],
        "country": {"descriptor": "United States of America"},
        "remoteType": "Hybrid",
    }
}


def raw(i, title=None, posted="Posted Today"):
    return {
        "title": title or f"Data Analyst {i}",
        "externalPath": f"/job/US-CA-Santa-Clara/Data-Analyst-{i}_JR{i:05d}",
        "locationsText": "2 Locations",
        "postedOn": posted,
        "bulletFields": [f"JR{i:05d}"],
    }


class FakeWorkday:
    """Records every request; behaves like a Workday tenant."""

    def __init__(
        self, total=3, robots="User-agent: *\nAllow: /\n", robots_status=200, detail_status=200
    ):
        self.requests: list[tuple[str, str, bytes]] = []
        self.total, self.robots, self.robots_status = total, robots, robots_status
        self.detail_status = detail_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path == "/robots.txt":
            # Like Accenture's server: refuses to serve robots.txt to a client asking for JSON.
            if (
                "json" in request.headers.get("accept", "")
                and "text" not in request.headers["accept"]
            ):
                return httpx.Response(406)
            return httpx.Response(self.robots_status, text=self.robots)
        if request.method == "POST" and path.endswith("/jobs"):
            body = json.loads(request.content)
            offset, limit = body["offset"], body["limit"]
            assert limit <= PAGE_SIZE  # Workday rejects bigger pages
            items = [raw(i) for i in range(offset, min(self.total, offset + limit))]
            return httpx.Response(
                200, json={"total": self.total if offset == 0 else 0, "jobPostings": items}
            )
        if request.method == "GET" and "/job/" in path:
            return httpx.Response(self.detail_status, json=DETAIL)
        return httpx.Response(404)

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    def details(self):
        return [r for r in self.requests if r[0] == "GET" and "/job/" in r[1]]

    def bodies(self):
        return [json.loads(r[2]) for r in self.posts()]


def make_http(handler):
    return SafeHttpClient(
        max_response_bytes=10**7,
        requests_per_second=1000,
        allowed_hosts=ALLOWED_HOSTS | hosts_for([ENTRY]),
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )


def source(fake, entries=(ENTRY,)):
    return WorkdaySource(make_http(fake), list(entries))


def fetch(src, keywords=None, **kw):
    return asyncio.run(src.fetch_jobs(SourceQuery(keywords=keywords, **kw)))


# ---- entries, hosts, dates ---------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    ["nvidia.wd5.NVIDIAExternalCareerSite", "a.wd1.X", "my-co.wd103.Site_Name-2"],
)
def test_valid_entries(entry):
    assert Site.parse(entry) is not None


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "nvidia",
        "nvidia.wd5",
        "NVIDIA.wd5.Site",  # tenant must be lowercase
        "nvidia.wd5.Site/../x",
        "nvidia.wd5.Site Name",
        "evil.com.wd5.Site",
        "nvidia.wd5.Site.extra",
        "nvidia.xx5.Site",
        "../nvidia.wd5.Site",
        "a" * 50 + ".wd5.Site",
        "nvidia.wd5." + "S" * 90,
        "nvidia.wd5.Site?x=1",
    ],
)
def test_bad_entries_are_rejected(entry):
    assert Site.parse(entry) is None


def test_hosts_are_exact_never_a_wildcard():
    hosts = hosts_for([ENTRY, "other.wd1.Site", "garbage"])
    assert hosts == {"acme.wd5.myworkdayjobs.com", "other.wd1.myworkdayjobs.com"}
    allowed = ALLOWED_HOSTS | hosts
    validate_url("https://acme.wd5.myworkdayjobs.com/x", allowed)
    for bad in (
        "https://evil.wd5.myworkdayjobs.com/x",  # a tenant the user did not list
        "https://acme.wd5.myworkdayjobs.com.evil.com/x",
        "https://myworkdayjobs.com/x",
        "http://acme.wd5.myworkdayjobs.com/x",
    ):
        with pytest.raises(BlockedURLError):
            validate_url(bad, allowed)


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("Posted Today", 0),
        ("Posted Yesterday", 1),
        ("Posted 5 Days Ago", 5),
        ("Posted 30+ Days Ago", 31),
        ("posted 1 day ago", 1),
    ],
)
def test_relative_dates(text, days):
    assert parse_posted(text, NOW) == NOW - timedelta(days=days)


@pytest.mark.parametrize("text", [None, 5, "", "Posted Someday", "recently"])
def test_unparseable_dates_are_none(text):
    assert parse_posted(text, NOW) is None


# ---- parsing -----------------------------------------------------------------------------


def test_parse_posting():
    job = parse_posting(SITE, raw(7, posted="Posted 3 Days Ago"), NOW)
    assert job.id == "workday:acme.wd5.AcmeCareers:JR00007"
    assert job.source == "workday" and job.company == "Acme"
    assert (
        job.url
        == "https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/US-CA-Santa-Clara/Data-Analyst-7_JR00007"
    )
    assert job.posted_at == NOW - timedelta(days=3)
    assert job.description == "" and job.description_complete is False
    assert job.salary_min is None


def test_requisition_id_falls_back_to_the_path():
    item = {**raw(9), "bulletFields": []}
    assert parse_posting(SITE, item, NOW).id.endswith(":JR00009")


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "x",
        {**raw(1), "externalPath": "/job/../../etc/passwd"},
        {**raw(1), "externalPath": "https://evil.example.com/job/x"},
        {**raw(1), "externalPath": "/other/path"},
        {**raw(1), "externalPath": "/job/x y"},
        {**raw(1), "externalPath": 5},
        {**raw(1), "title": "  "},
        {**raw(1), "bulletFields": ["bad id!"], "externalPath": "/job/US/NoUnderscoreHere"},
    ],
)
def test_bad_postings_are_rejected(bad):
    assert parse_posting(SITE, bad, NOW) is None


def test_hostile_values_do_not_crash():
    item = {**raw(1), "locationsText": ["x"], "postedOn": {"a": 1}, "bulletFields": "oops"}
    assert parse_posting(SITE, item, NOW) is not None


def test_parse_detail():
    detail = parse_detail(DETAIL)
    assert "do not offer visa sponsorship" in detail.description
    assert detail.posted_at == datetime(2026, 10, 20, tzinfo=UTC)
    assert detail.location == "US, CA, Santa Clara" and detail.country == "US"
    assert detail.workplace == "hybrid"


def test_several_locations_do_not_trust_the_primary_country():
    data = {
        "jobPostingInfo": {**DETAIL["jobPostingInfo"], "additionalLocations": ["Canada, Toronto"]}
    }
    detail = parse_detail(data)
    assert detail.country is None and "Canada, Toronto" in detail.location


@pytest.mark.parametrize("bad", [None, [], {"jobPostingInfo": 5}, {}])
def test_bad_details_are_none(bad):
    assert parse_detail(bad) is None


# ---- robots.txt --------------------------------------------------------------------------


def test_a_site_that_disallows_automated_access_is_skipped_entirely():
    fake = FakeWorkday(robots="User-agent: *\nDisallow: /wday/\n")
    result = fetch(source(fake), "data analyst")
    assert result.jobs == [] and "robots.txt does not allow" in result.warnings[0]
    assert fake.posts() == [] and fake.details() == []  # not a single search or detail request


def test_blocking_only_our_own_agent_also_stops_us():
    fake = FakeWorkday(robots="User-agent: job-search-mcp\nDisallow: /\n")
    assert fetch(source(fake), "x").jobs == [] and fake.posts() == []


def test_no_robots_file_means_no_restrictions():
    fake = FakeWorkday(robots_status=404)
    assert len(fetch(source(fake), "data").jobs) == 3


def test_unreadable_robots_means_skip_not_assume():
    fake = FakeWorkday(robots_status=500)
    result = fetch(source(fake), "data")
    assert result.jobs == [] and "could not be read" in result.warnings[0] and fake.posts() == []


def test_unrelated_disallow_rules_do_not_block():
    fake = FakeWorkday(robots="User-agent: *\nDisallow: /talentcommunity/\nAllow: /AcmeCareers/\n")
    assert len(fetch(source(fake), "data").jobs) == 3


def test_robots_is_fetched_once_not_every_search():
    fake = FakeWorkday()
    src = source(fake)
    fetch(src, "one")
    fetch(src, "two")
    assert len([r for r in fake.requests if r[1] == "/robots.txt"]) == 1


# ---- listing, searching, paging ----------------------------------------------------------------


def test_the_users_job_title_becomes_the_server_side_search():
    fake = FakeWorkday()
    fetch(source(fake), "data analyst")
    body = fake.bodies()[0]
    assert (
        body["searchText"] == "data analyst" and body["offset"] == 0 and body["limit"] == PAGE_SIZE
    )


def test_without_a_title_it_reads_the_newest_postings():
    fake = FakeWorkday()
    fetch(source(fake), None)
    assert fake.bodies()[0]["searchText"] == ""


def test_paging_uses_the_total_from_the_first_page_only():
    fake = FakeWorkday(total=45)  # later pages report total 0, like the real service
    result = fetch(source(fake), "x", prefilter=lambda j: False)
    assert [b["offset"] for b in fake.bodies()] == [0, 20, 40]
    assert result.seen == 45


def test_at_most_five_pages_and_it_says_so():
    fake = FakeWorkday(total=900)
    result = fetch(source(fake), "x", prefilter=lambda j: False)
    assert len(fake.posts()) == MAX_PAGES and result.seen == MAX_PAGES * PAGE_SIZE
    assert any("only the first 100 matches" in w for w in result.warnings)


def test_full_text_only_for_candidates():
    fake = FakeWorkday(total=5)
    result = fetch(source(fake), "x", prefilter=lambda j: j.title.endswith(("1", "3")))
    assert len(fake.details()) == 2
    assert {j.title for j in result.jobs} == {"Data Analyst 1", "Data Analyst 3"}
    job = result.jobs[0]
    assert "visa sponsorship" in job.description and job.description_complete
    # exact date, country and location arrive with the full text
    assert job.posted_at == datetime(2026, 10, 20, tzinfo=UTC)
    assert job.country == "US" and job.location == "US, CA, Santa Clara"
    assert job.workplace_type == "hybrid"


def test_detail_cap_per_site():
    fake = FakeWorkday(total=MAX_DETAILS_PER_SITE + 8)
    result = fetch(source(fake), "x")
    assert len(fake.details()) == MAX_DETAILS_PER_SITE
    assert len(result.jobs) == MAX_DETAILS_PER_SITE + 8
    assert sum(not j.description_complete for j in result.jobs) == 8
    assert any("narrow the search" in w for w in result.warnings)


def test_a_failed_detail_keeps_the_candidate_and_marks_it():
    fake = FakeWorkday(total=2, detail_status=500)
    result = fetch(source(fake), "x")
    assert len(result.jobs) == 2 and all(not j.description_complete for j in result.jobs)


def test_everything_is_cached_for_a_while():
    fake = FakeWorkday(total=2)
    src = source(fake)
    fetch(src, "data")
    before = len(fake.requests)
    again = fetch(src, "data")
    assert len(fake.requests) == before and len(again.jobs) == 2


def test_different_titles_share_the_cached_details():
    fake = FakeWorkday(total=2)
    src = source(fake)
    fetch(src, "data")
    details_before = len(fake.details())
    fetch(src, "analyst")  # a new list request, but the same postings
    assert len(fake.details()) == details_before


def test_unknown_site_is_a_warning():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404)

    result = fetch(WorkdaySource(make_http(handler), [ENTRY]), "x")
    assert result.jobs == [] and "not found" in result.warnings[0]


def test_time_budget_reports_sites_still_answering():
    class Slow(httpx.AsyncBaseTransport):
        def __init__(self, fake):
            self.fake = fake

        async def handle_async_request(self, request):
            if "slow" in request.url.host:
                await asyncio.sleep(0.4)
            return self.fake(request)

    entries = ["fast.wd5.Site", "slow.wd5.Site"]
    http = SafeHttpClient(
        max_response_bytes=10**7,
        requests_per_second=1000,
        allowed_hosts=ALLOWED_HOSTS | hosts_for(entries),
        transport=Slow(FakeWorkday(total=1)),
        sleep=no_sleep,
    )
    result = asyncio.run(
        WorkdaySource(http, entries).fetch_jobs(SourceQuery(keywords="x", time_budget=0.1))
    )
    assert result.pending == 1 and len(result.jobs) == 1
    assert any("1 of 2 sites answered" in w for w in result.warnings)


# ---- link check, details tool, ids --------------------------------------------------------------


def test_check_live():
    job = parse_posting(SITE, raw(1), NOW)
    assert asyncio.run(source(FakeWorkday()).check_live(job)) == "live"
    assert asyncio.run(source(FakeWorkday(detail_status=404)).check_live(job)) == "gone"
    assert asyncio.run(source(FakeWorkday(detail_status=500)).check_live(job)) == "unverified"
    blocked = FakeWorkday(robots="User-agent: *\nDisallow: /\n")
    assert asyncio.run(source(blocked).check_live(job)) == "unverified"
    assert blocked.details() == []


def test_check_live_ignores_jobs_from_sites_not_configured():
    job = parse_posting(Site.parse("other.wd1.Site"), raw(1), NOW)
    fake = FakeWorkday()
    assert asyncio.run(source(fake).check_live(job)) == "unverified"
    assert fake.requests == []


def make_app(src):
    settings = Settings(home=Path("."), companies_file=Path("c.yaml"))
    return App(settings=settings, http=src._http, sources=[src])


def test_link_check_goes_through_the_workday_client():
    src = source(FakeWorkday())
    job = parse_posting(SITE, raw(1), NOW)
    assert asyncio.run(check_link(make_app(src), job)) == "live"


def test_job_ids():
    assert valid_job_id("workday:acme.wd5.AcmeCareers:JR00007")
    assert parse_job_id("workday:acme.wd5.AcmeCareers:JR00007") == (
        "workday",
        "acme.wd5.AcmeCareers",
        "JR00007",
    )
    for bad in (
        "workday:acme.wd5.Site/../x:JR1",
        "workday:acme:JR1",
        "workday:acme.wd5.Site:JR 1",
        "workday:a.b.c:JR1",
    ):
        assert parse_job_id(bad) is None


def test_details_tool_completes_a_workday_posting():
    src = source(FakeWorkday(total=1))
    app = make_app(src)
    job = parse_posting(SITE, raw(0), NOW)
    app.index.put_many([job])
    details = asyncio.run(get_details(app, job.id, False))
    assert "visa sponsorship" in details.description
    assert details.work_authorization.classification == "no_sponsorship"


# ---- inside a real search ----


def test_search_sends_each_title_to_workday_and_classifies_the_result():
    fake = FakeWorkday(total=3)
    app = make_app(source(fake))
    result = asyncio.run(
        run_search(
            app,
            SearchFilters(
                job_titles=["data analyst 2"],
                work_authorization=["no_sponsorship"],
                posted_within_days=30,
            ),
        )
    )
    assert [j.title for j in result.jobs] == ["Data Analyst 2"]
    assert result.jobs[0].source == "workday"
    assert any(b["searchText"] == "data analyst 2" for b in fake.bodies())


# ---- configuration ----


def test_config_accepts_workday_entries_and_keeps_their_case(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_SEARCH_HOME", str(tmp_path))
    (tmp_path / "companies.yaml").write_text(
        "workday: [nvidia.wd5.NVIDIAExternalCareerSite,\n"
        "  nvidia.wd5.NVIDIAExternalCareerSite, a.wd1.Ext]\n"
    )
    assert load_settings().companies.workday == ["nvidia.wd5.NVIDIAExternalCareerSite", "a.wd1.Ext"]


@pytest.mark.parametrize("bad", ["nvidia", "NVIDIA.wd5.X", "x.wd5.y/../z", "evil.com", "a.wd5.b c"])
def test_config_rejects_bad_workday_entries(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("JOB_SEARCH_HOME", str(tmp_path))
    (tmp_path / "companies.yaml").write_text(f"workday: ['{bad}']\n")
    with pytest.raises(ConfigError):
        load_settings()


def test_the_workday_client_only_knows_the_listed_tenants(tmp_path, monkeypatch):
    from job_search_mcp.app import build_app  # noqa: PLC0415

    monkeypatch.setenv("JOB_SEARCH_HOME", str(tmp_path))
    (tmp_path / "companies.yaml").write_text("workday: [acme.wd5.AcmeCareers]\n")
    app = build_app()
    workday = next(s for s in app.sources if s.name == "workday")
    assert workday.company_count == 1 and workday._http is not app.http
    assert "acme.wd5.myworkdayjobs.com" in workday._http._allowed
    assert "acme.wd5.myworkdayjobs.com" not in app.http._allowed  # the main client stays strict
    assert not any(h.endswith("myworkdayjobs.com") for h in app.http._allowed)
    asyncio.run(app.http.aclose())
    asyncio.run(workday._http.aclose())


def test_robots_is_requested_as_text_not_json():
    # Regression (real): Accenture's server answered 406 to "Accept: application/json".
    fake = FakeWorkday()
    assert len(fetch(source(fake), "data").jobs) == 3


def test_many_truncation_warnings_become_one_line():
    entries = [f"co{i}.wd5.Site" for i in range(6)]
    fake = FakeWorkday(total=900)
    http = SafeHttpClient(
        max_response_bytes=10**7,
        requests_per_second=1000,
        allowed_hosts=ALLOWED_HOSTS | hosts_for(entries),
        transport=httpx.MockTransport(fake),
        sleep=no_sleep,
    )
    result = asyncio.run(
        WorkdaySource(http, entries).fetch_jobs(
            SourceQuery(keywords="x", prefilter=lambda j: False)
        )
    )
    assert len(result.warnings) == 1
    assert "for 6 companies only the first 100 matches" in result.warnings[0]
