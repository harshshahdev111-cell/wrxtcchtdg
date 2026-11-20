"""Detect mock provider cancellations and propose replacements without booking them."""

import logging

from django.db import transaction
from django.utils import timezone

from chat_agent.models import CancellationFollowUp, Conversation, MockBooking, Proposal
from chat_agent.selectors import current_preferences
from chat_agent.services.agent import Agent

logger = logging.getLogger(__name__)


def simulate_provider_cancellation(booking_id):
    """Change the mock provider ledger, leaving notification to the periodic checker."""
    with transaction.atomic():
        booking = MockBooking.objects.select_for_update().get(pk=booking_id)
        if booking.status == "cancelled" and booking.cancellation_source == "provider":
            return booking
        if booking.status != "confirmed":
            raise ValueError("Only a confirmed booking can be cancelled by its provider.")
        booking.status = "cancelled"
        booking.cancellation_source = "provider"
        booking.save(update_fields=["status", "cancellation_source"])
        return booking


def process_provider_cancellations(batch_size=50):
    """Discover provider changes and retry pending work; isolate failures per booking."""
    bookings = MockBooking.objects.filter(status="cancelled", cancellation_source="provider")
    bookings = bookings.exclude(cancellationfollowup__status__in=["proposed", "no_replacement"])
    processed = 0
    for booking in bookings.order_by("pk")[:batch_size]:
        try:
            # The original book key is the approved proposal ID, including after a restart.
            source = Proposal.objects.filter(pk=booking.idempotency_key, action="book").first()
            if source is None:
                continue
            follow_up, _ = CancellationFollowUp.objects.get_or_create(
                booking=booking, defaults={"conversation_id": source.conversation_id}
            )
            processed += process_follow_up(follow_up.pk)
        except Exception:
            # A failed transaction is retried on the next tick; no partial notification remains.
            logger.exception("Provider cancellation follow-up failed for booking %s", booking.pk)
    return processed


@transaction.atomic
def process_follow_up(follow_up_id):
    """Atomically save one notification and proposal using only local mock provider tools.

    PostgreSQL serializes repeated checks. Holds, traces, messages and the proposal
    share this transaction, so a crash rolls back partial work. This deliberately
    contains no model call or external network request.
    """
    follow_up = CancellationFollowUp.objects.select_for_update().get(pk=follow_up_id)
    if follow_up.status != "pending":
        return 0
    conversation = Conversation.objects.select_for_update().get(pk=follow_up.conversation_id)
    booking = MockBooking.objects.select_related("hold").get(pk=follow_up.booking_id)
    agent = Agent(conversation)
    if follow_up.notified_at is None:
        agent.trace("event", "provider_cancelled", {"booking_id": str(booking.pk)})
        agent.reply(
            f"Your provider cancelled booking {booking.pk}. "
            "I will look for another provider. A replacement needs your fresh approval."
        )
        follow_up.notified_at = timezone.now()
        follow_up.save(update_fields=["notified_at"])
    # Never replace another pending request or disturb an uncertain approved operation.
    conversation.proposals.filter(status="pending", expires_at__lte=timezone.now()).update(
        status="expired"
    )
    if conversation.proposals.filter(status__in=["pending", "executing"]).exists():
        return 0
    snapshot = booking.hold.request_details
    if not snapshot:
        agent.transition("needs_attention")
        agent.reply(
            "The original request was not saved. Please send your requirements for a replacement."
        )
        follow_up.status = "no_replacement"
    else:
        facts = {
            **{
                key: value
                for key, value in snapshot["effective"].items()
                if key in {"service", "area", "date_range", "budget"}
            },
            **current_preferences(conversation.user_id),
            **snapshot["facts"],
            "action": "book",
        }
        facts.pop("booking_id", None)
        facts.pop("slot", None)
        result = agent._prepare_booking(facts, excluded_providers=[booking.hold.provider_id])
        if "proposal" in result:
            conversation.facts = facts
            conversation.save(update_fields=["facts"])
            follow_up.proposal_id = result["proposal"]["id"]
            follow_up.status = "proposed"
        else:
            agent.transition("needs_attention")
            agent.reply("No replacement is ready. Please update your requirements to continue.")
            follow_up.status = "no_replacement"
    follow_up.save(update_fields=["status", "proposal"])
    return 1
