"""Verify awaited model HTTP, cancellation, shared pooling and database-safe async chat."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from integrations.exceptions import IntegrationConfigurationError, ModelResponseError
from integrations.litellm import LiteLLMClient, LiteLLMService
from integrations.litellm.http import worker_http_pool

from chat_agent.models import Conversation, MockBooking
from chat_agent.services.agent import Agent


@override_settings(LLM_API_KEY="test-only-key", LLM_MODEL="groq/openai/gpt-oss-20b")
class AsyncModelScenarios(SimpleTestCase):
    async def test_async_http_reuses_worker_pool_and_closes_it_on_shutdown(self):
        """Use the same open HTTP pool for multiple requests and close it exactly once."""
        requests = []

        async def respond(request):
            """Record the actual outbound JSON and return a deterministic compatible response."""
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "test-response",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "openai/gpt-oss-20b",
                    "service_tier": "on_demand",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "{}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                },
            )

        pool = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("integrations.litellm.http.new_client", return_value=pool) as create:
            async with worker_http_pool():
                await LiteLLMService().aplan('{"message":"Find a plumber"}', 5)
                await LiteLLMService().aplan('{"message":"Find an electrician"}', 5)
                self.assertFalse(pool.is_closed)
            self.assertTrue(pool.is_closed)
            create.assert_called_once()
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer test-only-key")
        body = json.loads(requests[0].content)
        self.assertEqual([message["role"] for message in body["messages"]], ["system", "user"])
        self.assertLessEqual(requests[0].extensions["timeout"]["pool"], 1)

    async def test_async_http_deadline_cancels_slow_response(self):
        """A slow provider coroutine is cancelled rather than left running after the timeout."""
        cancelled = asyncio.Event()

        async def never_respond(request):
            """Simulate a network wait that only ends when its task is cancelled."""
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        pool = httpx.AsyncClient(transport=httpx.MockTransport(never_respond))
        with patch("integrations.litellm.http.new_client", return_value=pool):
            async with worker_http_pool():
                with self.assertRaises(TimeoutError):
                    await LiteLLMClient().acreate_completion("{}", 0.5)
                self.assertTrue(cancelled.is_set())

    @override_settings(LLM_API_KEY="")
    async def test_async_missing_key_does_not_open_http_client(self):
        """Reject missing credentials before allocating connections or sending requests."""
        with patch("integrations.litellm.http.new_client") as create:
            with self.assertRaises(IntegrationConfigurationError):
                await LiteLLMClient().acreate_completion("{}", 5)
        create.assert_not_called()

    async def test_async_oversized_input_and_expired_budget_stop_before_http(self):
        """Direct async client callers cannot bypass input-token or elapsed-time limits."""
        with patch("integrations.litellm.http.new_client") as create:
            with self.assertRaises(IntegrationConfigurationError):
                await LiteLLMClient().acreate_completion("huge " * 10000, 5)
            with self.assertRaises(TimeoutError):
                await LiteLLMClient().acreate_completion("{}", 0)
        create.assert_not_called()

    async def test_async_malformed_plan_keeps_usage(self):
        """Retain provider usage when async output decoding fails, without saving raw text."""
        client = LiteLLMClient()
        client.acreate_completion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": "not JSON"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }
        )
        with self.assertRaises(ModelResponseError) as caught:
            await LiteLLMService(client).aplan("{}", 5)
        self.assertEqual(caught.exception.usage["completion_tokens"], 20)


@override_settings(LLM_BACKEND="demo", LLM_MODEL="groq/openai/gpt-oss-20b")
class AsyncChatScenarios(TransactionTestCase):
    async def test_awaiting_one_chat_does_not_block_another_chat(self):
        """Two independent chats must both enter model waiting before either can finish."""
        first = await Conversation.objects.acreate()
        second = await Conversation.objects.acreate()
        entered = 0
        ready = asyncio.Event()

        class ConcurrentModel:
            backend = "demo"

            async def aplan(self, context, timeout):
                """Release both model responses only after both requests reach their async wait."""
                nonlocal entered
                entered += 1
                if entered == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=2)
                return {"service": "plumber"}, {}

        model = ConcurrentModel()
        results = await asyncio.gather(
            Agent(first, model=model).achat("Find a plumber"),
            Agent(second, model=model).achat("Find another plumber"),
        )
        self.assertEqual([result["state"] for result in results], ["collecting", "collecting"])
        self.assertEqual(entered, 2)
        self.assertFalse(await MockBooking.objects.aexists())

    async def test_async_model_cancellation_records_trace_and_propagates_disconnect(self):
        """Client cancellation leaves a trace and never starts booking tools."""
        conversation = await Conversation.objects.acreate()
        entered = asyncio.Event()

        class WaitingModel:
            backend = "demo"

            async def aplan(self, context, timeout):
                """Wait until the client disconnects and cancels the agent task."""
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(
            Agent(conversation, model=WaitingModel()).achat("Find a plumber")
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        event = await conversation.traces.aget(kind="model")
        self.assertEqual(event.data["error"], "CancelledError")
        self.assertFalse(await conversation.traces.filter(kind="tool").aexists())

    @override_settings(AGENT_MAX_SECONDS=1)
    async def test_async_agent_deadline_returns_clear_timeout_without_tools(self):
        """An async model ignoring its argument is still cancelled by the agent's deadline."""
        conversation = await Conversation.objects.acreate()

        class SlowModel:
            backend = "demo"

            async def aplan(self, context, timeout):
                """Simulate a correctly awaitable but very slow model."""
                await asyncio.sleep(10)

        result = await Agent(conversation, model=SlowModel()).achat("Find a plumber")
        self.assertEqual(result["state"], "limited")
        self.assertIn("timed out", result["message"])
        event = await conversation.traces.aget(kind="model")
        self.assertEqual(event.data["error"], "TimeoutError")
        self.assertFalse(await conversation.traces.filter(kind="tool").aexists())

    async def test_async_pool_exhaustion_has_clear_outcome(self):
        """A full HTTP pool ends the request safely instead of waiting without a bound."""
        conversation = await Conversation.objects.acreate()
        model = LiteLLMService()
        model.aplan = AsyncMock(side_effect=httpx.PoolTimeout("Connection pool busy"))
        result = await Agent(conversation, model=model).achat("Find a plumber")
        self.assertEqual(result["state"], "limited")
        self.assertIn("busy", result["message"])
        self.assertFalse(await MockBooking.objects.aexists())
