"""Exercise concurrent model waits and Gunicorn ASGI heartbeat supervision in Linux."""

import asyncio
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

import httpx
import requests
from django.db import connection
from django.test import TransactionTestCase, override_settings

from chat_agent.models import Conversation, MockBooking


@skipUnless(
    sys.platform != "win32" and importlib.util.find_spec("gunicorn"),
    "Real Gunicorn termination is verified inside Linux Docker",
)
@override_settings(LLM_BACKEND="demo")
class WorkerTimeoutScenarios(TransactionTestCase):
    def setUp(self):
        """Start an isolated Gunicorn using the production config and committed test database."""
        database = connection.settings_dict
        environment = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.test",
            "POSTGRES_DB": str(database["NAME"]),
            "POSTGRES_USER": str(database["USER"]),
            "POSTGRES_PASSWORD": str(database["PASSWORD"]),
            "POSTGRES_HOST": str(database["HOST"]),
            "POSTGRES_PORT": str(database["PORT"]),
            "AGENT_MAX_SECONDS": "1",
            "GUNICORN_TIMEOUT": "5",
            "GUNICORN_WORKERS": "1",
        }
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/api/v1/"
        self.log = tempfile.TemporaryFile()
        self.addCleanup(self.log.close)
        self.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "--config",
                "config/gunicorn.py",
                "--bind",
                f"127.0.0.1:{port}",
                "chat_agent.tests.worker_timeout_app:application",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.addCleanup(self.stop_server)
        self.wait_for_health()
        self.conversation = Conversation.objects.create()

    def stop_server(self):
        """Reap the isolated server even when an assertion fails during a blocked request."""
        self.server.terminate()
        try:
            self.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(timeout=5)

    def wait_for_health(self):
        """Wait briefly for the first worker or its replacement to accept requests."""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if requests.get(self.url + "health/", timeout=0.5).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.05)
        self.fail("Isolated Gunicorn worker did not become healthy")

    def blocked_post(self, path, payload):
        """Require the server to drop/fail a blocked request before the client's own timeout."""
        started = time.monotonic()
        try:
            response = requests.post(self.url + path, json=payload, timeout=10)
        except requests.Timeout:
            self.fail("Client timed out before Gunicorn stopped the stuck worker")
        except requests.ConnectionError:
            pass
        else:
            self.assertGreaterEqual(response.status_code, 500)
        self.assertLess(time.monotonic() - started, 8)
        self.wait_for_health()
        self.log.seek(0)
        self.assertIn(b"WORKER TIMEOUT", self.log.read())

    def test_gunicorn_terminates_blocked_event_loop_and_serves_next_request(self):
        """Heartbeat supervision replaces a worker whose model blocks the event loop."""
        self.blocked_post(
            f"conversations/{self.conversation.pk}/messages/", {"message": "block-event-loop"}
        )
        self.assertFalse(MockBooking.objects.exists())
        self.assertTrue(self.conversation.messages.filter(role="user").exists())
        self.assertEqual(self.conversation.traces.get(kind="model").data["status"], "started")

    def test_async_deadline_returns_json_and_keeps_worker_healthy(self):
        """A cooperative slow AI call ends at the deadline with a clear reply, not a disconnect."""
        response = requests.post(
            self.url + f"conversations/{self.conversation.pk}/messages/",
            json={"message": "never-finish"},
            timeout=6,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "limited")
        self.assertIn("timed out", response.json()["message"])
        self.wait_for_health()
        self.assertFalse(MockBooking.objects.exists())
        self.assertEqual(self.conversation.traces.get(kind="model").data["error"], "TimeoutError")

    def test_one_asgi_worker_handles_six_simultaneous_model_waits(self):
        """All six requests must reach their model wait before any is allowed to finish."""
        conversations = [Conversation.objects.create() for _ in range(6)]

        async def request_all():
            """Send real concurrent HTTP requests to one production-configured ASGI worker."""
            async with httpx.AsyncClient(timeout=6) as client:
                return await asyncio.gather(
                    *[
                        client.post(
                            self.url + f"conversations/{conversation.pk}/messages/",
                            json={"message": "wait-for-peers"},
                        )
                        for conversation in conversations
                    ]
                )

        responses = asyncio.run(request_all())
        self.assertEqual([response.status_code for response in responses], [200] * 6)
        self.assertEqual([response.json()["state"] for response in responses], ["collecting"] * 6)
        self.assertFalse(MockBooking.objects.exists())
