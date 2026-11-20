"""Bounded booking retries with durable attempts and authoritative reconciliation."""

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from integrations.exceptions import ProviderError

from chat_agent.exceptions import BookingDetailsChanged, BookingOutcomeUnknown, ExecutionInProgress
from chat_agent.models import MockHold, Proposal


class BookingRecovery:
    def __init__(self, proposal, owner, tool):
        """Use the approved proposal and the agent's traced, bounded tool caller."""
        self.proposal = proposal
        self.owner = owner
        self.tool = tool
        self.key = str(proposal.id)

    def execute(self):
        """Reconcile before writing, and reuse the same hold and key on every retry.

        Only an authoritative not_found permits another book call. Status errors
        and unknown states leave the proposal unresolved for a later retry.
        """
        status = self._status()
        if status["status"] != "not_found":
            return status
        self._check_hold()
        while self._reserve_attempt():
            try:
                response = self.tool(
                    "book", hold_id=self.proposal.details["hold_id"], idempotency_key=self.key
                )
            except (TimeoutError, ProviderError):
                response = {"status": "unknown"}
            status = self._status()
            if status["status"] != "not_found":
                return status
            if response.get("status") in {"slot_taken", "hold_expired"}:
                return response
            self._check_hold()
        return {"status": "retries_exhausted"}

    def _status(self):
        """Accept only known provider states and verify confirmed booking identity."""
        status = self.tool("get_booking_status", idempotency_key=self.key)
        value = status.get("status")
        if value == "not_found":
            return status
        if value not in {"confirmed", "cancelled"}:
            raise BookingOutcomeUnknown("The provider has not resolved this booking")
        expected = self.proposal.details
        for field in ("hold_id", "provider_id", "slot", "price"):
            if status.get(field) != expected[field]:
                raise BookingOutcomeUnknown("The returned booking differs from the approved action")
        if not status.get("booking_id") or status.get("idempotency_key") != self.key:
            raise BookingOutcomeUnknown("The provider returned an unrelated booking")
        return status

    def _check_hold(self):
        """Reject any provider, time, or price change before another booking attempt."""
        hold = MockHold.objects.get(pk=self.proposal.details["hold_id"])
        details = self.proposal.details
        if (
            hold.provider_id != details["provider_id"]
            or hold.slot != details["slot"]
            or hold.price != details["price"]
        ):
            raise BookingDetailsChanged("The held details changed; fresh approval is required")

    def _reserve_attempt(self):
        """Persist the next attempt before calling a side-effecting provider tool."""
        with transaction.atomic():
            proposal = Proposal.objects.select_for_update().get(pk=self.proposal.pk)
            if (
                proposal.execution_token != self.owner
                or proposal.execution_expires_at <= timezone.now()
            ):
                raise ExecutionInProgress("This run no longer owns the action")
            if proposal.booking_attempts >= settings.BOOKING_MAX_ATTEMPTS:
                return False
            proposal.booking_attempts += 1
            proposal.save(update_fields=["booking_attempts"])
            return True
