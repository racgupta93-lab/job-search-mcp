"""Lever public Postings API (no key needed for reading).

Docs: https://github.com/lever/postings-api
List: GET https://api.lever.co/v0/postings/{company}?mode=json
EU:   same path on https://api.eu.lever.co (for companies hosted in Lever's EU region)
"""

from __future__ import annotations

from typing import Any

from job_search_mcp.http_client import SafeHttpClient
from job_search_mcp.models import Job
from job_search_mcp.sanitize import clean_text, html_to_text
from job_search_mcp.sources.base import BoardSource
from job_search_mcp.sources.common import (
    normalize_country,
    parse_epoch_ms,
    period_from_interval,
    salary_fields,
)

_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}


def _description_html(raw: dict) -> str:
    """Lever splits a posting into several parts. Join them all, because
    statements about visa sponsorship often sit in the closing section."""
    parts = [raw.get("description") if isinstance(raw.get("description"), str) else ""]
    for item in raw.get("lists") or []:
        if isinstance(item, dict):
            parts.append(f"<h3>{item.get('text', '')}</h3>" if item.get("text") else "")
            parts.append(item.get("content") if isinstance(item.get("content"), str) else "")
    if isinstance(raw.get("additional"), str):
        parts.append(raw["additional"])
    return "\n".join(p for p in parts if isinstance(p, str))


def parse_posting(board: str, raw: Any, source: str = "lever") -> Job | None:
    if not isinstance(raw, dict):
        return None
    job_id, url = raw.get("id"), raw.get("hostedUrl")
    title = clean_text(raw.get("text"), 300)
    if not isinstance(job_id, str) or not isinstance(url, str) or not title:
        return None
    if not url.startswith("https://"):
        return None
    cats = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    location = clean_text(cats.get("location"), 300)
    departments = [clean_text(cats.get(k), 100) for k in ("department", "team")]
    pay = raw.get("salaryRange") if isinstance(raw.get("salaryRange"), dict) else {}
    workplace = raw.get("workplaceType")
    try:
        return Job(
            id=f"{source}:{board}:{clean_text(job_id, 100)}",
            source=source,
            company=board,
            title=title,
            location=location or None,
            country=normalize_country(raw.get("country")),
            workplace_type=_WORKPLACE.get(workplace) if isinstance(workplace, str) else None,
            url=url[:2000],
            posted_at=parse_epoch_ms(raw.get("createdAt")),
            departments=[d for d in departments if d],
            description=html_to_text(_description_html(raw), 20_000),
            **salary_fields(
                pay.get("min"),
                pay.get("max"),
                pay.get("currency"),
                period_from_interval(pay.get("interval")),
                "employer",
            ),
        )
    except ValueError:
        return None


class LeverSource(BoardSource):
    def __init__(self, http: SafeHttpClient, boards: list[str], *, eu: bool = False) -> None:
        super().__init__(http, boards)
        self.base_url = (
            "https://api.eu.lever.co/v0/postings" if eu else "https://api.lever.co/v0/postings"
        )
        self.name = "lever_eu" if eu else "lever"
        self.config_hint = f"optional: add company names under '{self.name}:' in companies.yaml"

    def _list_url(self, board: str) -> str:
        return f"{self.base_url}/{board}?mode=json"

    def _check_url(self, board: str) -> str:
        return f"{self.base_url}/{board}?mode=json&limit=1"

    def _extract(self, data: Any) -> list[Any] | None:
        return data if isinstance(data, list) else None

    def _parse(self, board: str, raw: Any) -> Job | None:
        return parse_posting(board, raw, self.name)
