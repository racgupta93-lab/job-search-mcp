"""The only code in this project that talks to the internet.

Every request goes through SafeHttpClient, which enforces:
  * HTTPS only, and only to hosts on ALLOWED_HOSTS (checked again on every
    redirect, so a job board cannot bounce us to some other site);
  * timeouts (per-chunk and overall), retries with exponential backoff, and a
    per-host rate limit;
  * a cap on how many bytes we will read from any response;
  * no secrets in error messages: URLs are reported without their query string,
    because Adzuna keys travel in the query.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

# Quiet the libraries that would otherwise log full URLs (including API keys).
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

ALLOWED_HOSTS = frozenset(
    {
        "boards-api.greenhouse.io",
        "api.lever.co",
        "api.eu.lever.co",
        "api.ashbyhq.com",
        "api.adzuna.com",
        "www.themuse.com",
    }
)
USER_AGENT = "job-search-mcp/0.1 (personal job search; read-only)"
MAX_REDIRECTS = 3
MAX_ATTEMPTS = 3
OVERALL_TIMEOUT_SECONDS = 45.0
MAX_RETRY_AFTER_SECONDS = 10.0
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_RETRY_CODES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """A request failed. The message never contains a query string."""


class NotFoundError(FetchError):
    """HTTP 404."""


class BlockedURLError(FetchError):
    """The URL was refused by our security rules (never retried)."""


class _Retry(Exception):  # noqa: N818
    def __init__(self, reason: str, delay: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay = delay


def validate_url(url: str, allowed_hosts: frozenset[str] = ALLOWED_HOSTS) -> str:
    """Refuse anything that is not plain https to an allowlisted host."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BlockedURLError("malformed URL") from None
    if parts.scheme != "https":
        raise BlockedURLError("only https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise BlockedURLError("URLs with embedded credentials are not allowed")
    if port not in (None, 443):
        raise BlockedURLError("non-standard ports are not allowed")
    host = (parts.hostname or "").lower()
    if host not in allowed_hosts:
        raise BlockedURLError(f"host is not on the allowlist: {host[:60]!r}")
    return url


def safe_label(url: str) -> str:
    """host + path only, so keys in the query string never reach messages."""
    parts = urlsplit(url)
    return f"{parts.hostname}{parts.path}"[:200]


class _HostLimiter:
    """Spaces requests to the same host at least 1/rps seconds apart."""

    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / requests_per_second
        self._next_slot: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot.get(host, 0.0))
            self._next_slot[host] = slot + self._interval
        if slot > now:
            await asyncio.sleep(slot - now)


class SafeHttpClient:
    def __init__(
        self,
        *,
        max_response_bytes: int,
        requests_per_second: float = 2.0,
        allowed_hosts: frozenset[str] = ALLOWED_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._max_bytes = max_response_bytes
        self._allowed = allowed_hosts
        self._limiter = _HostLimiter(requests_per_second)
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,  # we follow redirects ourselves, checked
            trust_env=False,  # ignore proxy/netrc environment surprises
            timeout=httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        body = await self.get_bytes(url, params=params, headers=headers)
        try:
            return json.loads(body)
        except ValueError:
            raise FetchError(f"invalid JSON from {safe_label(url)}") from None

    async def get_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
    ) -> bytes:
        validate_url(url, self._allowed)
        label = safe_label(url)
        last_reason = "unknown error"
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with asyncio.timeout(OVERALL_TIMEOUT_SECONDS):
                    return await self._fetch_once(url, params, headers, method)
            except _Retry as retry:
                last_reason = retry.reason
                delay = retry.delay
            except (httpx.TransportError, TimeoutError) as exc:
                last_reason = type(exc).__name__
                delay = None
            except FetchError:
                raise  # already safe: messages never contain a query string
            except Exception as exc:  # last line of defence for the API key
                # An unexpected library error can carry the full URL, key included, in its
                # message. Report only its type, and drop the original from the traceback.
                raise FetchError(f"{label} failed unexpectedly ({type(exc).__name__})") from None
            if attempt < MAX_ATTEMPTS - 1:
                backoff = delay if delay is not None else 0.5 * 2**attempt
                await self._sleep(backoff + random.uniform(0, 0.25))  # noqa: S311  # nosec B311  # retry jitter only
        raise FetchError(f"{label} failed after {MAX_ATTEMPTS} attempts ({last_reason})")

    async def _fetch_once(
        self,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        method: str,
    ) -> bytes:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            host = (urlsplit(current).hostname or "").lower()
            await self._limiter.wait(host)
            # Query params and custom headers are sent to the first host only.
            async with self._client.stream(
                method,
                current,
                params=params if hop == 0 else None,
                headers=headers if hop == 0 else None,
            ) as resp:
                status = resp.status_code
                if status in _REDIRECT_CODES:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("redirect without a location")
                    current = validate_url(urljoin(current, location), self._allowed)
                    continue
                if status == 404:
                    raise NotFoundError(f"{safe_label(current)} not found (404)")
                if status in _RETRY_CODES:
                    raise _Retry(f"HTTP {status}", _retry_after(resp))
                if not 200 <= status < 300:
                    raise FetchError(f"{safe_label(current)} returned HTTP {status}")
                if method == "HEAD":
                    return b""
                return await self._read_capped(resp, current)
        raise FetchError("too many redirects")

    async def _read_capped(self, resp: httpx.Response, url: str) -> bytes:
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise FetchError(f"response from {safe_label(url)} is too large")
        buffer = bytearray()
        # aiter_bytes() yields *decompressed* data, so the cap also stops
        # "zip bombs" (tiny downloads that expand enormously).
        async for chunk in resp.aiter_bytes():
            buffer += chunk
            if len(buffer) > self._max_bytes:
                raise FetchError(f"response from {safe_label(url)} is too large")
        return bytes(buffer)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after", "")
    if raw.isdigit():
        return min(float(raw), MAX_RETRY_AFTER_SECONDS)
    return None
