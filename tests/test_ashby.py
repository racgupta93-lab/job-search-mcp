import asyncio

import httpx

from job_search_mcp.sources.ashby import AshbySource, parse_job
from tests.helpers import make_http


def comp(tiers):
    return {"compensationTiers": [{"components": c} for c in tiers]}


SALARY = {
    "compensationType": "Salary",
    "interval": "1 YEAR",
    "currencyCode": "USD",
    "minValue": 211400,
    "maxValue": 290600,
}
EQUITY = {"compensationType": "EquityPercentage", "interval": "NONE", "minValue": None}

RAW = {
    "id": "34413f8d-26bf-4bbc-8ade-eb309a0e2245",
    "title": " Staff Data Scientist ",
    "department": "Data",
    "team": "Growth",
    "location": "New York, NY (HQ)",
    "isListed": True,
    "isRemote": True,
    "workplaceType": "Hybrid",
    "publishedAt": "2026-04-07T17:12:35.753+00:00",
    "address": {"postalAddress": {"addressCountry": "United States"}},
    "jobUrl": "https://jobs.ashbyhq.com/acme/34413f8d",
    "descriptionHtml": "<h1>About</h1><p>Must be authorized to work without sponsorship.</p>",
    "compensation": comp([[EQUITY, SALARY]]),
}


def test_parse_full_job():
    job = parse_job("acme", RAW)
    assert job.title == "Staff Data Scientist"
    assert job.country == "US"
    # isRemote is true for hybrid roles too, so workplaceType decides
    assert job.workplace_type == "hybrid"
    assert (job.salary_min, job.salary_max, job.salary_period) == (211400, 290600, "year")
    assert job.salary_basis == "employer"
    assert "authorized to work without sponsorship" in job.description


def test_multiple_tiers_report_the_overall_span():
    other = {**SALARY, "minValue": 190000, "maxValue": 260000}
    job = parse_job("a", {**RAW, "compensation": comp([[SALARY], [other]])})
    assert (job.salary_min, job.salary_max) == (190000, 290600)


def test_mixed_periods_use_first_component_only():
    hourly = {**SALARY, "interval": "1 HOUR", "minValue": 50, "maxValue": 60}
    job = parse_job("a", {**RAW, "compensation": comp([[SALARY], [hourly]])})
    assert job.salary_period == "year" and job.salary_min == 211400


def test_equity_only_means_no_salary():
    job = parse_job("a", {**RAW, "compensation": comp([[EQUITY]])})
    assert job.salary_min is None and job.salary_basis is None


def test_no_compensation_means_no_salary():
    assert parse_job("a", {**RAW, "compensation": None}).salary_min is None
    assert parse_job("a", {**RAW, "compensation": "junk"}).salary_min is None


def test_unlisted_and_malformed_are_skipped():
    assert parse_job("a", {**RAW, "isListed": False}) is None
    assert parse_job("a", {**RAW, "jobUrl": "http://x.example"}) is None
    assert parse_job("a", {**RAW, "id": 1}) is None
    assert parse_job("a", [1, 2]) is None


def test_hostile_nested_values_do_not_crash():
    job = parse_job(
        "a",
        {**RAW, "address": "x", "compensation": {"compensationTiers": ["x", {"components": "y"}]}},
    )
    assert job is not None and job.country is None


def test_fetch_requests_compensation():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"jobs": [RAW], "apiVersion": "1"})

    result = asyncio.run(AshbySource(make_http(handler), ["acme"]).fetch_jobs())
    assert len(result.jobs) == 1
    assert "includeCompensation=true" in seen[0]
