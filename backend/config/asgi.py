"""Serve async Django requests, static assets and the worker's model HTTP pool."""

import os

from asgiref.sync import sync_to_async
from blacknoise import BlackNoise
from django.conf import settings
from django.core.asgi import get_asgi_application
from integrations.litellm.http import worker_http_pool
from integrations.llm.tokens import model_encoding

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django_application = get_asgi_application()

http_application = BlackNoise(django_application)
if settings.STATIC_ROOT.is_dir():
    http_application.add(settings.STATIC_ROOT, settings.STATIC_URL.rstrip("/"))


async def application(scope, receive, send):
    """Open/close pooled HTTP resources on lifespan events and dispatch normal requests."""
    if scope["type"] != "lifespan":
        await http_application(scope, receive, send)
        return
    await receive()
    try:
        await sync_to_async(model_encoding)(settings.LLM_MODEL)
        async with worker_http_pool():
            await send({"type": "lifespan.startup.complete"})
            await receive()
    except Exception:
        await send(
            {"type": "lifespan.startup.failed", "message": "ASGI startup or shutdown failed"}
        )
        raise
    await send({"type": "lifespan.shutdown.complete"})
