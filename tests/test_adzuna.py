import asyncio

import httpx
import pytest
from pydantic import SecretStr

from job_search_mcp.sources.adzuna import AdzunaSource, parse_result
from job_search_mcp.sources.base import SourceQuery
from tests.helpers import make_http

RAW = {
    "id": "5551234",
    "title": "Senior <strong>Data</strong> Analyst",
    "description": "Looking for a <strong>data</strong> analyst with SQL...",
    "redirect_url": "https://www.adzuna.com/land/ad/5551234?se=abc",
    "created": "2026-10-20T08:00:00Z",
    "company": {"display_name": "Acme Corp"},
    "location": {"display_name": "Austin, Texas", "area": ["US", "Texas", "Austin"]},
    "category": {"label": "IT Jobs"},
    "salary_min": 90000.0,
    "salary_max": 110000.0,
    "salary_is_predicted": "0",
}
KEYS = (SecretStr("ID123"), SecretStr("KEY456"))


def source(handler, keys=KEYS):
    return AdzunaSource(make_http(handler), *keys)


def test_parse_result():
    job = parse_result(RAW)
    assert job.title == "Senior Data Analyst"  # <strong> tags removed
    assert job.company == "Acme Corp"
    assert job.country == "US"
    assert job.description_complete is False  # preview only
    assert (job.salary_min, job.salary_max, job.salary_basis) == (90000, 110000, "employer")


@pytest.mark.parametrize("flag", ["1", 1, True])
def test_predicted_salary_is_labelled_estimated(flag):
    assert parse_result({**RAW, "salary_is_predicted": flag}).salary_basis == "estimated"


def test_missing_salary_stays_missing():
    raw = {k: v for k, v in RAW.items() if not k.startswith("salary")}
    job = parse_result(raw)
    assert job.salary_min is None and job.salary_basis is None


def test_rejects_bad_records():
    assert parse_result(None) is None
    assert parse_result({**RAW, "redirect_url": "http://insecure.example"}) is None
    assert parse_result({**RAW, "id": None}) is None
    assert parse_result({**RAW, "title": "<b></b>"}) is None


def test_without_keys_is_skipped_with_clear_message_and_no_request():
    calls = []
    src = source(lambda r: calls.append(r) or httpx.Response(200, json={}), keys=(None, None))
    result = asyncio.run(src.fetch_jobs())
    assert result.jobs == [] and "ADZUNA_APP_ID" in result.warnings[0]
    assert calls == []
    status = asyncio.run(src.check())
    assert status.status == "not_configured"


def test_query_parameters_are_sent():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={"results": [RAW]})

    q = SourceQuery(keywords="data analyst", location="Austin", max_age_days=7, salary_min=80000)
    result = asyncio.run(source(handler).fetch_jobs(q))
    assert len(result.jobs) == 1
    p = seen[0]
    assert (p["what"], p["where"], p["max_days_old"], p["salary_min"]) == (
        "data analyst",
        "Austin",
        "7",
        "80000",
    )


def test_paginates_until_short_page():
    pages = []

    def handler(req):
        pages.append(req.url.path.rsplit("/", 1)[-1])
        n = 50 if len(pages) == 1 else 3
        return httpx.Response(200, json={"results": [RAW] * n})

    result = asyncio.run(source(handler).fetch_jobs(SourceQuery(max_results=150)))
    assert pages == ["1", "2"] and len(result.jobs) == 53


def test_results_cached_per_query():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"results": [RAW]})

    src = source(handler)
    q = SourceQuery(keywords="x")
    asyncio.run(src.fetch_jobs(q))
    asyncio.run(src.fetch_jobs(q))
    asyncio.run(src.fetch_jobs(SourceQuery(keywords="y")))
    assert len(calls) == 2


def test_failures_become_warnings_without_leaking_keys():
    src = source(lambda r: httpx.Response(401))
    result = asyncio.run(src.fetch_jobs())
    assert result.jobs == []
    text = " ".join(result.warnings)
    assert "401" in text and "ID123" not in text and "KEY456" not in text
    status = asyncio.run(src.check())
    assert status.status == "error" and "check your Adzuna keys" in status.detail
    assert "KEY456" not in status.detail


def test_check_ok():
    src = source(lambda r: httpx.Response(200, json={"results": []}))
    assert asyncio.run(src.check()).status == "ok"
