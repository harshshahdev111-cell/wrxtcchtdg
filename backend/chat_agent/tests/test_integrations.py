import json
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings
from integrations.exceptions import IntegrationConfigurationError, ModelResponseError
from integrations.factory import get_model
from integrations.litellm import LiteLLMClient, LiteLLMService


@override_settings(
    LLM_API_KEY="test-only-key",
    LLM_BASE_URL="https://example.invalid/v1/",
    LLM_MODEL="groq/openai/gpt-oss-20b",
    LLM_MAX_COMPLETION_TOKENS=512,
)
class LiveModelScenarios(SimpleTestCase):
    @override_settings(LLM_BACKEND="live", LLM_MODEL="openai/gpt-4.1-mini", LLM_BASE_URL="")
    @patch("integrations.litellm.client.litellm.completion")
    def test_switching_ai_provider_uses_the_same_adapter(self, completion):
        """Route a second provider through LiteLLM without adding a new application client."""
        completion.return_value = {"choices": [{"message": {"content": '{"service":"plumber"}'}}]}
        plan, _ = get_model().plan("{}", timeout=5)
        self.assertEqual(plan, {"service": "plumber"})
        self.assertEqual(completion.call_args.kwargs["model"], "openai/gpt-4.1-mini")
        self.assertNotIn("api_base", completion.call_args.kwargs)
        self.assertEqual(completion.call_args.kwargs["num_retries"], 0)

    @patch("integrations.litellm.client.litellm.completion")
    def test_expired_network_budget_does_not_start_http(self, post):
        """Reject exhausted time instead of extending it with a minimum timeout."""
        with self.assertRaises(TimeoutError):
            LiteLLMClient().create_completion("{}", timeout=0)
        post.assert_not_called()

    @patch("integrations.litellm.client.litellm.completion")
    def test_network_phase_limits_are_capped_by_remaining_budget(self, post):
        """Bound connect and read waits independently while sharing the remaining budget."""
        LiteLLMClient().create_completion("{}", timeout=24)
        timeout = post.call_args.kwargs["timeout"]
        self.assertEqual(timeout.connect, 5)
        self.assertEqual(timeout.read, 20)
        self.assertLessEqual(timeout.read, 24)

    @patch("integrations.litellm.client.litellm.completion")
    def test_model_response_is_decoded_and_usage_preserved(self, post):
        """Preserve the model's decoded fields and usage for validation by the agent."""
        post.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "service": "plumber",
                                "earliest_time": "11:00",
                                "approved": True,
                            }
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 80},
        }
        plan, usage = LiteLLMService().plan('{"message":"Find a plumber"}', timeout=5)
        self.assertEqual(plan, {"service": "plumber", "earliest_time": "11:00", "approved": True})
        self.assertEqual(usage, {"total_tokens": 80})
        post.assert_called_once()

    @override_settings(LLM_API_KEY="")
    @patch("integrations.litellm.client.litellm.completion")
    def test_missing_key_fails_before_network_call(self, post):
        """Verify the expected invariant: missing key fails before network call."""
        with self.assertRaisesMessage(IntegrationConfigurationError, "LLM_API_KEY is missing"):
            LiteLLMService().plan("{}", timeout=5)
        post.assert_not_called()

    @patch("integrations.litellm.client.litellm.completion")
    def test_http_failure_propagates_to_agent(self, post):
        """Verify the expected invariant: http failure propagates to agent."""
        post.side_effect = requests.HTTPError("503")
        with self.assertRaises(requests.HTTPError):
            LiteLLMService().plan("{}", timeout=5)
        post.assert_called_once()

    @patch("integrations.litellm.client.litellm.completion")
    def test_timeout_propagates_without_hidden_retries(self, post):
        """Verify the expected invariant: timeout propagates without hidden retries."""
        post.side_effect = requests.Timeout("Timed out")
        with self.assertRaises(requests.Timeout):
            LiteLLMService().plan("{}", timeout=5)
        post.assert_called_once()

    @patch("integrations.litellm.client.litellm.completion")
    def test_malformed_model_json_is_not_accepted(self, post):
        """Verify the expected invariant: malformed model json is not accepted."""
        post.return_value = {
            "choices": [{"message": {"content": "not JSON"}}],
        }
        with self.assertRaises(ModelResponseError):
            LiteLLMService().plan("{}", timeout=5)

    def test_service_accepts_a_client_and_decodes_its_response(self):
        """Decode an injected client's response without changing its application fields."""
        client = Mock(spec=LiteLLMClient)
        client.create_completion.return_value = {
            "choices": [{"message": {"content": '{"area":"Indiranagar","approved":true}'}}],
        }
        plan, usage = LiteLLMService(client=client).plan("{}", timeout=5)
        self.assertEqual(plan, {"area": "Indiranagar", "approved": True})
        self.assertEqual(usage, {})
        client.create_completion.assert_called_once_with("{}", 5)

    @override_settings(LLM_BACKEND="live")
    @patch("integrations.litellm.client.litellm.completion")
    def test_factory_uses_the_configured_live_vendor(self, post):
        """Verify the expected invariant: factory uses the configured live vendor."""
        post.return_value = {
            "choices": [{"message": {"content": '{"service":"plumber"}'}}],
        }
        plan, _ = get_model().plan("{}", timeout=5)
        self.assertEqual(plan, {"service": "plumber"})
        self.assertEqual(post.call_args.kwargs["api_base"], "https://example.invalid/v1")
        self.assertEqual(post.call_args.kwargs["model"], "groq/openai/gpt-oss-20b")
        self.assertEqual(post.call_args.kwargs["max_completion_tokens"], 512)
        timeout = post.call_args.kwargs["timeout"]
        self.assertGreater(timeout.read, 0)
        self.assertLessEqual(timeout.read, 5)
        self.assertLessEqual(timeout.connect, 5)
        self.assertLessEqual(timeout.read, 5)
        self.assertEqual(post.call_args.kwargs["num_retries"], 0)
        self.assertEqual(post.call_args.kwargs["max_retries"], 0)

    @override_settings(LLM_BACKEND="demo")
    @patch("integrations.litellm.client.litellm.completion")
    def test_factory_demo_mode_stays_offline(self, post):
        """Verify the expected invariant: factory demo mode stays offline."""
        context = json.dumps({"message": json.dumps({"service": "plumber"})})
        plan, _ = get_model().plan(context, timeout=5)
        self.assertEqual(plan, {"service": "plumber"})
        post.assert_not_called()

    @patch("integrations.litellm.client.litellm.completion")
    def test_unknown_backend_is_rejected_without_network_access(self, post):
        """Verify the expected invariant: unknown backend is rejected without network access."""
        with self.assertRaisesMessage(
            IntegrationConfigurationError, "LLM_BACKEND must be demo or live"
        ):
            get_model(backend="unknown")
        post.assert_not_called()
