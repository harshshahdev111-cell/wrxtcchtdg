import json
import uuid
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from integrations.factory import get_model

from chat_agent.models import Conversation
from chat_agent.services.agent import Agent


class Command(BaseCommand):
    help = "Run a local timeout-after-success scenario and export its real trace."

    def add_arguments(self, parser):
        """Add the command option that controls where the generated demo trace is saved."""
        parser.add_argument("--output", default="docs/timeout-after-success.json")

    def handle(self, *args, **options):
        """Run a simulated approved booking with a timeout and export its verified trace."""
        conversation = Conversation.objects.create(
            user_id=f"demo-{uuid.uuid4().hex[:8]}", scenario="timeout_success"
        )
        day = (timezone.localdate() + timedelta(days=3)).isoformat()
        agent = Agent(conversation, model=get_model(backend="demo"))
        prepared = agent.chat(
            json.dumps(
                {
                    "service": "plumber",
                    "area": "Indiranagar",
                    "budget": 1500,
                    "date_range": [day, day],
                    "earliest_time": "10:00",
                    "latest_time": "12:00",
                }
            )
        )
        if "proposal" not in prepared:
            raise CommandError(prepared["message"])
        proposal = prepared["proposal"]
        # This CLI is a simulated user: explicitly confirms the displayed proposal.
        completed = Agent(conversation).confirm(proposal["id"], proposal["fingerprint"])
        output = Path(options["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": "offline-demo",
            "prepared": prepared,
            "completed": completed,
            "events": list(
                conversation.traces.order_by("id").values(
                    "run_id", "kind", "name", "data", "latency_ms", "created_at"
                )
            ),
        }
        output.write_text(json.dumps(payload, cls=DjangoJSONEncoder, indent=2), encoding="utf-8")
        if completed["state"] != "completed":
            raise CommandError(f"Demo did not complete; inspect {output}")
        self.stdout.write(self.style.SUCCESS(f"Verified booking after timeout. Trace: {output}"))
