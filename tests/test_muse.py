import asyncio

import httpx
import pytest
from pydantic import SecretStr

from job_search_mcp.sources.base import SourceQuery
from job_search_mcp.sources.muse import MuseSource, categories_for, parse_job
from tests.helpers import make_http

RAW = {
    "id": 18220754,
    "name": "Director, Data Science",
    "contents": "<p><b>Pay</b> The range is $150,000 - $200,000.</p>",
    "publication_date": "2026-10-02T21:26:11Z",
    "locations": [{"name": "Boston, MA"}, {"name": "Flexible / Remote"}],
    "categories": [{"name": "Data and Analytics"}],
    "levels": [{"name": "Senior Level", "short_name": "senior"}],
    "refs": {"landing_page": "https://www.themuse.com/jobs/acme/director-data-science"},
    "company": {"id": 1, "name": "Acme Insurance"},
}


def test_parse_job():
    job = parse_job(RAW)
    assert job.id == "muse:18220754"
    assert job.company == "Acme Insurance"
    assert job.location == "Boston, MA; Flexible / Remote"
    assert job.workplace_type == "remote"
    assert job.level_hint == "senior"
    assert job.salary_min is None  # no structured salary: never invented
    assert "$150,000" in job.description


def test_parse_rejects_bad_records():
    assert parse_job(None) is None
    assert parse_job({**RAW, "id": "x"}) is None
    assert parse_job({**RAW, "refs": {"landing_page": "http://x.example"}}) is None
    assert parse_job({**RAW, "refs": "nope"}) is None
    assert parse_job({**RAW, "name": ""}) is None


def test_parse_survives_hostile_values():
    job = parse_job({**RAW, "locations": "x", "levels": "x", "categories": [1], "company": 5})
    assert job is not None and job.location is None and job.level_hint is None


@pytest.mark.parametrize(
    ("keywords", "expected"),
    [
        ("data analyst", ["Data and Analytics"]),
        ("analytics engineer", ["Data and Analytics", "Software Engineering"]),
        ("senior software engineer", ["Software Engineering"]),
        ("product manager", ["Product Management"]),
        ("underwater basket weaver", []),
        (None, []),
    ],
)
def test_categories_for(keywords, expected):
    assert categories_for(keywords) == expected


def _page(n, page_count):
    return {
        "page": 0,
        "page_count": page_count,
        "results": [{**RAW, "id": 1000 + n * 100 + i} for i in range(3)],
    }


def test_pages_are_zero_based_and_filters_sent():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=_page(len(seen), 2))

    src = MuseSource(make_http(handler), SecretStr("KEY"))
    result = asyncio.run(
        src.fetch_jobs(SourceQuery(keywords="data analyst", level="junior", max_results=400))
    )
    assert [p["page"] for p in seen] == ["0", "1"]  # stops at page_count
    assert seen[0]["category"] == "Data and Analytics"
    assert seen[0]["level"] == "Entry Level"
    assert seen[0]["api_key"] == "KEY"  # pragma: allowlist secret
    assert len(result.jobs) == 6
    assert result.warnings == []


def test_warns_when_more_pages_exist_than_scanned():
    src = MuseSource(make_http(lambda r: httpx.Response(200, json=_page(1, 100))), None)
    result = asyncio.run(src.fetch_jobs(SourceQuery(keywords="data", max_results=40)))
    assert any("scanned 2 of 100 pages" in w for w in result.warnings)


def test_warns_when_no_category_matches():
    src = MuseSource(make_http(lambda r: httpx.Response(200, json=_page(1, 1))), None)
    result = asyncio.run(src.fetch_jobs(SourceQuery(keywords="basket weaver")))
    assert any("no category matched" in w for w in result.warnings)


def test_no_key_means_no_api_key_param():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=_page(1, 1))

    asyncio.run(MuseSource(make_http(handler), None).fetch_jobs(SourceQuery(keywords="data")))
    assert "api_key" not in seen[0]


def test_failures_are_warnings_and_do_not_leak_key_or_cache():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(403)

    src = MuseSource(make_http(handler), SecretStr("TOPSECRET"))  # pragma: allowlist secret
    result = asyncio.run(src.fetch_jobs(SourceQuery(keywords="data")))
    assert result.jobs == [] and "403" in " ".join(result.warnings)
    assert "TOPSECRET" not in " ".join(result.warnings)
    asyncio.run(src.fetch_jobs(SourceQuery(keywords="data")))
    assert len(calls) == 2  # failures are not cached


def test_success_is_cached():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=_page(1, 1))

    src = MuseSource(make_http(handler), None)
    asyncio.run(src.fetch_jobs(SourceQuery(keywords="data")))
    asyncio.run(src.fetch_jobs(SourceQuery(keywords="data")))
    assert len(calls) == 1


def test_check():
    ok = MuseSource(make_http(lambda r: httpx.Response(200, json=_page(1, 1))), None)
    status = asyncio.run(ok.check())
    assert status.status == "ok" and "no API key" in status.detail
    bad = MuseSource(make_http(lambda r: httpx.Response(403)), SecretStr("K"))
    status = asyncio.run(bad.check())
    assert status.status == "error" and "MUSE_API_KEY" in status.detail
