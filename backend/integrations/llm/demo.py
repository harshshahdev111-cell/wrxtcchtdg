"""Scripted JSON responses for offline demos and reproducible scenarios."""

import json


class DemoModel:
    """Read a scripted plan from the message instead of calling a real model."""

    backend = "demo"

    def plan(self, context, timeout):
        """Decode the scripted JSON plan; the agent validates it before using any facts."""
        message = json.loads(context)["message"]
        return json.loads(message), {}

    async def aplan(self, context, timeout):
        """Return the same offline plan without network or database work."""
        return self.plan(context, timeout)
