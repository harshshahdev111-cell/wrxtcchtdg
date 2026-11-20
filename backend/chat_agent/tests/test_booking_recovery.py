"""Booking safety scenarios using the real PostgreSQL-backed mock ledger."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from integrations.exceptions import ProviderError
from integrations.providers import MockProviderClient

from chat_agent.models import Conversation, MockBooking, MockHold, Proposal
from chat_agent.services.agent import Agent


@override_settings(LLM_BACKEND="demo", BOOKING_MAX_ATTEMPTS=2)
class BookingRecoveryScenarios(TestCase):
    def setUp(self):
        """Prepare one unapproved proposal for an isolated user and future slot."""
        self.conversation = Conversation.objects.create(user_id="recovery-user")
        day = (timezone.localdate() + timedelta(days=3)).isoformat()
        prepared = Agent(self.conversation).chat(
            json.dumps(
                {
                    "service": "plumber",
                    "area": "Indiranagar",
                    "budget": 1500,
                    "date_range": [day, day],
                }
            )
        )
        self.proposal = Proposal.objects.get(pk=prepared["proposal"]["id"])

    def confirm(self):
        """Start a fresh agent run to confirm the exact saved proposal."""
        conversation = Conversation.objects.get(pk=self.conversation.pk)
        return Agent(conversation).confirm(self.proposal.id, self.proposal.fingerprint)

    def test_timeout_without_effect_retries_the_same_key_and_hold(self):
        """Retry only after authoritative not_found, preserving the original request identity."""
        original_book = MockProviderClient.book
        requests = []

        def transient_timeout(client, hold_id, idempotency_key):
            """Lose the first response before booking, then use the real provider implementation."""
            requests.append((hold_id, idempotency_key))
            if len(requests) == 1:
                raise TimeoutError("No side effect on the first attempt")
            return original_book(client, hold_id, idempotency_key)

        with patch.object(MockProviderClient, "book", autospec=True, side_effect=transient_timeout):
            result = self.confirm()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(requests, [(self.proposal.details["hold_id"], str(self.proposal.id))] * 2)
        self.assertEqual(MockBooking.objects.count(), 1)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.booking_attempts, 2)
        names = list(
            self.conversation.traces.filter(kind="tool")
            .order_by("id")
            .values_list("name", flat=True)
        )
        self.assertEqual(
            names[-5:],
            ["get_booking_status", "book", "get_booking_status", "book", "get_booking_status"],
        )

    def test_status_outage_after_success_stays_unresolved_then_recovers(self):
        """Keep an uncertain proposal executable and recover without another booking call."""
        original_status = MockProviderClient.get_booking_status
        checks = []

        def unavailable_status(client, **arguments):
            """Allow the initial empty lookup, then simulate a source-of-truth outage."""
            checks.append(arguments)
            if len(checks) == 2:
                raise TimeoutError("Status unavailable")
            return original_status(client, **arguments)

        with patch.object(
            MockProviderClient, "get_booking_status", autospec=True, side_effect=unavailable_status
        ):
            result = self.confirm()
        self.assertEqual(result["state"], "needs_attention")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "executing")
        self.assertIsNotNone(self.proposal.approved_at)
        self.assertIsNone(self.proposal.execution_token)
        booking = MockBooking.objects.get()
        self.assertEqual(booking.user_id, "recovery-user")
        self.assertEqual(booking.service, "plumber")
        with patch.object(
            MockProviderClient, "book", side_effect=AssertionError("Must reconcile first")
        ):
            result = self.confirm()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["booking_id"], str(booking.id))
        self.assertEqual(MockBooking.objects.count(), 1)

    def test_unknown_status_never_triggers_a_booking(self):
        """An in-progress provider status is not permission to retry the side effect."""
        with patch.object(
            MockProviderClient, "get_booking_status", return_value={"status": "processing"}
        ):
            result = self.confirm()
        self.assertEqual(result["state"], "needs_attention")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "executing")
        self.assertEqual(self.proposal.booking_attempts, 0)
        self.assertFalse(MockBooking.objects.exists())

    def test_unrelated_provider_result_is_not_reported_as_success(self):
        """Reject a confirmed status carrying a different booking identity."""
        unrelated = {
            **self.proposal.details,
            "status": "confirmed",
            "booking_id": "unrelated",
            "idempotency_key": "another-operation",
        }
        with patch.object(MockProviderClient, "get_booking_status", return_value=unrelated):
            result = self.confirm()
        self.assertEqual(result["state"], "needs_attention")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "executing")
        self.assertFalse(MockBooking.objects.exists())

    def test_retry_budget_survives_a_new_agent_instance(self):
        """Persist consumed attempts so restarting cannot reset the total retry budget."""
        Proposal.objects.filter(pk=self.proposal.pk).update(status="executing", booking_attempts=1)
        self.conversation.scenario = "timeout_empty"
        self.conversation.save()
        result = self.confirm()
        self.assertEqual(result["result"]["status"], "retries_exhausted")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.booking_attempts, 2)
        self.assertEqual(self.conversation.traces.filter(name="book").count(), 1)
        self.assertFalse(MockBooking.objects.exists())

    def test_status_failure_at_retry_limit_does_not_claim_failure_or_success(self):
        """Preserve ambiguity if the last allowed attempt cannot be reconciled."""
        Proposal.objects.filter(pk=self.proposal.pk).update(status="executing", booking_attempts=2)
        with patch.object(
            MockProviderClient, "get_booking_status", side_effect=TimeoutError("Offline")
        ):
            result = self.confirm()
        self.assertNotIn("result", result)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "executing")
        self.assertEqual(self.proposal.booking_attempts, 2)

    def test_changed_quote_between_attempts_requires_fresh_approval(self):
        """Stop recovery when a held material detail changes after the first timeout."""

        def change_price(client, hold_id, idempotency_key):
            """Change the provider quote while returning a timeout without a booking."""
            MockHold.objects.filter(pk=hold_id).update(price=1200)
            raise TimeoutError("Quote changed")

        with patch.object(MockProviderClient, "book", autospec=True, side_effect=change_price):
            result = self.confirm()
        self.assertEqual(result["state"], "needs_attention")
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "invalidated")
        self.assertEqual(self.proposal.booking_attempts, 1)
        self.assertFalse(MockBooking.objects.exists())

    def test_expired_hold_can_still_be_reconciled_after_success(self):
        """Verify an existing booking before considering expired hold or proposal times."""
        client = MockProviderClient()
        booked = client.book(self.proposal.details["hold_id"], str(self.proposal.id))
        past = timezone.now() - timedelta(minutes=1)
        Proposal.objects.filter(pk=self.proposal.pk).update(status="executing", expires_at=past)
        MockHold.objects.filter(pk=self.proposal.details["hold_id"]).update(expires_at=past)
        with patch.object(MockProviderClient, "book", side_effect=AssertionError("Already booked")):
            result = self.confirm()
        self.assertEqual(result["result"]["booking_id"], booked["booking_id"])
        self.assertEqual(result["state"], "completed")

    def test_provider_rejects_the_same_hold_with_a_different_key(self):
        """Do not reinterpret one booked hold as a different operation."""
        client = MockProviderClient()
        client.book(self.proposal.details["hold_id"], str(self.proposal.id))
        with self.assertRaisesMessage(ProviderError, "already booked with another key"):
            client.book(self.proposal.details["hold_id"], "different-key")
        self.assertEqual(MockBooking.objects.count(), 1)

    def test_provider_rejects_the_same_key_with_a_different_hold(self):
        """Bind an idempotency key to its original hold across repeated client instances."""
        client = MockProviderClient()
        client.book(self.proposal.details["hold_id"], str(self.proposal.id))
        other = client.hold_slot("provider-2", self.proposal.details["slot"])
        with self.assertRaisesMessage(ProviderError, "reused for another hold"):
            MockProviderClient().book(other["hold_id"], str(self.proposal.id))
        self.assertEqual(MockBooking.objects.count(), 1)
