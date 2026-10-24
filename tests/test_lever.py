import asyncio

import httpx

from job_search_mcp.sources.lever import LeverSource, parse_posting
from tests.helpers import make_http

RAW = {
    "id": "2a55d6c9-c564-443a-be3e-a3851db5a93b",
    "text": "Senior Analytics Engineer",
    "hostedUrl": "https://jobs.lever.co/acme/2a55d6c9",
    "applyUrl": "https://jobs.lever.co/acme/2a55d6c9/apply",
    "createdAt": 1792767640768,
    "country": "US",
    "workplaceType": "hybrid",
    "categories": {"location": "New York", "department": "Data", "team": "Analytics"},
    "description": "<div><p>Build models.</p></div>",
    "lists": [{"text": "Requirements", "content": "<li>SQL</li><li>dbt</li>"}],
    "additional": "<p>We are unable to sponsor visas.</p>",
    "salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 150000, "max": 180000},
}


def test_parse_full_posting():
    job = parse_posting("acme", RAW)
    assert job.id.startswith("lever:acme:")
    assert job.title == "Senior Analytics Engineer"
    assert job.country == "US"
    assert job.workplace_type == "hybrid"
    assert job.departments == ["Data", "Analytics"]
    assert job.posted_at.year == 2026
    assert (job.salary_min, job.salary_max, job.salary_period) == (150000, 180000, "year")
    assert job.salary_basis == "employer"
    # every section of the posting is included, especially the closing one
    for phrase in ("Build models.", "Requirements", "- SQL", "unable to sponsor visas"):
        assert phrase in job.description


def test_missing_salary_stays_missing():
    job = parse_posting("acme", {k: v for k, v in RAW.items() if k != "salaryRange"})
    assert job.salary_min is None and job.salary_basis is None


def test_weekly_salary_is_not_used():
    raw = {**RAW, "salaryRange": {**RAW["salaryRange"], "interval": "per-week-salary"}}
    assert parse_posting("acme", raw).salary_min is None


def test_unspecified_workplace_is_none():
    assert parse_posting("acme", {**RAW, "workplaceType": "unspecified"}).workplace_type is None


def test_rejects_bad_records_and_hostile_values():
    assert parse_posting("a", None) is None
    assert parse_posting("a", {**RAW, "id": 5}) is None
    assert parse_posting("a", {**RAW, "hostedUrl": "http://x.example"}) is None
    assert parse_posting("a", {**RAW, "text": "   "}) is None
    hostile = {**RAW, "categories": "nope", "lists": "nope", "salaryRange": "nope", "additional": 5}
    job = parse_posting("a", hostile)
    assert job is not None and job.location is None


def test_fetch_and_regions():
    seen = []

    def handler(req):
        seen.append((req.url.host, req.url.path))
        return httpx.Response(200, json=[RAW])

    us = LeverSource(make_http(handler), ["acme"])
    eu = LeverSource(make_http(handler), ["acme"], eu=True)
    assert (us.name, eu.name) == ("lever", "lever_eu")
    assert len(asyncio.run(us.fetch_jobs()).jobs) == 1
    asyncio.run(eu.fetch_jobs())
    assert seen == [("api.lever.co", "/v0/postings/acme"), ("api.eu.lever.co", "/v0/postings/acme")]


def test_non_list_response_is_warning():
    src = LeverSource(make_http(lambda r: httpx.Response(200, json={"oops": 1})), ["acme"])
    result = asyncio.run(src.fetch_jobs())
    assert result.jobs == [] and "unexpected response" in result.warnings[0]
