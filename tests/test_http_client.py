import asyncio

import httpx
import pytest

from job_search_mcp.http_client import (
    BlockedURLError,
    FetchError,
    NotFoundError,
    SafeHttpClient,
    safe_label,
    validate_url,
)

GOOD = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"


async def _no_sleep(_: float) -> None:
    return None


def make_client(handler, max_bytes=1_000_000):
    return SafeHttpClient(
        max_response_bytes=max_bytes,
        requests_per_second=1000,
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "url",
    [
        "http://boards-api.greenhouse.io/v1/boards/a/jobs",  # not https
        "https://evil.example.com/x",  # not allowlisted
        "https://boards-api.greenhouse.io.evil.com/x",  # look-alike host
        "https://boards-api.greenhouse.io@evil.com/x",  # credentials trick
        "https://user:pw@boards-api.greenhouse.io/x",  # pragma: allowlist secret
        "https://boards-api.greenhouse.io:8443/x",  # odd port
        "https://boards-api.greenhouse.io./x",  # trailing dot
        "file:///etc/passwd",
        "ftp://boards-api.greenhouse.io/x",
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "",
    ],
)
def test_blocked_urls(url):
    with pytest.raises(BlockedURLError):
        validate_url(url)


def test_allowed_url_passes():
    assert validate_url(GOOD) == GOOD


def test_get_json_ok():
    client = make_client(lambda req: httpx.Response(200, json={"a": 1}))
    assert run(client.get_json(GOOD)) == {"a": 1}


def test_request_to_blocked_host_never_sent():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={})

    client = make_client(handler)
    with pytest.raises(BlockedURLError):
        run(client.get_json("https://evil.example.com/x"))
    assert calls == []


def test_redirect_to_allowlisted_host_is_followed():
    def handler(req):
        if req.url.host == "boards-api.greenhouse.io":
            return httpx.Response(302, headers={"location": "https://api.lever.co/ok"})
        return httpx.Response(200, json={"ok": True})

    assert run(make_client(handler).get_json(GOOD)) == {"ok": True}


def test_redirect_to_other_host_is_blocked():
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    with pytest.raises(BlockedURLError):
        run(make_client(handler).get_json(GOOD))
    assert seen == ["boards-api.greenhouse.io"]


def test_redirect_downgrade_to_http_is_blocked():
    def handler(req):
        return httpx.Response(302, headers={"location": "http://api.lever.co/x"})

    with pytest.raises(BlockedURLError):
        run(make_client(handler).get_json(GOOD))


def test_redirect_loop_is_stopped():
    def handler(req):
        return httpx.Response(302, headers={"location": GOOD})

    with pytest.raises(FetchError, match="too many redirects"):
        run(make_client(handler).get_json(GOOD))


def test_query_params_not_forwarded_across_redirect():
    queries = []

    def handler(req):
        queries.append(req.url.query.decode())
        if len(queries) == 1:
            return httpx.Response(302, headers={"location": "https://api.lever.co/x"})
        return httpx.Response(200, json={})

    run(make_client(handler).get_json(GOOD, params={"app_key": "SECRET"}))
    assert "SECRET" in queries[0]
    assert queries[1] == ""


def test_response_size_cap_by_content_length():
    client = make_client(lambda r: httpx.Response(200, content=b"x" * 5000), max_bytes=1000)
    with pytest.raises(FetchError, match="too large"):
        run(client.get_json(GOOD))


def test_response_size_cap_while_streaming():
    class Streaming(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(50):
                yield b"x" * 100

    client = make_client(lambda r: httpx.Response(200, stream=Streaming()), max_bytes=1000)
    with pytest.raises(FetchError, match="too large"):
        run(client.get_json(GOOD))


def test_retries_then_succeeds():
    attempts = []

    def handler(req):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": 1})

    assert run(make_client(handler).get_json(GOOD)) == {"ok": 1}
    assert len(attempts) == 3


def test_gives_up_after_max_attempts():
    attempts = []

    def handler(req):
        attempts.append(1)
        return httpx.Response(429, headers={"retry-after": "1"})

    with pytest.raises(FetchError, match="failed after 3 attempts"):
        run(make_client(handler).get_json(GOOD))
    assert len(attempts) == 3


def test_404_is_not_retried():
    attempts = []

    def handler(req):
        attempts.append(1)
        return httpx.Response(404)

    with pytest.raises(NotFoundError):
        run(make_client(handler).get_json(GOOD))
    assert len(attempts) == 1


def test_other_4xx_not_retried():
    attempts = []

    def handler(req):
        attempts.append(1)
        return httpx.Response(403)

    with pytest.raises(FetchError, match="403"):
        run(make_client(handler).get_json(GOOD))
    assert len(attempts) == 1


def test_invalid_json_is_a_clean_error():
    client = make_client(lambda r: httpx.Response(200, content=b"<html>not json"))
    with pytest.raises(FetchError, match="invalid JSON"):
        run(client.get_json(GOOD))


def test_errors_never_leak_query_string_secrets():
    client = make_client(lambda r: httpx.Response(500))
    url = "https://api.adzuna.com/v1/api/jobs/us/search/1"
    with pytest.raises(FetchError) as info:
        run(client.get_json(url, params={"app_key": "TOPSECRET", "app_id": "ID123"}))
    assert "TOPSECRET" not in str(info.value)
    assert "ID123" not in str(info.value)


def test_transport_errors_are_retried_then_reported():
    attempts = []

    def handler(req):
        attempts.append(1)
        raise httpx.ConnectError("boom with TOPSECRET")

    with pytest.raises(FetchError) as info:
        run(make_client(handler).get_json(GOOD, params={"k": "TOPSECRET"}))
    assert len(attempts) == 3
    assert "TOPSECRET" not in str(info.value)


def test_safe_label_drops_query():
    assert safe_label("https://api.adzuna.com/a/b?app_key=SECRET") == "api.adzuna.com/a/b"


def test_unexpected_library_errors_cannot_leak_the_api_key():
    # A library exception whose message contains the full URL (key included) must be
    # replaced by a safe error that carries only the exception type.
    def handler(req):
        raise ValueError(f"unexpected failure for {req.url}")

    client = make_client(handler)
    with pytest.raises(FetchError) as info:
        run(
            client.get_json(
                "https://api.adzuna.com/v1/api/jobs/us/search/1",
                params={"app_key": "TOPSECRETKEY"},  # pragma: allowlist secret
            )
        )
    err = info.value
    assert "TOPSECRETKEY" not in str(err) and "TOPSECRETKEY" not in repr(
        err
    )  # pragma: allowlist secret
    assert "ValueError" in str(err)
    assert err.__cause__ is None and err.__suppress_context__ is True


# ---- POST (used for Workday-style search requests) ---------------------------------------


def test_post_json_sends_the_body_and_parses_the_answer():
    seen = []

    def handler(req):
        seen.append((req.method, req.content, req.headers.get("content-type")))
        return httpx.Response(200, json={"total": 3})

    out = run(make_client(handler).post_json(GOOD, {"searchText": "data", "limit": 20}))
    assert out == {"total": 3}
    method, content, ctype = seen[0]
    assert method == "POST" and b'"searchText"' in content and "application/json" in ctype


def test_post_is_still_limited_to_the_allowlist_and_https():
    client = make_client(lambda r: httpx.Response(200, json={}))
    for bad in ("https://evil.example.com/x", "http://boards-api.greenhouse.io/x"):
        with pytest.raises(BlockedURLError):
            run(client.post_json(bad, {}))


def test_a_post_that_is_redirected_is_refused_not_replayed():
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(307, headers={"location": "https://api.lever.co/elsewhere"})

    with pytest.raises(FetchError, match="redirected a POST"):
        run(make_client(handler).post_json(GOOD, {"a": 1}))
    assert seen == ["boards-api.greenhouse.io"]  # the body never reached the other host


def test_post_retries_and_404_behave_like_get():
    attempts = []

    def flaky(req):
        attempts.append(1)
        return httpx.Response(503) if len(attempts) < 3 else httpx.Response(200, json={"ok": 1})

    assert run(make_client(flaky).post_json(GOOD, {})) == {"ok": 1}
    with pytest.raises(NotFoundError):
        run(make_client(lambda r: httpx.Response(404)).post_json(GOOD, {}))
