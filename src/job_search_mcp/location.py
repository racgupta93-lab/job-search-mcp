"""Decide whether a posting is in the United States, from its location text.

Returns True (US), False (clearly elsewhere) or None (the text doesn't say,
e.g. just "Remote"). We prefer None over a wrong answer, so unsure postings
are kept and marked rather than silently dropped.

Limits: a keyword heuristic. Unusual spellings, small towns without a state,
and "Remote" roles that are really restricted to one country will be unknown.
"""

from __future__ import annotations

import re

_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
]  # fmt: skip
_US_PLACES = [
    "nyc", "brooklyn", "san francisco", "bay area", "los angeles", "seattle", "austin",
    "boston", "chicago", "denver", "atlanta", "dallas", "houston", "miami", "san diego",
    "san jose", "portland", "phoenix", "philadelphia", "minneapolis", "nashville", "raleigh",
    "salt lake city", "pittsburgh", "detroit", "charlotte", "boulder", "palo alto",
    "mountain view", "menlo park", "sunnyvale", "santa clara", "redwood city", "oakland",
    "san mateo", "irvine", "tampa", "orlando", "indianapolis", "kansas city", "st. louis",
    "baltimore", "las vegas", "sacramento", "honolulu", "washington dc", "washington, d.c",
]  # fmt: skip
_FOREIGN = [
    "india", "bangalore", "bengaluru", "hyderabad", "mumbai", "delhi", "pune", "chennai",
    "united kingdom", "uk", "england", "london", "scotland", "ireland", "canada", "toronto",
    "vancouver", "montreal", "ottawa", "calgary", "germany", "berlin", "munich", "france",
    "paris", "spain", "madrid", "barcelona", "portugal", "lisbon", "italy", "milan",
    "netherlands", "amsterdam", "belgium", "brussels", "switzerland", "zurich", "austria",
    "vienna", "sweden", "stockholm", "norway", "oslo", "denmark", "copenhagen", "finland",
    "helsinki", "poland", "warsaw", "krakow", "romania", "rou", "bucharest", "czech",
    "prague", "hungary", "budapest", "ukraine", "turkey", "istanbul", "israel", "tel aviv",
    "uae", "dubai", "saudi", "egypt", "nigeria", "lagos", "kenya", "nairobi", "south africa",
    "cape town", "brazil", "sao paulo", "são paulo", "mexico", "argentina", "buenos aires",
    "colombia", "bogota", "chile", "peru", "costa rica", "australia", "sydney", "melbourne",
    "new zealand", "auckland", "singapore", "japan", "tokyo", "china", "beijing", "shanghai",
    "hong kong", "taiwan", "korea", "seoul", "philippines", "manila", "vietnam", "thailand",
    "bangkok", "indonesia", "jakarta", "malaysia", "emea", "apac", "latam", "europe",
    "slovenia", "ljubljana", "croatia", "zagreb", "serbia", "belgrade", "bulgaria", "sofia",
    "greece", "athens", "lithuania", "vilnius", "latvia", "riga", "estonia", "tallinn",
    "luxembourg", "slovakia", "bratislava", "cyprus", "malta", "iceland", "morocco", "ghana",
    "pakistan", "bangladesh", "sri lanka", "uruguay", "ecuador", "panama", "guatemala",
    "dominican republic", "puerto rico_", "qatar", "kuwait", "jordan", "kazakhstan",
    "georgia_", "armenia", "ho chi minh", "kuala lumpur", "taipei", "osaka", "gurgaon",
    "gurugram", "noida", "kolkata", "ahmedabad", "kyiv", "lviv", "cluj", "gdansk", "wroclaw",
]  # fmt: skip


def _any_of(words: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


# Lowercase "or" separates alternatives ("Boston, MA or Remote"); uppercase "OR"
# is Oregon ("Portland, OR"), so this pattern is deliberately case-sensitive.
_SPLIT = re.compile(r"\s*(?:;|\||•|/|\bor\b)\s*")
_US_WORD = re.compile(r"\b(united states|usa|u\.s\.a?\.?)\b", re.IGNORECASE)
_US_UPPER = re.compile(r"\bUS\b")  # case-sensitive: "Remote - US", never the word "us"
_STATE_NAME = _any_of(_STATE_NAMES)
_FOREIGN_PLACE = _any_of(_FOREIGN)
_US_PLACE = _any_of(_US_PLACES)


def _part(part: str) -> bool | None:  # noqa: PLR0911  # one early return per rule reads best
    text = part.strip()
    if not text:
        return None
    if _US_WORD.search(text) or _US_UPPER.search(text):
        return True
    tokens = [t.strip(") .") for t in re.split(r"[,(]", text)[1:]]
    if any(t in _STATE_CODES for t in tokens):
        return True
    # State names come before foreign names so "New Mexico" is not read as Mexico.
    if _STATE_NAME.search(text):
        return True
    if _FOREIGN_PLACE.search(text):
        return False
    if _US_PLACE.search(text):
        return True
    return None


def us_location(country: str | None, location: str | None) -> bool | None:
    """True / False / None as described in the module docstring."""
    if country:
        return country.upper() == "US"
    if not location:
        return None
    verdicts = [_part(p) for p in _SPLIT.split(location)]
    if any(v is True for v in verdicts):
        return True
    if verdicts and all(v is False for v in verdicts):
        return False
    return None
