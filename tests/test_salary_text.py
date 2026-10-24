import pytest

from job_search_mcp.salary_text import extract_salary


def got(text):
    out = extract_salary(text)
    return (out.get("salary_min"), out.get("salary_max"), out.get("salary_period"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "The base salary range for this role is $150,000 - $200,000 per year.",
            (150000, 200000, "year"),
        ),
        ("Pay range: $120K-$150K", (120000, 150000, "year")),
        (
            "The United States base range for this position is $87,069-$128,000, plus equity.",
            (87069, 128000, "year"),
        ),
        ("Compensation: $90,000 to $110,000 USD annually", (90000, 110000, "year")),
        ("Salary between $95,000 and $120,000.", (95000, 120000, "year")),
        ("The hourly rate is $45 - $60 per hour.", (45, 60, "hour")),
        ("Pay: $38.50-$52/hr", (38, 52, "hour")),  # decimals rounded
        ("Base pay range is $8,000 - $11,000 per month", (8000, 11000, "month")),
        ("Our salary range is $87.5K – $110K", (87500, 110000, "year")),  # en dash, decimal k
        ("Annual base salary: $130,000—$160,000", (130000, 160000, "year")),  # em dash
    ],
)
def test_finds_real_ranges(text, expected):
    assert got(text) == expected


def test_labelled_posting_text_and_usd():
    out = extract_salary("The salary range is $100,000 - $130,000 per year.")
    assert out["salary_basis"] == "posting_text"
    assert out["salary_currency"] == "USD"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Competitive salary and great benefits.",
        "We raised $50 - $100 million in funding.",  # not pay
        "We have raised $100,000 - $250,000 in funding, valuation up",  # funding context
        "Annual equity grant of $50,000 - $100,000 in stock.",  # equity, not salary
        "Signing bonus $10,000 - $20,000.",  # bonus
        "Relocation stipend of $5,000 - $8,000 per year",  # stipend
        "Salary up to $200,000",  # single figure, not a range
        "Our office is at 100 - 200 Market Street, pay parking $5 - $10",  # no usable unit
        "Salary range $200,000 - $100,000",  # reversed
        "Salary range $1,000 - $100,000 per year",  # absurdly wide
        "Salary range $10 - $20 per year",  # too small to be an annual salary
        "Pay $5,000,000 - $6,000,000 per year",  # too large
        "Revenue grew $120,000 - $150,000 last quarter",  # no pay word nearby
        "Hourly pay is $2 - $3 per hour",  # too small
        "pay range 150,000 - 200,000",  # no dollar sign: not trusted
    ],
)
def test_ignores_non_salary_text(text):
    assert extract_salary(text) == {}


def test_multiple_tiers_give_overall_span():
    text = (
        "Pay range for NYC: $150,000 - $190,000 per year. "
        "Pay range for other US locations: $135,000 - $170,000 per year."
    )
    assert got(text) == (135000, 190000, "year")


def test_wildly_different_tiers_use_the_first_only():
    text = "Salary $60,000 - $80,000 per year. Another salary $300,000 - $400,000 per year."
    assert got(text) == (60000, 80000, "year")


def test_bonus_mentioned_after_salary_does_not_block_it():
    text = "Base salary range: $140,000 - $170,000 per year, plus annual bonus and equity."
    assert got(text) == (140000, 170000, "year")


def test_huge_text_is_handled_quickly():
    text = ("salary " + "$1 - " * 50 + "x" * 100) * 2000
    extract_salary(text)  # must simply finish


# ---- regressions found on live postings -------------------------------------


def test_currency_code_after_the_range_is_respected():
    text = "Mexico Monthly Pay Range\n\n$43,500—$48,333 MXN\n\nReasonable Accommodations"
    out = extract_salary(text)
    assert (out["salary_currency"], out["salary_period"]) == ("MXN", "month")


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("Salary range $90,000 - $110,000 CAD per year", "CAD"),
        ("Salary range CAD $90,000 - $110,000", "CAD"),
        ("Salary range $90,000 - $110,000 USD", "USD"),
        ("Salary range $90,000 - $110,000", "USD"),  # no code: assume USD
        ("Salary range $90,000 - $110,000 per year, TOTAL rewards", "USD"),  # "TOT" is not a code
    ],
)
def test_currency_detection(text, currency):
    assert extract_salary(text)["salary_currency"] == currency


def test_unit_from_heading_before_the_range():
    assert got("Hourly pay range: $30 - $40") == (30, 40, "hour")
