"""Provide a reproducible provider event for the background follow-up demonstration."""

from django.core.management.base import BaseCommand, CommandError

from chat_agent.models import MockBooking
from chat_agent.services.follow_up import simulate_provider_cancellation


class Command(BaseCommand):
    help = "Simulate a provider cancellation; Beat will detect it without a chat prompt."

    def add_arguments(self, parser):
        """Accept the ID of a confirmed mock booking."""
        parser.add_argument("booking_id")

    def handle(self, *args, **options):
        """Update only the provider ledger; do not run the follow-up inline."""
        try:
            booking = simulate_provider_cancellation(options["booking_id"])
        except (MockBooking.DoesNotExist, ValueError) as exc:
            raise CommandError("Supply an existing, confirmed booking ID.") from exc
        self.stdout.write(
            f"Provider cancelled booking {booking.pk}; waiting for the background check."
        )
