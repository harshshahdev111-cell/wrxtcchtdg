"""Isolated ASGI fixture for concurrent waiting, cancellation and blocked-loop recovery."""

import asyncio
import json
import time
from importlib import import_module


class TestModel:
    backend = "demo"
    entered = 0
    peers_ready = None

    async def aplan(self, context, timeout):
        """Provide deterministic awaitable waits and one deliberately blocking failure mode."""
        message = json.loads(context)["message"]
        if message == "block-event-loop":
            time.sleep(60)
            raise AssertionError("Gunicorn should terminate the blocked event loop")
        if message == "wait-for-peers":
            if TestModel.peers_ready is None:
                TestModel.peers_ready = asyncio.Event()
            TestModel.entered += 1
            if TestModel.entered >= 6:
                TestModel.peers_ready.set()
            await asyncio.wait_for(TestModel.peers_ready.wait(), timeout=0.8)
            return {"service": "plumber"}, {}
        await asyncio.sleep(60)
        raise AssertionError("The async model deadline should cancel this wait")


def get_test_model():
    """Return the test-only model; no live AI request is possible on this server."""
    return TestModel()


async def application(scope, receive, send):
    """Load the real ASGI app and replace only the model factory in this isolated process."""
    actual = import_module("config.asgi").application
    service = import_module("chat_agent.services.agent")
    service.get_model = get_test_model
    await actual(scope, receive, send)
