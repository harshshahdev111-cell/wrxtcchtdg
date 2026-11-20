"""Configured reschedule conflicts and deterministic saved provider outcomes."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from integrations.factory import get_provider_client

from chat_agent.models import MockBooking, MockOperation


class RescheduleSlotScenarios(TestCase):
    def setUp(self):
        """Create one real mock booking and two future appointment slots."""
        day = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.blocked = f"{day}T14:00:00+05:30"
        self.available = f"{day}T11:00:00+05:30"
        self.client = get_provider_client()
        hold = self.client.hold_slot("provider-1", f"{day}T09:00:00+05:30")
        result = self.client.book(hold["hold_id"], "initial-booking")
        self.booking = MockBooking.objects.get(pk=result["booking_id"])

    def test_only_configured_exact_slot_conflicts(self):
        """A configured conflict leaves the booking unchanged while a different slot succeeds."""
        client = get_provider_client(conflict_slots=[self.blocked])
        previous = self.booking.slot
        conflict = client.reschedule(str(self.booking.id), self.blocked, "blocked-attempt")
        self.assertEqual(conflict, {"status": "slot_conflict"})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.slot, previous)
        result = client.reschedule(str(self.booking.id), self.available, "available-attempt")
        self.assertEqual(result["status"], "confirmed")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.slot, self.available)

    def test_exact_conflict_matches_equivalent_timezone_but_not_next_day(self):
        """Compare instants consistently without blocking that time on unrelated dates."""
        client = get_provider_client(conflict_slots=[self.blocked])
        utc_slot = self.blocked.replace("T14:00:00+05:30", "T08:30:00+00:00")
        self.assertEqual(
            client.reschedule(str(self.booking.id), utc_slot, "same-instant")["status"],
            "slot_conflict",
        )
        next_day = (timezone.localdate() + timedelta(days=4)).isoformat()
        self.assertEqual(
            client.reschedule(str(self.booking.id), f"{next_day}T14:00:00+05:30", "next-day")[
                "status"
            ],
            "confirmed",
        )

    @override_settings(MOCK_CONFLICT_SLOTS=["14:00"])
    def test_scenario_uses_configured_local_times_and_allows_other_slots(self):
        """The UI conflict scenario blocks its configured time rather than every request."""
        client = get_provider_client(scenario="slot_conflict")
        self.assertEqual(
            client.reschedule(str(self.booking.id), self.blocked, "configured-time")["status"],
            "slot_conflict",
        )
        self.assertEqual(
            client.reschedule(str(self.booking.id), self.available, "other-time")["status"],
            "confirmed",
        )

    def test_retry_returns_saved_conflict_even_after_configuration_changes(self):
        """Keep a retry reproducible across a new client whose conflict settings differ."""
        first = get_provider_client(conflict_slots=[self.blocked]).reschedule(
            str(self.booking.id), self.blocked, "conflict-retry"
        )
        second = get_provider_client(conflict_slots=[]).reschedule(
            str(self.booking.id), self.blocked, "conflict-retry"
        )
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "slot_conflict")
        self.assertEqual(MockOperation.objects.count(), 1)
        self.booking.refresh_from_db()
        self.assertNotEqual(self.booking.slot, self.blocked)

    def test_retry_success_returns_saved_result_without_moving_booking_again(self):
        """A successful old key replays its result after a later approved slot change."""
        first = self.client.reschedule(str(self.booking.id), self.available, "successful-retry")
        self.client.reschedule(str(self.booking.id), self.blocked, "later-change")
        second = get_provider_client().reschedule(
            str(self.booking.id), self.available, "successful-retry"
        )
        self.assertEqual(first, second)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.slot, self.blocked)
