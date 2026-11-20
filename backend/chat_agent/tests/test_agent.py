import json
import time
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from unittest.mock import Mock, patch

import requests
from django.test import override_settings
from django.utils import timezone
from integrations.litellm import LiteLLMService
from integrations.providers.client import MockProviderClient
from rest_framework.test import APITestCase

from chat_agent.models import Conversation, Memory, MockBooking, MockHold, Proposal
from chat_agent.services.agent import Agent


@override_settings(LLM_BACKEND="demo")
class AgentScenarios(APITestCase):
    def test_model_network_timeout_returns_retry_message_without_tools(self):
        """Treat an AI network timeout as a bounded failure without attempting booking."""
        model = Mock()
        model.plan.side_effect = requests.Timeout()
        result = Agent(self.conversation, model=model).chat("Find a plumber")
        self.assertEqual(result["state"], "limited")
        self.assertIn("timed out", result["message"])
        self.assertFalse(self.conversation.traces.filter(kind="tool").exists())

    def test_late_provider_result_stays_unresolved_then_reconciles(self):
        """A provider success beyond the budget must be checked by a later run."""
        prepared = self.chat().data["proposal"]
        agent = Agent(self.conversation)
        original_book = agent.tools.book

        def book_after_budget(hold_id, idempotency_key):
            """Commit the booking and simulate the clock passing the run deadline."""
            result = original_book(hold_id, idempotency_key)
            agent.started = time.monotonic() - 100
            return result

        with patch.object(agent.tools, "book", side_effect=book_after_budget):
            result = agent.confirm(prepared["id"], prepared["fingerprint"])
        self.assertIn("unresolved", result["message"])
        self.assertEqual(MockBooking.objects.count(), 1)
        self.assertEqual(Proposal.objects.get(pk=prepared["id"]).status, "executing")
        recovered = Agent(self.conversation).confirm(prepared["id"], prepared["fingerprint"])
        self.assertEqual(recovered["state"], "completed")
        self.assertEqual(MockBooking.objects.count(), 1)

    def setUp(self):
        """Create an isolated conversation and a future India-date request for each scenario."""
        self.day = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.facts = {
            "service": "plumber",
            "area": "Indiranagar",
            "date_range": [self.day, self.day],
            "budget": 1500,
        }
        self.conversation = Conversation.objects.create()
        self.base = f"/api/v1/conversations/{self.conversation.id}/"

    def chat(self, facts=None):
        """Submit request facts as a JSON message through the conversation API."""
        return self.client.post(
            self.base + "messages/",
            {"message": json.dumps(self.facts if facts is None else facts)},
            format="json",
        )

    def confirm(self, proposal):
        """Explicitly confirm the supplied proposal through the conversation API."""
        return self.client.post(
            self.base + "confirm/",
            {
                "proposal_id": proposal["id"],
                "fingerprint": proposal["fingerprint"],
                "confirmed": True,
            },
            format="json",
        )

    def booking(self):
        """Prepare and explicitly confirm a mock booking, then return its booking ID."""
        proposal = self.chat().data["proposal"]
        return self.confirm(proposal).data["result"]["booking_id"]

    def test_search_never_books_without_confirmation(self):
        """Verify the expected invariant: search never books without confirmation."""
        response = self.chat()
        self.assertEqual(response.data["state"], "awaiting_approval")
        self.assertEqual(MockBooking.objects.count(), 0)
        self.assertEqual(len(response.data["providers"]), 3)

    def test_collects_only_missing_fields(self):
        """Verify the expected invariant: collects only missing fields."""
        response = self.chat({"service": "plumber", "budget": 1500})
        self.assertEqual(response.data["missing"], ["area", "date_range"])

    def test_partial_correction_preserves_context(self):
        """Verify the expected invariant: partial correction preserves context."""
        self.chat()
        self.chat({"area": "Koramangala"})
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.facts["service"], "plumber")
        self.assertEqual(self.conversation.facts["area"], "Koramangala")

    def test_false_confirmation_rejected(self):
        """Verify the expected invariant: false confirmation rejected."""
        proposal = self.chat().data["proposal"]
        response = self.client.post(
            self.base + "confirm/",
            {
                "proposal_id": proposal["id"],
                "fingerprint": proposal["fingerprint"],
                "confirmed": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MockBooking.objects.exists())

    def test_missing_confirmation_rejected(self):
        """Verify the expected invariant: missing confirmation rejected."""
        self.assertEqual(
            self.client.post(self.base + "confirm/", {}, format="json").status_code, 400
        )

    def test_exact_proposal_fingerprint_required(self):
        """Verify the expected invariant: exact proposal fingerprint required."""
        proposal = self.chat().data["proposal"]
        proposal["fingerprint"] = "0" * 64
        self.assertEqual(self.confirm(proposal).status_code, 400)
        self.assertFalse(MockBooking.objects.exists())

    def test_success_is_authoritatively_verified(self):
        """Verify the expected invariant: success is authoritatively verified."""
        proposal = self.chat().data["proposal"]
        self.assertEqual(self.confirm(proposal).data["state"], "completed")
        self.assertEqual(self.conversation.traces.filter(name="get_booking_status").count(), 2)

    def test_repeated_confirmation_is_idempotent(self):
        """Verify the expected invariant: repeated confirmation is idempotent."""
        proposal = self.chat().data["proposal"]
        first = self.confirm(proposal)
        second = self.confirm(proposal)
        self.assertEqual(first.data["result"], second.data["result"])
        self.assertEqual(MockBooking.objects.count(), 1)

    def test_correction_invalidates_prior_approval(self):
        """Verify the expected invariant: correction invalidates prior approval."""
        old = self.chat().data["proposal"]
        self.chat({"budget": 1600})
        self.assertEqual(self.confirm(old).status_code, 400)
        self.assertFalse(MockBooking.objects.exists())

    def test_changed_quote_requires_fresh_approval(self):
        """Verify the expected invariant: changed quote requires fresh approval."""
        proposal = self.chat().data["proposal"]
        MockHold.objects.filter(pk=proposal["details"]["hold_id"]).update(price=1200)
        self.assertEqual(self.confirm(proposal).data["state"], "needs_attention")
        self.assertFalse(MockBooking.objects.exists())

    def test_changed_slot_requires_fresh_approval(self):
        """Verify the expected invariant: changed slot requires fresh approval."""
        proposal = self.chat().data["proposal"]
        MockHold.objects.filter(pk=proposal["details"]["hold_id"]).update(
            slot=f"{self.day}T14:00:00+05:30"
        )
        self.confirm(proposal)
        self.assertFalse(MockBooking.objects.exists())

    def test_changed_provider_requires_fresh_approval(self):
        """Verify the expected invariant: changed provider requires fresh approval."""
        proposal = self.chat().data["proposal"]
        MockHold.objects.filter(pk=proposal["details"]["hold_id"]).update(provider_id="provider-2")
        self.confirm(proposal)
        self.assertFalse(MockBooking.objects.exists())

    def test_timeout_after_success_reconciles(self):
        """Verify the expected invariant: timeout after success reconciles."""
        self.conversation.scenario = "timeout_success"
        self.conversation.save()
        result = self.confirm(self.chat().data["proposal"])
        self.assertEqual(result.data["state"], "completed")
        self.assertEqual(MockBooking.objects.count(), 1)
        self.assertTrue(
            self.conversation.traces.filter(name="book", data__error="TimeoutError").exists()
        )

    def test_timeout_without_success_never_claims_booking(self):
        """Verify the expected invariant: timeout without success never claims booking."""
        self.conversation.scenario = "timeout_empty"
        self.conversation.save()
        result = self.confirm(self.chat().data["proposal"])
        self.assertEqual(result.data["state"], "needs_attention")
        self.assertEqual(result.data["result"]["status"], "retries_exhausted")
        self.assertFalse(MockBooking.objects.exists())

    def test_restart_after_provider_success_is_reconciled(self):
        """Verify the expected invariant: restart after provider success is reconciled."""
        proposal = self.chat().data["proposal"]
        Proposal.objects.filter(pk=proposal["id"]).update(status="executing")
        MockProviderClient().book(proposal["details"]["hold_id"], proposal["id"])
        # A new API request constructs a new Agent and reads only persisted state.
        result = self.confirm(proposal)
        self.assertEqual(result.data["state"], "completed")
        self.assertEqual(MockBooking.objects.count(), 1)

    def test_expired_proposal_is_not_executed(self):
        """Verify the expected invariant: expired proposal is not executed."""
        proposal = self.chat().data["proposal"]
        Proposal.objects.filter(pk=proposal["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertIn("expired", self.confirm(proposal).data["message"])
        self.assertFalse(MockBooking.objects.exists())

    def test_slot_taken_is_reported(self):
        """Verify the expected invariant: slot taken is reported."""
        self.conversation.scenario = "slot_taken"
        self.conversation.save()
        result = self.confirm(self.chat().data["proposal"])
        self.assertEqual(result.data["result"]["status"], "slot_taken")
        self.assertFalse(MockBooking.objects.exists())

    def test_review_prompt_injection_cannot_book(self):
        """Verify the expected invariant: review prompt injection cannot book."""
        self.chat()
        review = self.conversation.traces.get(name="get_provider").data["result"]["reviews"][1]
        self.assertIn("IGNORE", review)
        self.assertFalse(MockBooking.objects.exists())
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.facts["budget"], 1500)

    def test_model_cannot_grant_approval(self):
        """Verify the expected invariant: model cannot grant approval."""
        self.chat({**self.facts, "approved": True, "confirmed": True})
        self.assertFalse(MockBooking.objects.exists())

    def test_morning_window_excludes_afternoon(self):
        """Verify the expected invariant: morning window excludes afternoon."""
        response = self.chat({**self.facts, "earliest_time": "10:00", "latest_time": "12:00"})
        self.assertEqual(response.data["proposal"]["details"]["slot"][11:16], "11:00")

    def test_exact_earliest_time_boundary_is_inclusive(self):
        """Verify the expected invariant: exact earliest time boundary is inclusive."""
        response = self.chat({**self.facts, "earliest_time": "11:00", "latest_time": "12:00"})
        self.assertEqual(response.data["proposal"]["details"]["slot"][11:16], "11:00")

    def test_price_change_above_budget_is_not_proposed(self):
        """Verify the expected invariant: price change above budget is not proposed."""
        self.conversation.scenario = "price_change"
        self.conversation.save()
        response = self.chat({**self.facts, "budget": 1000})
        self.assertNotIn("proposal", response.data)
        self.assertFalse(MockBooking.objects.exists())

    def test_search_failure_has_user_facing_outcome(self):
        """Verify the expected invariant: search failure has user facing outcome."""
        self.conversation.scenario = "tool_failure"
        self.conversation.save()
        self.assertEqual(self.chat().data["state"], "error")

    @override_settings(AGENT_MAX_TOOL_CALLS=1)
    def test_tool_call_budget_is_enforced(self):
        """Verify the expected invariant: tool call budget is enforced."""
        self.assertEqual(self.chat().data["state"], "limited")
        self.assertEqual(self.conversation.traces.filter(kind="tool").count(), 1)

    @override_settings(AGENT_MAX_SECONDS=0)
    def test_wall_clock_budget_is_enforced(self):
        """Verify the expected invariant: wall clock budget is enforced."""
        self.assertEqual(self.chat().data["state"], "limited")

    @override_settings(AGENT_INPUT_TOKENS=100)
    def test_context_budget_is_enforced(self):
        """Verify the expected invariant: context budget is enforced."""
        self.assertEqual(self.chat().data["state"], "limited")
        self.assertFalse(self.conversation.traces.filter(kind="model").exists())

    def test_only_relevant_explicit_memories_reach_model(self):
        """Verify the expected invariant: only relevant explicit memories reach model."""
        Memory.objects.bulk_create(
            [Memory(user_id="local-user", key=f"irrelevant-{i}", value="old") for i in range(50)]
        )
        Memory.objects.create(user_id="local-user", key="area", value="Indiranagar")
        Memory.objects.create(
            user_id="local-user", key="preferred_provider", value="provider-3", explicit=False
        )
        self.chat({k: v for k, v in self.facts.items() if k != "area"})
        context = self.conversation.traces.get(kind="context").data["context"]
        self.assertEqual(context["preferences"], {"area": "Indiranagar"})

    def test_memory_correction_is_used(self):
        """Verify the expected invariant: memory correction is used."""
        url = "/api/v1/users/local-user/memories/"
        self.client.put(url, {"key": "area", "value": "Old area"}, format="json")
        self.client.put(url, {"key": "area", "value": "New area"}, format="json")
        response = self.chat({k: v for k, v in self.facts.items() if k != "area"})
        self.assertEqual(response.data["proposal"]["details"]["area"], "New area")

    def test_forgotten_memory_is_excluded(self):
        """Verify the expected invariant: forgotten memory is excluded."""
        Memory.objects.create(user_id="local-user", key="area", value="Forget me")
        self.client.delete("/api/v1/users/local-user/memories/area/")
        response = self.chat({k: v for k, v in self.facts.items() if k != "area"})
        self.assertIn("area", response.data["missing"])
        self.assertNotIn("Forget me", json.dumps(self.conversation.traces.get(kind="context").data))

    def test_cancel_requires_approval_and_retries_transient_503(self):
        """Verify the expected invariant: cancel requires approval and retries transient 503."""
        booking_id = self.booking()
        proposal = self.chat({"action": "cancel", "booking_id": booking_id}).data["proposal"]
        self.assertEqual(MockBooking.objects.get(pk=booking_id).status, "confirmed")
        self.assertEqual(self.confirm(proposal).data["result"]["status"], "cancelled")
        self.assertEqual(self.conversation.traces.filter(name="cancel").count(), 2)

    def test_reschedule_conflict_does_not_change_booking(self):
        """Verify the expected invariant: reschedule conflict does not change booking."""
        booking_id = self.booking()
        previous = MockBooking.objects.get(pk=booking_id).slot
        self.conversation.scenario = "slot_conflict"
        self.conversation.save()
        proposal = self.chat(
            {"action": "reschedule", "booking_id": booking_id, "slot": f"{self.day}T14:00:00+05:30"}
        ).data["proposal"]
        self.assertEqual(self.confirm(proposal).data["result"]["status"], "slot_conflict")
        self.assertEqual(MockBooking.objects.get(pk=booking_id).slot, previous)

    def test_reschedule_correction_preserves_action(self):
        """Verify the expected invariant: reschedule correction preserves action."""
        booking_id = self.booking()
        self.chat({"action": "reschedule", "booking_id": booking_id})
        response = self.chat({"slot": f"{self.day}T14:00:00+05:30"})
        self.assertEqual(response.data["proposal"]["action"], "reschedule")

    @override_settings(MOCK_CONFLICT_SLOTS=["14:00"])
    def test_reschedule_after_conflict_accepts_different_slot_with_fresh_approval(self):
        """Recover from a configured conflict by approving a new available appointment."""
        booking_id = self.booking()
        self.conversation.scenario = "slot_conflict"
        self.conversation.save()
        blocked = self.chat(
            {
                "action": "reschedule",
                "booking_id": booking_id,
                "slot": f"{self.day}T14:00:00+05:30",
            }
        ).data["proposal"]
        self.assertEqual(self.confirm(blocked).data["result"]["status"], "slot_conflict")
        available = f"{self.day}T11:00:00+05:30"
        replacement = self.chat({"slot": available}).data["proposal"]
        self.assertNotEqual(replacement["id"], blocked["id"])
        self.assertEqual(MockBooking.objects.get(pk=booking_id).slot[11:16], "09:00")
        self.assertEqual(self.confirm(replacement).data["state"], "completed")
        self.assertEqual(MockBooking.objects.get(pk=booking_id).slot, available)

    def test_other_conversation_cannot_approve_proposal(self):
        """Verify the expected invariant: other conversation cannot approve proposal."""
        proposal = self.chat().data["proposal"]
        self.conversation = Conversation.objects.create()
        self.base = f"/api/v1/conversations/{self.conversation.id}/"
        self.assertEqual(self.confirm(proposal).status_code, 400)

    def test_other_user_cannot_cancel_booking(self):
        """Verify the expected invariant: other user cannot cancel booking."""
        booking_id = self.booking()
        self.conversation = Conversation.objects.create(user_id="other-user")
        self.base = f"/api/v1/conversations/{self.conversation.id}/"
        self.assertNotIn("proposal", self.chat({"action": "cancel", "booking_id": booking_id}).data)

    def test_invalid_date_range_has_safe_outcome(self):
        """Verify the expected invariant: invalid date range has safe outcome."""
        response = self.chat({**self.facts, "date_range": [self.day, "2000-01-01"]})
        self.assertEqual(response.data["state"], "error")
        self.assertFalse(MockBooking.objects.exists())

    def test_unresolved_execution_blocks_new_request(self):
        """Verify the expected invariant: unresolved execution blocks new request."""
        proposal = self.chat().data["proposal"]
        Proposal.objects.filter(pk=proposal["id"]).update(status="executing")
        self.assertIn("unresolved", self.chat({"area": "Another area"}).data["message"])

    def test_health_and_browsable_api(self):
        """Verify the expected invariant: health and browsable api."""
        self.assertEqual(self.client.get("/api/v1/health/").data["model_backend"], "demo")
        self.assertEqual(
            self.client.get("/api/v1/conversations/", HTTP_ACCEPT="text/html").status_code, 200
        )

    def test_context_uses_india_date_at_utc_day_boundary(self):
        """Verify the expected invariant: context uses india date at utc day boundary."""
        frozen_time = datetime(2026, 11, 19, 20, 0, tzinfo=datetime_timezone.utc)
        with patch("django.utils.timezone.now", return_value=frozen_time):
            self.chat({"service": "plumber"})
        context = self.conversation.traces.get(kind="context").data["context"]
        self.assertEqual(context["current_date"], "2026-11-20")
        self.assertEqual(context["timezone"], "Asia/Kolkata")
        self.assertEqual(context["upcoming_dates"]["Saturday"], "2026-11-21")

    def test_api_timestamps_use_indian_standard_time(self):
        """Verify the expected invariant: api timestamps use indian standard time."""
        prepared = self.chat().data
        self.assertTrue(prepared["proposal"]["expires_at"].endswith("+05:30"))
        self.assertTrue(prepared["proposal"]["details"]["slot"].endswith("+05:30"))
        details = self.client.get(self.base).data
        self.assertTrue(details["messages"][0]["created_at"].endswith("+05:30"))
        traces = self.client.get(self.base + "traces/").data
        self.assertTrue(traces[0]["created_at"].endswith("+05:30"))

    @override_settings(LLM_API_KEY="test-only-key")
    @patch("integrations.litellm.client.litellm.completion")
    def test_live_plan_is_validated_before_facts_are_saved(self, post):
        """Normalize live model facts and discard approval claims before preparing any action."""
        raw_plan = {**self.facts, "budget": "1500", "approved": True}
        post.return_value = {
            "choices": [{"message": {"content": json.dumps(raw_plan)}}],
            "usage": {"total_tokens": 80},
        }
        result = Agent(self.conversation, model=LiteLLMService()).chat("Find a plumber")
        self.assertEqual(result["state"], "awaiting_approval")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.facts["budget"], 1500)
        self.assertNotIn("approved", self.conversation.facts)
        self.assertFalse(MockBooking.objects.exists())
        model_trace = self.conversation.traces.get(kind="model")
        self.assertEqual(model_trace.data["backend"], "live")
        self.assertEqual(model_trace.data["usage"], {"total_tokens": 80})

    def test_invalid_model_plan_cannot_reach_provider_tools(self):
        """Reject invalid facts from an injected model before persistence or provider calls."""
        model = Mock()
        model.plan.return_value = ({"budget": -1}, {})
        result = Agent(self.conversation, model=model).chat("Find a plumber")
        self.assertEqual(result["state"], "error")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.facts, {})
        self.assertFalse(self.conversation.traces.filter(kind="tool").exists())
        self.assertFalse(MockBooking.objects.exists())
