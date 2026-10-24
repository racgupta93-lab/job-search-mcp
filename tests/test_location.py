import pytest

from job_search_mcp.location import us_location


@pytest.mark.parametrize(
    "location",
    [
        "Austin, TX",
        "San Francisco, CA",
        "New York, NY",
        "Portland, OR",  # OR is Oregon, not the word "or"
        "Remote - US",
        "Remote, US",
        "US Remote",
        "United States",
        "United States (Remote)",
        "Remote - United States",
        "Boston, MA; Flexible / Remote",
        "Boston, Massachusetts",
        "New Mexico",  # must not be read as Mexico
        "Seattle",
        "New York City",
        "Washington, DC",
        "Austin, TX (Hybrid)",
        "Chicago, IL, USA",
        "London, UK or New York, NY",  # any US option counts
        "Bay Area",
    ],
)
def test_us_locations(location):
    assert us_location(None, location) is True


@pytest.mark.parametrize(
    "location",
    [
        "London, United Kingdom",
        "Toronto, ON, Canada",
        "Remote - Canada",
        "Bangalore, India",
        "Berlin, Germany",
        "Remote - Europe",
        "Sydney, Australia",
        "Mexico City, Mexico",
        "Remote, EMEA",
        "São Paulo, Brazil",
        "Bangalore, India; Hyderabad, India",
        "Remote (ROU)",
    ],
)
def test_non_us_locations(location):
    assert us_location(None, location) is False


@pytest.mark.parametrize(
    "location", [None, "", "Remote", "Flexible / Remote", "Anywhere", "Hybrid", "North America"]
)
def test_unknown_locations(location):
    assert us_location(None, location) is None


def test_mixed_remote_and_foreign_is_unknown_not_excluded():
    # "Remote" could be anywhere, so we don't exclude it just because India is listed.
    assert us_location(None, "Flexible / Remote; New Delhi, India") is None


def test_country_field_wins_over_text():
    assert us_location("US", "London") is True
    assert us_location("GB", "Austin, TX") is False
    assert us_location("us", None) is True


@pytest.mark.parametrize("location", ["Ljubljana, Slovenia", "Gurugram, India", "Zagreb, Croatia"])
def test_more_non_us_locations(location):
    assert us_location(None, location) is False
