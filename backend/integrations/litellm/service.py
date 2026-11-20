"""Application-facing model planning; no HTTP or database operations."""

import json

from integrations.exceptions import ModelResponseError

from .client import LiteLLMClient


class LiteLLMService:
    """Decode SDK-normalized responses before the agent validates the proposed facts."""

    backend = "live"

    def __init__(self, client=None):
        """Use the supplied vendor client, or create the default OpenAI-compatible client."""
        self.client = client if client is not None else LiteLLMClient()

    def plan(self, context, timeout):
        """Decode a vendor completion and preserve usage; leave fact validation to the agent."""
        response = self.client.create_completion(context, timeout)
        return self.parse_plan(response)

    async def aplan(self, context, timeout):
        """Await the live model without holding a synchronous HTTP worker during network I/O."""
        response = await self.client.acreate_completion(context, timeout)
        return self.parse_plan(response)

    @staticmethod
    def parse_plan(response):
        """Decode the JSON plan and return it with token usage for the agent to validate."""
        if hasattr(response, "model_dump"):
            response = response.model_dump()
        try:
            content = response["choices"][0]["message"]["content"]
            return json.loads(content), response.get("usage", {})
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            usage = response.get("usage") if isinstance(response, dict) else None
            raise ModelResponseError(usage) from exc
