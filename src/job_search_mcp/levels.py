"""Guess seniority from a job title. This is a HEURISTIC, not a fact.

Rules, strongest first: intern > manager > lead > senior > ladder codes and
numerals > junior > mid. It reads only the title (plus an optional hint from
the source), so:
  * a bare title like "Data Analyst" gives no level (None), not a guess;
  * companies use levels differently: "III", "L5", "E5" and "IC4" mean different
    things at different employers, so the numeric ladders are approximate
    (I=junior, II=mid, III=senior, IV and above=lead; E/L: 3=junior, 4=mid,
    5=senior, 6+=lead; IC: 1-2 junior, 3 mid, 4-5 senior, 6+ lead);
  * "Associate" is treated as junior, which is wrong at some firms;
  * Director / VP / Head of are all folded into "manager";
  * function titles such as "Product Manager" or "Account Manager" are jobs,
    not management levels, so they are not marked "manager";
  * years-of-experience text in the description is not used.
"""

from __future__ import annotations

import re

_I = re.IGNORECASE
_TECH = (
    r"(?:engineer|developer|data|software|machine learning|ml|ai|designer|analyst|"
    r"architect|swe|researcher)"
)
_ROLE = (
    r"(?:analyst|engineer|scientist|developer|specialist|consultant|designer|accountant|"
    r"associate|researcher|administrator|technician|representative|coordinator|architect)"
)
_FUNCTION_MANAGER = (
    r"(?:product|program|project|account|customer success|customer support|community|"
    r"social media|marketing|content|brand|partner|partnerships|case|property|store|office|"
    r"portfolio|vendor|delivery|release|solutions?|sales|channel|territory|relationship|"
    r"change|event|growth|category|quality|campaign|talent|recruiting|operations|market|city|regional|general)"
)

_INTERN = re.compile(r"\bintern(ship)?s?\b", _I)
_MANAGER_WORDS = re.compile(r"\b(director|head of|vp|vice president|svp|evp|chief|managing)\b", _I)
_MANAGER = re.compile(r"\b(manager|mgr)\b", _I)
_FUNCTION_MANAGER_RE = re.compile(rf"\b{_FUNCTION_MANAGER}\s+(manager|mgr)\b", _I)
_TEAM_MANAGER_RE = re.compile(
    r"\b(engineering|data|analytics|science|software|machine learning|ml|ai|research|"
    r"design|development|it|infrastructure|platform|security|technical|bi)\s+(manager|mgr)\b",
    _I,
)
_LEAD = re.compile(r"\b(lead|principal|distinguished)\b(?!\s*gen)", _I)
_STAFF = re.compile(rf"\bstaff\b(?=.*\b{_TECH}\b)|\b{_TECH}\b.*\bstaff\b", _I)
_SENIOR = re.compile(r"\b(senior|sr|snr)\b\.?", _I)
_JUNIOR = re.compile(
    r"\b(junior|jr|entry[- ]level|graduate|new grad|early career|apprentice)\b", _I
)
_ASSOCIATE = re.compile(
    r"\bassociate\b(?!\s+(director|vice|vp|principal|counsel|general|attorney|partner))", _I
)
_MID = re.compile(r"\b(mid[- ]level|intermediate)\b", _I)

# Case-sensitive on purpose: a numeral must be uppercase ("II"), not the pronoun "i".
_ROMAN_AFTER = re.compile(r"\s+(I{1,3}|IV|V)\b(?![\w.])")
_DIGIT_AFTER = re.compile(r"\s+([1-5])\b(?!\s*[+%])")
_LADDER = re.compile(r"(?<![A-Za-z0-9])(E|L|IC)-?([1-9])(?![A-Za-z0-9])")

_ROMAN_LEVELS = {"I": "junior", "II": "mid", "III": "senior", "IV": "lead", "V": "lead"}
_DIGIT_LEVELS = {"1": "junior", "2": "mid", "3": "senior", "4": "lead", "5": "lead"}
_HINTS = {
    "entry": "junior",
    "mid": "mid",
    "senior": "senior",
    "management": "manager",
    "internship": "intern",
}


def _ladder_level(prefix: str, number: int) -> str:
    if prefix == "IC":
        steps = [(2, "junior"), (3, "mid"), (5, "senior")]
    else:  # E and L ladders (Meta, Google and similar)
        steps = [(3, "junior"), (4, "mid"), (5, "senior")]
    for limit, level in steps:
        if number <= limit:
            return level
    return "lead"


def _numbered(title: str) -> str | None:
    ladder = _LADDER.search(title)
    if ladder:
        return _ladder_level(ladder.group(1), int(ladder.group(2)))
    for role in re.finditer(rf"\b{_ROLE}\b", title, _I):
        tail = title[role.end() :]
        roman = _ROMAN_AFTER.match(tail)
        if roman:
            return _ROMAN_LEVELS[roman.group(1)]
        digit = _DIGIT_AFTER.match(tail)
        if digit:
            return _DIGIT_LEVELS[digit.group(1)]
    return None


def _is_manager(title: str) -> bool:
    if _MANAGER_WORDS.search(title):
        return True
    if not _MANAGER.search(title):
        return False
    return bool(_TEAM_MANAGER_RE.search(title)) or not _FUNCTION_MANAGER_RE.search(title)


def _from_title(title: str) -> str | None:  # noqa: PLR0911  # one early return per rule reads best
    if _INTERN.search(title):
        return "intern"
    if _is_manager(title):
        return "manager"
    if _LEAD.search(title) or _STAFF.search(title):
        return "lead"
    if _SENIOR.search(title):
        return "senior"
    numbered = _numbered(title)
    if numbered:
        return numbered
    if _JUNIOR.search(title) or _ASSOCIATE.search(title):
        return "junior"
    if _MID.search(title):
        return "mid"
    return None


def detect_level(title: str, hint: str | None = None) -> tuple[str | None, str]:
    """Return (level, basis). basis is 'title', 'source_hint' or 'none'."""
    level = _from_title(title)
    if level:
        return level, "title"
    if hint and hint.lower() in _HINTS:
        return _HINTS[hint.lower()], "source_hint"
    return None, "none"
