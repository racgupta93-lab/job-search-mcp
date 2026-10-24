import asyncio

import httpx

from job_search_mcp.http_client import SafeHttpClient
from job_search_mcp.sources.greenhouse import GreenhouseSource, parse_job

RAW = {
    "id": 123,
    "title": "Senior Data Analyst",
    "absolute_url": "https://careers.example.com/jobs/123",
    "company_name": "Acme",
    "location": {"name": "Remote - US"},
    "first_published": "2026-10-01T09:00:00-04:00",
    "updated_at": "2026-10-10T09:00:00-04:00",
    "content": "&lt;p&gt;Great job. We do not offer visa sponsorship.&lt;/p&gt;",
    "departments": [{"name": "Data"}],
}


async def _no_sleep(_):
    return None


def make_source(handler, boards):
    http = SafeHttpClient(
        max_response_bytes=1_000_000,
        requests_per_second=1000,
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )
    return GreenhouseSource(http, boards)


def test_parse_job_fields():
    job = parse_job("acme", RAW)
    assert job.id == "greenhouse:acme:123"
    assert job.title == "Senior Data Analyst"
    assert job.workplace_type == "remote"
    assert job.posted_at.isoformat() == "2026-10-01T13:00:00+00:00"  # first_published, UTC
    assert job.description == "Great job. We do not offer visa sponsorship."
    assert job.departments == ["Data"]
    assert job.salary_min is None  # never invented


def test_parse_job_rejects_bad_records():
    assert parse_job("a", "nope") is None
    assert parse_job("a", {**RAW, "id": "x"}) is None
    assert parse_job("a", {**RAW, "title": ""}) is None
    assert parse_job("a", {**RAW, "absolute_url": "http://insecure.example.com"}) is None
    assert parse_job("a", {**RAW, "absolute_url": None}) is None


def test_parse_job_survives_hostile_values():
    job = parse_job("a", {**RAW, "title": "T" * 5000, "location": "not a dict", "departments": "x"})
    assert len(job.title) <= 300
    assert job.location is None
    assert job.departments == []


def test_fetch_jobs_skips_bad_records_and_reports_missing_boards():
    def handler(req):
        if "/boards/acme/jobs" in req.url.path:
            return httpx.Response(200, json={"jobs": [RAW, {"id": "bad"}, "junk"]})
        return httpx.Response(404)

    source = make_source(handler, ["acme", "ghost"])
    result = asyncio.run(source.fetch_jobs())
    assert [j.id for j in result.jobs] == ["greenhouse:acme:123"]
    assert any("skipped 2" in w for w in result.warnings)
    assert any("'ghost' not found" in w for w in result.warnings)


def test_unexpected_response_shape_is_a_warning_not_a_crash():
    source = make_source(lambda r: httpx.Response(200, json=["surprise"]), ["acme"])
    result = asyncio.run(source.fetch_jobs())
    assert result.jobs == []
    assert "unexpected response" in result.warnings[0]


def test_results_are_cached():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"jobs": [RAW]})

    source = make_source(handler, ["acme"])
    asyncio.run(source.fetch_jobs())
    asyncio.run(source.fetch_jobs())
    assert len(calls) == 1


def test_check_reports_ok_error_and_unconfigured():
    ok = make_source(lambda r: httpx.Response(200, json={"name": "Acme"}), ["acme"])
    assert asyncio.run(ok.check()).status == "ok"
    bad = make_source(lambda r: httpx.Response(404), ["acme"])
    assert asyncio.run(bad.check()).status == "error"
    none = make_source(lambda r: httpx.Response(200), [])
    assert asyncio.run(none.check()).status == "not_configured"
