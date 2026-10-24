import pytest

from job_search_mcp.sources.common import (
    normalize_country,
    parse_epoch_ms,
    parse_iso_time,
    period_from_interval,
    salary_fields,
)


def test_parse_iso_time_variants():
    assert parse_iso_time("2026-10-01T09:00:00Z").isoformat() == "2026-10-01T09:00:00+00:00"
    assert parse_iso_time("2026-10-01T09:00:00-04:00").hour == 13
    assert parse_iso_time("2026-10-01T09:00:00.753+00:00").year == 2026
    assert parse_iso_time("2026-10-01").tzinfo is not None  # assumed UTC
    for bad in (None, 5, "yesterday", ""):
        assert parse_iso_time(bad) is None


def test_parse_epoch_ms():
    assert parse_epoch_ms(1792767640768).year == 2026
    for bad in (None, "1792767640768", True, 10**30, float("nan")):
        assert parse_epoch_ms(bad) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("US", "US"), ("usa", "US"), ("United States", "US"), ("CA", "CA"), (None, None), ("", None)],
)
def test_normalize_country(raw, expected):
    assert normalize_country(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("per-year-salary", "year"),
        ("1 YEAR", "year"),
        ("Annual", "year"),
        ("per-month-salary", "month"),
        ("per-hour-wage", "hour"),
        ("1 HOUR", "hour"),
        ("per-week-salary", None),
        ("one-time", None),
        ("NONE", None),
        (None, None),
    ],
)
def test_period_from_interval(raw, expected):
    assert period_from_interval(raw) == expected


def test_salary_fields_happy_path():
    out = salary_fields(120000, 150000.4, "usd", "year", "employer")
    assert out == {
        "salary_min": 120000,
        "salary_max": 150000,
        "salary_currency": "USD",
        "salary_period": "year",
        "salary_basis": "employer",
    }


@pytest.mark.parametrize(
    ("low", "high", "period"),
    [
        (None, None, "year"),  # nothing given
        (100, 50, "year"),  # min above max
        (0, 0, "year"),  # zero is not a salary
        (-5, -1, "year"),  # negative
        ("100000", "120000", "year"),  # strings are not trusted
        (True, True, "year"),
        (100000, 120000, None),  # unknown period: ambiguous, so dropped
        (10**9, 10**9, "year"),  # absurd
    ],
)
def test_salary_fields_rejects_bad_data(low, high, period):
    assert salary_fields(low, high, "USD", period, "employer") == {}


def test_salary_one_sided_is_kept_as_is():
    out = salary_fields(90000, None, "USD", "year", "employer")
    assert out["salary_min"] == 90000
    assert out["salary_max"] is None


def test_bad_currency_dropped_but_salary_kept():
    assert salary_fields(1, 2, "US$", "year", "employer")["salary_currency"] is None
