"""Real PostgreSQL locking and separate-process booking recovery checks."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from integrations.providers import MockProviderClient

from chat_agent.models import Conversation, MockBooking, Proposal
from chat_agent.services.agent import Agent


@override_settings(LLM_BACKEND="demo")
class PostgreSQLBookingScenarios(TransactionTestCase):
    def setUp(self):
        """Commit fixtures that other database connections and subprocesses can observe."""
        self.assertEqual(connection.vendor, "postgresql")
        self.conversation = Conversation.objects.create(user_id="postgres-recovery-user")
        day = (timezone.localdate() + timedelta(days=3)).isoformat()
        result = Agent(self.conversation).chat(
            json.dumps(
                {
                    "service": "plumber",
                    "area": "Indiranagar",
                    "date_range": [day, day],
                    "budget": 1500,
                }
            )
        )
        self.proposal = Proposal.objects.get(pk=result["proposal"]["id"])

    def test_overlapping_confirmations_have_one_execution_owner(self):
        """A second connection observes the active claim and never calls the booking tool."""
        entered = Event()
        release = Event()
        original_book = MockProviderClient.book

        def slow_booking(client, hold_id, idempotency_key):
            """Pause the first request after its claim has committed."""
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test did not release the first booking")
            return original_book(client, hold_id, idempotency_key)

        def confirm_in_worker():
            """Use an independent PostgreSQL connection for the first confirmation."""
            close_old_connections()
            try:
                conversation = Conversation.objects.get(pk=self.conversation.pk)
                return Agent(conversation).confirm(self.proposal.id, self.proposal.fingerprint)
            finally:
                connections.close_all()

        with patch.object(
            MockProviderClient, "book", autospec=True, side_effect=slow_booking
        ) as book:
            with ThreadPoolExecutor(max_workers=1) as executor:
                first = executor.submit(confirm_in_worker)
                try:
                    self.assertTrue(entered.wait(timeout=3))
                    second = Agent(self.conversation).confirm(
                        self.proposal.id, self.proposal.fingerprint
                    )
                    self.assertIn("being processed", second["message"])
                    self.assertEqual(book.call_count, 1)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5)["state"], "completed")
        self.assertEqual(MockBooking.objects.count(), 1)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.booking_attempts, 1)

    def test_concurrent_provider_retries_return_the_same_booking(self):
        """The provider's hold-row lock and unique ledger serialize duplicate writes."""
        barrier = Barrier(2)

        def book_in_worker():
            """Enter the provider concurrently using a dedicated database connection."""
            close_old_connections()
            try:
                barrier.wait(timeout=3)
                return MockProviderClient().book(
                    self.proposal.details["hold_id"], str(self.proposal.id)
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: book_in_worker(), range(2)))
        self.assertEqual(results[0]["booking_id"], results[1]["booking_id"])
        self.assertEqual(MockBooking.objects.count(), 1)

    def test_abrupt_process_exit_after_provider_commit_recovers_one_booking(self):
        """A new process can reconcile a committed booking after the original process dies."""
        database = connection.settings_dict
        environment = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.test",
            "POSTGRES_DB": str(database["NAME"]),
            "POSTGRES_USER": str(database["USER"]),
            "POSTGRES_PASSWORD": str(database["PASSWORD"]),
            "POSTGRES_HOST": str(database["HOST"]),
            "POSTGRES_PORT": str(database["PORT"]),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        code = """
import django, os, sys, uuid
django.setup()
from chat_agent.models import Conversation, Proposal
from chat_agent.services.approvals import claim_execution
from chat_agent.services.booking import BookingRecovery
from integrations.providers import MockProviderClient
conversation = Conversation.objects.get(pk=sys.argv[1])
proposal = Proposal.objects.get(pk=sys.argv[2])
owner = uuid.uuid4()
proposal, _ = claim_execution(conversation, proposal.id, proposal.fingerprint, owner)
client = MockProviderClient()
def tool(name, **arguments):
    "Call the local provider inside the process that will exit abruptly."
    return getattr(client, name)(**arguments)
BookingRecovery(proposal, owner, tool).execute()
os._exit(17)
"""
        process = subprocess.run(
            [sys.executable, "-c", code, str(self.conversation.id), str(self.proposal.id)],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            process.returncode, 17, process.stderr.replace(str(database["PASSWORD"]), "[redacted]")
        )
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, "executing")
        self.assertEqual(self.proposal.booking_attempts, 1)
        self.assertEqual(MockBooking.objects.get().user_id, "postgres-recovery-user")
        # Advance the saved execution lease after the crashed process has gone away.
        after_lease = self.proposal.execution_expires_at + timedelta(seconds=1)
        with patch("django.utils.timezone.now", return_value=after_lease):
            with patch.object(
                MockProviderClient, "book", side_effect=AssertionError("Must not book again")
            ):
                result = Agent(Conversation.objects.get(pk=self.conversation.pk)).confirm(
                    self.proposal.id,
                    self.proposal.fingerprint,
                )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(MockBooking.objects.count(), 1)
        self.proposal.refresh_from_db()
        self.assertIsNone(self.proposal.execution_token)
