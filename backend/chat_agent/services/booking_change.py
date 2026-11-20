"""Validate approved booking changes and reconcile their saved local provider outcomes."""

from integrations.exceptions import ProviderError

from chat_agent.exceptions import BookingDetailsChanged, BookingOutcomeUnknown
from chat_agent.models import MockBooking, MockOperation


def execute_booking_change(proposal, user_id, tool):
    """Check saved outcomes first and revalidate material details before every write.

    The mock operation ledger is authoritative for a previously committed change.
    Replaying that result avoids mistaking our own reschedule for an external slot
    change after a timeout. Without a saved result, retries need the same material
    details as the original approval. Only the configured transient 503 is retried.
    """
    details = proposal.details
    key = str(proposal.id)
    arguments = {"booking_id": details["booking_id"], "idempotency_key": key}
    if proposal.action == "reschedule":
        arguments["slot"] = details["slot"]
    for attempt in range(2):
        operation = MockOperation.objects.filter(key=f"{proposal.action}:{key}").first()
        if operation and operation.result:
            return verify_change_result(proposal, operation.result)
        validate_current_booking(details, user_id)
        try:
            result = tool(proposal.action, **arguments)
        except ProviderError as exc:
            if attempt == 0 and "503" in str(exc):
                continue
            raise
        return verify_change_result(proposal, result)


def validate_current_booking(details, user_id):
    """Require current ownership, active status and the provider/slot/price that were approved."""
    booking = (
        MockBooking.objects.select_related("hold")
        .filter(pk=details["booking_id"], user_id=user_id)
        .first()
    )
    if booking is None:
        raise ProviderError("Booking not owned by user")
    if (
        booking.status != "confirmed"
        or booking.hold.provider_id != details["provider_id"]
        or booking.slot != details["previous_slot"]
        or booking.hold.price != details["price"]
    ):
        raise BookingDetailsChanged("The booking differs from the approved details")


def verify_change_result(proposal, result):
    """Accept known failures or an outcome matching the exact approved booking change."""
    if result.get("status") in {"slot_conflict", "not_active"}:
        return result
    expected = {
        "status": "cancelled" if proposal.action == "cancel" else "confirmed",
        "booking_id": proposal.details["booking_id"],
        "provider_id": proposal.details["provider_id"],
        "price": proposal.details["price"],
        "slot": proposal.details["slot"],
    }
    if any(result.get(field) != value for field, value in expected.items()):
        raise BookingOutcomeUnknown("The operation result differs from the approved action")
    return result
