import pytest

from job_search_mcp.levels import detect_level


def level(title, hint=None):
    return detect_level(title, hint)[0]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # junior
        ("Junior Data Analyst", "junior"),
        ("Jr. Analytics Engineer", "junior"),
        ("Entry Level Data Analyst", "junior"),
        ("Associate Data Analyst", "junior"),
        ("New Grad Software Engineer", "junior"),
        ("Data Analyst I", "junior"),
        ("Data Analyst 1", "junior"),
        ("Software Engineer, E3", "junior"),
        # mid
        ("Data Analyst II", "mid"),
        ("Analytics Engineer 2", "mid"),
        ("Mid-Level Data Scientist", "mid"),
        ("Software Engineer (L4)", "mid"),
        ("Data Scientist, E4", "mid"),
        # senior
        ("Senior Data Analyst", "senior"),
        ("Sr. Analytics Engineer", "senior"),
        ("Sr Data Scientist", "senior"),
        ("Data Analyst III", "senior"),
        ("Software Engineer, E5", "senior"),
        ("Machine Learning Engineer L5", "senior"),
        ("Data Engineer IC4", "senior"),
        ("Senior Product Manager", "senior"),  # a job function, not a people manager
        # lead / staff
        ("Lead Data Analyst", "lead"),
        ("Staff Software Engineer", "lead"),
        ("Staff Data Scientist", "lead"),
        ("Principal Analytics Engineer", "lead"),
        ("Data Analyst IV", "lead"),
        ("Software Engineer, E6", "lead"),
        ("Senior Staff Engineer", "lead"),  # lead outranks senior
        ("Tech Lead, Data Platform", "lead"),
        # manager
        ("Analytics Manager", "manager"),
        ("Engineering Manager, Data", "manager"),
        ("Senior Manager, Data Science", "manager"),
        ("Director of Analytics", "manager"),
        ("Head of Data", "manager"),
        ("VP, Data and Insights", "manager"),
        ("Vice President of Engineering", "manager"),
        ("Associate Director, Analytics", "manager"),  # not "junior"
        # intern
        ("Data Analyst Intern", "intern"),
        ("Summer 2027 Internship - Analytics", "intern"),
    ],
)
def test_detects_levels(title, expected):
    assert level(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Data Analyst",
        "Analytics Engineer",
        "Product Manager",  # function title, not a management level
        "Account Manager",
        "Program Manager, Operations",
        "Lead Generation Specialist",  # not a "lead" level
        "Staff Accountant",  # not an engineering staff level
        "Customer Success Manager",
        "Data Analyst, Pricing",
    ],
)
def test_no_level_when_title_gives_no_cue(title):
    assert detect_level(title) == (None, "none")


def test_basis_reports_where_the_answer_came_from():
    assert detect_level("Senior Data Analyst") == ("senior", "title")
    assert detect_level("Data Analyst", "senior") == ("senior", "source_hint")
    assert detect_level("Data Analyst", "entry") == ("junior", "source_hint")
    assert detect_level("Data Analyst", "weird") == (None, "none")


def test_title_beats_source_hint():
    assert detect_level("Junior Data Analyst", "senior") == ("junior", "title")


@pytest.mark.parametrize(
    "title",
    [
        "Engineer 3+ years experience",  # "3+" is a requirement, not a level
        "Engineer 5% travel",
        "Data Analyst, Team i",  # lowercase i is not a numeral
        "Analyst, I want to learn",  # prose
    ],
)
def test_numeral_false_positives(title):
    result = level(title)
    assert result not in {"senior", "lead"}


def test_weird_input_does_not_crash():
    for title in ["", "   ", "!!!", "I" * 5000, "Sr" * 3000]:
        detect_level(title)
