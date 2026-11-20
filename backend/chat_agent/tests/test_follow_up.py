"""Offline scenarios for proactive cancellation detection and fresh approval."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from chat_agent.models import CancellationFollowUp, Conversation, Memory, MockBooking, Proposal
from chat_agent.services.agent import Agent
from chat_agent.services.follow_up import (
    process_provider_cancellations,
    simulate_provider_cancellation,
)
from chat_agent.tasks import check_provider_cancellations


def create_booking():
    """Create a real approved offline booking with a saved original request."""
    day = (timezone.localdate() + timedelta(days=3)).isoformat()
    facts = {
        "service": "plumber",
        "area": "Indiranagar",
        "date_range": [day, day],
        "budget": 1500,
    }
    conversation = Conversation.objects.create()
    proposal = Agent(conversation).chat(json.dumps(facts))["proposal"]
    result = Agent(conversation).confirm(proposal["id"], proposal["fingerprint"])
    return conversation, MockBooking.objects.get(pk=result["result"]["booking_id"])


@override_settings(LLM_BACKEND="demo")
class ProactiveScenarios(TestCase):
    def setUp(self):
        """Create one completed booking before each simulated provider cancellation."""
        self.conversation, self.booking = create_booking()

    def cancel_provider(self):
        """Simulate the provider event without sending any new chat message."""
        simulate_provider_cancellation(self.booking.pk)

    def test_proactive_cancellation_proposes_without_user_prompt_or_booking(self):
        """A periodic check notifies and proposes an eligible different provider, without booking."""
        user_messages = self.conversation.messages.filter(role="user").count()
        self.cancel_provider()
        self.assertFalse(CancellationFollowUp.objects.exists())
        self.assertEqual(check_provider_cancellations.run(), 1)
        follow_up = CancellationFollowUp.objects.get()
        proposal = follow_up.proposal
        self.assertEqual(follow_up.status, "proposed")
        self.assertIsNotNone(follow_up.notified_at)
        self.assertNotEqual(proposal.details["provider_id"], self.booking.hold.provider_id)
        self.assertLessEqual(proposal.details["price"], 1500)
        self.assertEqual(proposal.details["area"], "Indiranagar")
        self.assertEqual(proposal.status, "pending")
        self.assertIsNone(proposal.approved_at)
        self.assertEqual(MockBooking.objects.count(), 1)
        self.assertEqual(self.conversation.messages.filter(role="user").count(), user_messages)
        self.assertTrue(self.conversation.traces.filter(name="provider_cancelled").exists())

    def test_repeated_checks_and_simulation_do_not_duplicate_follow_up(self):
        """Repeated provider events and scheduler ticks create one notice and one proposal."""
        self.cancel_provider()
        process_provider_cancellations()
        messages = self.conversation.messages.count()
        for _ in range(3):
            self.cancel_provider()
            self.assertEqual(process_provider_cancellations(), 0)
        self.assertEqual(CancellationFollowUp.objects.count(), 1)
        self.assertEqual(Proposal.objects.count(), 2)
        self.assertEqual(self.conversation.messages.count(), messages)

    def test_replacement_requires_new_confirmation_and_retries_are_idempotent(self):
        """Old approval cannot rebook; fresh replacement approval creates exactly one booking."""
        original = Proposal.objects.get()
        self.cancel_provider()
        process_provider_cancellations()
        Agent(self.conversation).confirm(original.pk, original.fingerprint)
        self.assertEqual(MockBooking.objects.count(), 1)
        replacement = CancellationFollowUp.objects.get().proposal
        result = Agent(self.conversation).confirm(replacement.pk, replacement.fingerprint)
        self.assertEqual(result["result"]["status"], "confirmed")
        Agent(self.conversation).confirm(replacement.pk, replacement.fingerprint)
        self.assertEqual(MockBooking.objects.count(), 2)

    def test_no_replacement_keeps_budget_and_notifies_once(self):
        """Insufficient budget produces a clear saved outcome without relaxing constraints."""
        self.booking.hold.request_details["facts"]["budget"] = 1000
        self.booking.hold.save(update_fields=["request_details"])
        self.cancel_provider()
        process_provider_cancellations()
        follow_up = CancellationFollowUp.objects.get()
        self.assertEqual(follow_up.status, "no_replacement")
        self.assertIsNone(follow_up.proposal)
        self.assertEqual(Proposal.objects.count(), 1)
        self.assertIn("No replacement", self.conversation.messages.last().content)
        count = self.conversation.messages.count()
        process_provider_cancellations()
        self.assertEqual(self.conversation.messages.count(), count)

    def test_original_request_survives_later_conversation_changes(self):
        """Replacement uses the saved booking request rather than mutable conversation facts."""
        self.conversation.facts = {"service": "electrician", "budget": 99999}
        self.conversation.save(update_fields=["facts"])
        self.cancel_provider()
        process_provider_cancellations()
        details = CancellationFollowUp.objects.get().proposal.details
        self.assertEqual(details["service"], "plumber")
        self.assertLessEqual(details["price"], 1500)

    def test_current_explicit_preferences_are_used(self):
        """A saved explicit time preference guides replacement selection after cancellation."""
        Memory.objects.create(user_id=self.conversation.user_id, key="earliest_time", value="14:00")
        Memory.objects.create(
            user_id=self.conversation.user_id, key="preferred_provider", value="provider-3"
        )
        self.cancel_provider()
        process_provider_cancellations()
        details = CancellationFollowUp.objects.get().proposal.details
        self.assertEqual(details["provider_id"], "provider-3")
        self.assertEqual(details["slot"][11:16], "14:00")

    def test_explicit_request_beats_memory_and_inferred_memory_is_ignored(self):
        """Original explicit constraints override conflicting memory; inferred preference is excluded."""
        snapshot = self.booking.hold.request_details
        snapshot["facts"]["latest_time"] = "11:00"
        self.booking.hold.save(update_fields=["request_details"])
        Memory.objects.create(user_id=self.conversation.user_id, key="area", value="Elsewhere")
        Memory.objects.create(
            user_id=self.conversation.user_id, key="earliest_time", value="14:00", explicit=False
        )
        self.cancel_provider()
        process_provider_cancellations()
        details = CancellationFollowUp.objects.get().proposal.details
        self.assertEqual(details["area"], "Indiranagar")
        self.assertLessEqual(details["slot"][11:16], "11:00")

    def test_user_cancellation_does_not_trigger_proactive_rebooking(self):
        """A user-requested cancellation must never start the provider-cancellation flow."""
        proposal = Agent(self.conversation).chat(
            json.dumps({"action": "cancel", "booking_id": str(self.booking.pk)})
        )["proposal"]
        Agent(self.conversation).confirm(proposal["id"], proposal["fingerprint"])
        self.assertEqual(process_provider_cancellations(), 0)
        self.assertFalse(CancellationFollowUp.objects.exists())

    def test_pending_request_defers_replacement_but_not_notification(self):
        """Notify once and wait while another proposal needs approval, then resume safely."""
        pending = Agent(self.conversation).chat(json.dumps({"budget": 1400}))["proposal"]
        self.cancel_provider()
        self.assertEqual(process_provider_cancellations(), 0)
        follow_up = CancellationFollowUp.objects.get()
        self.assertEqual(follow_up.status, "pending")
        self.assertIsNotNone(follow_up.notified_at)
        messages = self.conversation.messages.count()
        process_provider_cancellations()
        self.assertEqual(self.conversation.messages.count(), messages)
        Proposal.objects.filter(pk=pending["id"]).update(status="invalidated")
        self.assertEqual(process_provider_cancellations(), 1)

    def test_failure_rolls_back_partial_work_and_next_check_recovers(self):
        """A crash after preparing a proposal leaves no partial messages, holds or duplicate proposal."""
        self.cancel_provider()
        before = self.conversation.messages.count()
        original_save = CancellationFollowUp.save

        def save_or_crash(instance, *args, **kwargs):
            """Crash after proposal creation, just before marking the follow-up complete."""
            if instance.status == "proposed":
                raise RuntimeError("crash")
            return original_save(instance, *args, **kwargs)

        with patch("chat_agent.services.follow_up.CancellationFollowUp.save", new=save_or_crash):
            with self.assertLogs("chat_agent.services.follow_up", level="ERROR"):
                self.assertEqual(process_provider_cancellations(), 0)
        self.assertEqual(self.conversation.messages.count(), before)
        self.assertEqual(Proposal.objects.count(), 1)
        self.assertEqual(process_provider_cancellations(), 1)
        self.assertEqual(Proposal.objects.count(), 2)

    def test_forgotten_preference_is_not_revived_from_original_snapshot(self):
        """Old effective preferences must not survive a user's explicit forget operation."""
        snapshot = self.booking.hold.request_details
        snapshot["effective"].update(preferred_provider="provider-3", earliest_time="14:00")
        self.booking.hold.save(update_fields=["request_details"])
        self.cancel_provider()
        process_provider_cancellations()
        details = CancellationFollowUp.objects.get().proposal.details
        self.assertEqual(details["provider_id"], "provider-2")
        self.assertEqual(details["slot"][11:16], "09:00")

    def test_expired_pending_proposal_does_not_block_replacement_forever(self):
        """An expired unapproved proposal can be closed before preparing the replacement."""
        pending = Agent(self.conversation).chat(json.dumps({"budget": 1400}))["proposal"]
        Proposal.objects.filter(pk=pending["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.cancel_provider()
        self.assertEqual(process_provider_cancellations(), 1)
        self.assertEqual(Proposal.objects.get(pk=pending["id"]).status, "expired")

    def test_old_booking_without_snapshot_requests_requirements(self):
        """Legacy bookings notify clearly and ask for requirements instead of guessing them."""
        self.booking.hold.request_details = {}
        self.booking.hold.save(update_fields=["request_details"])
        self.cancel_provider()
        process_provider_cancellations()
        self.assertEqual(CancellationFollowUp.objects.get().status, "no_replacement")
        self.assertIn("original request", self.conversation.messages.last().content)


@override_settings(LLM_BACKEND="demo")
class ProactiveConcurrencyScenarios(TransactionTestCase):
    def test_overlapping_checks_create_one_notice_and_one_replacement(self):
        """Two independent worker connections serialize follow-up creation in PostgreSQL."""
        conversation, booking = create_booking()
        simulate_provider_cancellation(booking.pk)

        def check():
            """Run one scheduler tick using an independent database connection."""
            close_old_connections()
            try:
                return process_provider_cancellations()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: check(), range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(CancellationFollowUp.objects.count(), 1)
        self.assertEqual(Proposal.objects.count(), 2)
        self.assertEqual(conversation.traces.filter(name="provider_cancelled").count(), 1)
