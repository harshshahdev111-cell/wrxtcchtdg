"""Own the async model HTTP pool for one ASGI worker and close it on shutdown."""

from contextlib import asynccontextmanager

import httpx
import litellm
from django.conf import settings
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

_handler = None


def new_client():
    """Create a bounded connection pool with no automatic redirects or retries."""
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=settings.LLM_MAX_CONNECTIONS,
            max_keepalive_connections=min(settings.LLM_MAX_CONNECTIONS, 20),
        ),
        follow_redirects=False,
    )


async def cap_request_timeouts(request):
    """Keep application phase limits when an SDK provider normalizes timeout to one number."""
    timeouts = request.extensions.setdefault("timeout", {})
    for phase, cap in (
        ("connect", settings.LLM_CONNECT_TIMEOUT),
        ("read", settings.LLM_READ_TIMEOUT),
        ("pool", settings.LLM_POOL_TIMEOUT),
    ):
        value = timeouts.get(phase)
        timeouts[phase] = cap if value is None else min(value, cap)


@asynccontextmanager
async def worker_http_pool():
    """Share one pool for the worker lifetime; never retain a closed client after shutdown."""
    global _handler
    async with new_client() as client:
        client.event_hooks["request"].append(cap_request_timeouts)
        previous = litellm.aclient_session
        litellm.aclient_session = client
        handler = AsyncHTTPHandler(follow_redirects=False)
        await handler.close()
        handler.client = client
        _handler = handler
        try:
            yield
        finally:
            await litellm.close_litellm_async_clients()
            litellm.aclient_session = previous
            _handler = None


def shared_groq_handler():
    """Expose the SDK HTTP handler used by its Groq route; other routes use SDK-managed clients."""
    return _handler
