import httpx

from job_search_mcp.http_client import SafeHttpClient


async def no_sleep(_: float) -> None:
    return None


def make_http(handler) -> SafeHttpClient:
    return SafeHttpClient(
        max_response_bytes=1_000_000,
        requests_per_second=1000,
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )
