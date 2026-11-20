"""Material approval changes and recovery for cancellation and rescheduling."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from integrations.exceptions import ProviderError
from integrations.providers import MockProviderClient

from chat_agent.models import Conversation, MockBooking, MockOperation, Proposal
from chat_agent.services.agent import Agent


@override_settings(LLM_BACKEND="demo")
class BookingChangeApprovalScenarios(TestCase):
    def make_proposal(self, action):
        """Create a confirmed booking and a separate unapproved booking-change proposal."""
        conversation = Conversation.objects.create()
        day = (timezone.localdate() + timedelta(days=3)).isoformat()
        prepared = Agent(conversation).chat(
            json.dumps(
                {
                    "service": "plumber",
                    "area": "Indiranagar",
                    "budget": 1500,
                    "date_range": [day, day],
                }
            )
        )["proposal"]
        booked = Agent(conversation).confirm(prepared["id"], prepared["fingerprint"])
        booking = MockBooking.objects.get(pk=booked["result"]["booking_id"])
        request = {"action": action, "booking_id": str(booking.id)}
        if action == "reschedule":
            request["slot"] = f"{day}T11:00:00+05:30"
        proposed = Agent(conversation).chat(json.dumps(request))["proposal"]
        return conversation, booking, Proposal.objects.get(pk=proposed["id"])

    def assert_changed_detail_blocks_both_actions(self, field):
        """Check each change type is invalidated before either provider method is called."""
        for action in ("cancel", "reschedule"):
            with self.subTest(action=action):
                conversation, booking, proposal = self.make_proposal(action)
                if field == "slot":
                    booking.slot = proposal.details["previous_slot"].replace("T09:", "T14:")
                    booking.save(update_fields=["slot"])
                else:
                    setattr(booking.hold, field, "provider-2" if field == "provider_id" else 999)
                    booking.hold.save(update_fields=[field])
                with patch.object(MockProviderClient, action) as tool:
                    result = Agent(conversation).confirm(proposal.id, proposal.fingerprint)
                tool.assert_not_called()
                proposal.refresh_from_db()
                self.assertEqual(proposal.status, "invalidated")
                self.assertEqual(result["state"], "needs_attention")
                self.assertIn("approve it again", result["message"])
                booking.refresh_from_db()
                self.assertEqual(booking.status, "confirmed")

    def test_changed_provider_invalidates_cancel_and_reschedule(self):
        """Neither kind of approval authorizes a change against a different provider."""
        self.assert_changed_detail_blocks_both_actions("provider_id")

    def test_changed_slot_invalidates_cancel_and_reschedule(self):
        """Neither kind of approval authorizes a change against a different existing slot."""
        self.assert_changed_detail_blocks_both_actions("slot")

    def test_changed_price_invalidates_cancel_and_reschedule(self):
        """Neither kind of approval authorizes a change against a different price."""
        self.assert_changed_detail_blocks_both_actions("price")

    def test_unchanged_details_succeed_and_completed_confirmations_do_not_repeat_tools(self):
        """Execute valid changes and return their saved results on repeated confirmation."""
        for action in ("cancel", "reschedule"):
            with self.subTest(action=action):
                conversation, booking, proposal = self.make_proposal(action)
                first = Agent(conversation).confirm(proposal.id, proposal.fingerprint)
                self.assertEqual(first["state"], "completed")
                with patch.object(MockProviderClient, action) as tool:
                    repeated = Agent(conversation).confirm(proposal.id, proposal.fingerprint)
                tool.assert_not_called()
                self.assertEqual(first["result"], repeated["result"])
                booking.refresh_from_db()
                self.assertEqual(booking.slot, proposal.details["slot"])

    def test_retry_without_saved_outcome_rechecks_changed_details(self):
        """An earlier approval cannot bypass validation after an uncertain unsuccessful attempt."""
        for action in ("cancel", "reschedule"):
            with self.subTest(action=action):
                conversation, booking, proposal = self.make_proposal(action)
                with patch.object(MockProviderClient, action, side_effect=TimeoutError()):
                    first = Agent(conversation).confirm(proposal.id, proposal.fingerprint)
                self.assertIn("unresolved", first["message"])
                booking.hold.provider_id = "provider-2"
                booking.hold.save(update_fields=["provider_id"])
                with patch.object(MockProviderClient, action) as tool:
                    Agent(conversation).confirm(proposal.id, proposal.fingerprint)
                tool.assert_not_called()
                proposal.refresh_from_db()
                self.assertEqual(proposal.status, "invalidated")

    def test_committed_change_timeout_reconciles_without_repeating_tool(self):
        """Saved provider results prove our own slot/status changes after a lost response."""
        for action in ("cancel", "reschedule"):
            with self.subTest(action=action):
                conversation, booking, proposal = self.make_proposal(action)
                agent = Agent(conversation)
                original = getattr(agent.tools, action)

                def commit_then_timeout(**arguments):
                    """Let the provider commit the approved operation before losing its response."""
                    if action == "cancel":
                        try:
                            original(**arguments)
                        except ProviderError:
                            pass
                    original(**arguments)
                    raise TimeoutError("Response lost after provider commit")

                with patch.object(agent.tools, action, side_effect=commit_then_timeout):
                    first = agent.confirm(proposal.id, proposal.fingerprint)
                self.assertIn("unresolved", first["message"])
                with patch.object(MockProviderClient, action) as tool:
                    recovered = Agent(conversation).confirm(proposal.id, proposal.fingerprint)
                tool.assert_not_called()
                self.assertEqual(recovered["state"], "completed")
                self.assertEqual(
                    MockOperation.objects.filter(key=f"{action}:{proposal.id}").count(), 1
                )

    def test_details_changed_during_transient_cancel_failure_block_retry(self):
        """Revalidate before the retry when the first 503 accompanies a material change."""
        conversation, booking, proposal = self.make_proposal("cancel")
        agent = Agent(conversation)

        def fail_and_change_provider(**arguments):
            """Change the provider while simulating the first cancellation's transient error."""
            booking.hold.provider_id = "provider-2"
            booking.hold.save(update_fields=["provider_id"])
            raise ProviderError("503 transient failure")

        with patch.object(agent.tools, "cancel", side_effect=fail_and_change_provider) as tool:
            result = agent.confirm(proposal.id, proposal.fingerprint)
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(result["state"], "needs_attention")
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "invalidated")

    def test_saved_unrelated_operation_result_does_not_claim_success(self):
        """Reject an operation ledger result whose provider differs from the approved action."""
        conversation, booking, proposal = self.make_proposal("reschedule")
        result = MockProviderClient().reschedule(
            str(booking.id), proposal.details["slot"], str(proposal.id)
        )
        result["provider_id"] = "provider-2"
        MockOperation.objects.filter(key=f"reschedule:{proposal.id}").update(result=result)
        with patch.object(MockProviderClient, "reschedule") as tool:
            response = Agent(conversation).confirm(proposal.id, proposal.fingerprint)
        tool.assert_not_called()
        self.assertIn("unresolved", response["message"])
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "executing")
