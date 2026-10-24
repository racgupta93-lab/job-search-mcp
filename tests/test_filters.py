from datetime import UTC, datetime

import pytest

from job_search_mcp.filters import (
    annual_usd_range,
    level_matches,
    location_matches,
    salary_matches,
    title_matches,
    us_matches,
    workplace_matches,
)
from job_search_mcp.models import Job


def job(**kw):
    base = {
        "id": "greenhouse:acme:1",
        "source": "greenhouse",
        "company": "Acme",
        "title": "Senior Data Analyst",
        "url": "https://x.example/1",
        "posted_at": datetime(2026, 10, 20, tzinfo=UTC),
    }
    return Job(**{**base, **kw})


def paid(low, high, period="year", currency="USD", basis="employer"):
    return {
        "salary_min": low,
        "salary_max": high,
        "salary_period": period,
        "salary_currency": currency,
        "salary_basis": basis,
    }


# ---- title ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "phrases", "expected"),
    [
        ("Senior Data Analyst, Pricing", ["data analyst"], True),
        ("Data Analysts", ["data analyst"], True),  # plural
        ("Analytics Engineer II", ["analytics engineer"], True),
        ("Senior Data Engineer", ["data analyst"], False),
        ("Data Scientist", ["data analyst", "analytics engineer"], False),
        ("Analytics Engineer", ["data analyst", "analytics engineer"], True),  # any phrase
        ("Business Intelligence Analyst", ["BI analyst"], False),  # no synonyms (documented)
        ("Data & Business Analyst", ["data analyst"], True),  # words, not exact phrase
        ("Analyst, Data", ["data analyst"], True),  # order does not matter
        ("Anything", [], True),  # no filter
        ("Anything", ["   "], False),  # empty phrase never matches
        ("C++ Developer", ["c++ developer"], True),
    ],
)
def test_title_matches(title, phrases, expected):
    assert title_matches(job(title=title), phrases) is expected


def test_title_matching_ignores_the_department_but_can_use_the_description():
    j = job(title="Analyst", departments=["Data"], description="We use SQL and dbt daily.")
    assert not title_matches(j, ["data analyst"])  # department words must not count
    assert not title_matches(j, ["analyst sql"])
    assert title_matches(j, ["analyst sql"], search_description=True)


def test_broad_source_category_cannot_make_unrelated_titles_match():
    # Regression: The Muse tags jobs "Data and Analytics"; "Senior ML Engineer" is not
    # an "analytics engineer" and "Business Analyst" is not a "data analyst".
    engineer = job(title="Senior ML Accelerator Engineer", departments=["Data and Analytics"])
    analyst = job(title="Business Analyst - Strategic Finance", departments=["Data and Analytics"])
    assert not title_matches(engineer, ["analytics engineer"])
    assert not title_matches(analyst, ["data analyst"])


# ---- location ------------------------------------------------------------


@pytest.mark.parametrize(
    ("job_location", "query", "expected"),
    [
        ("Austin, TX", "Austin", True),
        ("Austin, TX", "Austin, TX", True),
        ("Austin, TX", "austin, tx", True),
        ("Austin, TX", "TX", True),
        ("Austin, TX", "tx", True),
        ("Austin, TX", "Dallas", False),
        ("Austin, TX", "Austin, CA", False),
        ("Portland, OR", "or", True),  # state code
        ("Portland, ME", "OR", False),
        ("Boston, MA; Flexible / Remote", "Boston", True),
        ("New York, NY", "New York", True),
        ("Austinville, VA", "Austin", False),  # whole word only
        ("California", "California", True),
        (None, "Austin", False),
    ],
)
def test_location_matches(job_location, query, expected):
    assert location_matches(job(location=job_location), query, include_remote=False) is expected


def test_remote_jobs_pass_a_location_filter_unless_excluded():
    remote = job(location="Remote - US", workplace_type="remote")
    assert location_matches(remote, "Austin", include_remote=True)
    assert not location_matches(remote, "Austin", include_remote=False)


def test_no_location_filter_matches_everything():
    assert location_matches(job(location=None), None)
    assert location_matches(job(location="x"), "  ")


# ---- salary --------------------------------------------------------------


def test_annual_conversion_and_currency_rules():
    assert annual_usd_range(job(**paid(100000, 120000))) == (100000, 120000)
    assert annual_usd_range(job(**paid(8000, 10000, "month"))) == (96000, 120000)
    assert annual_usd_range(job(**paid(50, 60, "hour"))) == (104000, 124800)
    assert annual_usd_range(job(**paid(100000, 120000, currency="CAD"))) is None  # no guessing
    assert annual_usd_range(job(**paid(100000, 120000, currency=None))) is None
    assert annual_usd_range(job()) is None


def test_one_sided_salary_is_treated_as_a_point():
    assert annual_usd_range(job(**paid(90000, None))) == (90000, 90000)
    assert annual_usd_range(job(**paid(None, 90000))) == (90000, 90000)


@pytest.mark.parametrize(
    ("posting", "smin", "smax", "expected"),
    [
        ((100000, 130000), 120000, None, True),  # ranges overlap
        ((100000, 130000), 140000, None, False),
        ((100000, 130000), None, 90000, False),
        ((100000, 130000), None, 100000, True),  # boundary counts
        ((100000, 130000), 90000, 110000, True),
        ((100000, 130000), 131000, 200000, False),
    ],
)
def test_salary_matches(posting, smin, smax, expected):
    assert salary_matches(job(**paid(*posting)), smin, smax) is expected


def test_hourly_compared_on_a_yearly_basis():
    assert salary_matches(job(**paid(50, 60, "hour")), 100000, None)  # $104k+/yr
    assert not salary_matches(job(**paid(30, 40, "hour")), 100000, None)  # $83k max


def test_no_salary_is_included_or_excluded_as_asked_and_never_invented():
    nosal = job()
    assert salary_matches(nosal, 100000, None, include_no_salary=True)
    assert not salary_matches(nosal, 100000, None, include_no_salary=False)
    assert salary_matches(nosal, None, None, include_no_salary=False)  # no salary filter set


def test_estimated_salaries_only_count_when_allowed():
    est = job(**paid(150000, 170000, basis="estimated"))
    assert not salary_matches(est, 140000, None, include_no_salary=False)
    assert salary_matches(est, 140000, None, include_no_salary=False, include_estimated=True)
    assert salary_matches(est, 140000, None, include_no_salary=True)  # treated as unknown


def test_foreign_currency_counts_as_no_salary():
    cad = job(**paid(150000, 170000, currency="CAD"))
    assert not salary_matches(cad, 100000, None, include_no_salary=False)


# ---- level / workplace / us -----------------------------------------------


def test_level_filter():
    senior = job(level="senior")
    assert level_matches(senior, ["senior", "lead"])
    assert not level_matches(senior, ["junior"])
    assert level_matches(senior, [])


def test_unspecified_level_default_depends_on_whether_mid_is_requested():
    bare = job(level=None)
    assert not level_matches(bare, ["senior"])  # strict by default
    assert level_matches(bare, ["mid"])  # plain titles are usually mid
    assert level_matches(bare, ["senior"], include_unspecified=True)
    assert not level_matches(bare, ["mid"], include_unspecified=False)


def test_workplace_filter():
    assert workplace_matches(job(workplace_type="remote"), ["remote", "hybrid"])
    assert not workplace_matches(job(workplace_type="onsite"), ["remote"])
    assert not workplace_matches(job(workplace_type=None), ["remote"])
    assert workplace_matches(job(workplace_type=None), ["remote"], include_unspecified=True)
    assert workplace_matches(job(workplace_type=None), [])


def test_us_filter():
    assert us_matches(job(us_location=True), True)
    assert not us_matches(job(us_location=False), True)
    assert us_matches(job(us_location=None), True, include_unknown=True)
    assert not us_matches(job(us_location=None), True, include_unknown=False)
    assert us_matches(job(us_location=False), False)  # filter off
