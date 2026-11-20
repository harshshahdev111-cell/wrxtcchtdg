"""Delegate model/provider HTTP routing to the LiteLLM SDK."""

import asyncio

import httpx
import litellm
from django.conf import settings

from integrations.exceptions import IntegrationConfigurationError
from integrations.llm.tokens import fits_budget, token_usage

from .http import shared_groq_handler


class LiteLLMClient:
    def __init__(self, *, model=None, api_key=None, base_url=None):
        """Load the provider-qualified model and credentials, allowing explicit overrides."""
        self.model = settings.LLM_MODEL if model is None else model
        self.api_key = settings.LLM_API_KEY if api_key is None else api_key
        self.base_url = (settings.LLM_BASE_URL if base_url is None else base_url).rstrip("/")

    def request_arguments(self, context, timeout):
        """Keep application budgets and explicit single-attempt behaviour around the SDK."""
        if not self.api_key:
            raise IntegrationConfigurationError(
                "LLM_API_KEY is missing. Configure .env for live mode."
            )
        if timeout <= 0:
            raise TimeoutError("Model time budget exhausted before request")
        usage = token_usage(context, self.model)
        if not fits_budget(usage):
            raise IntegrationConfigurationError("Model input exceeds the token budget")
        arguments = {
            "model": self.model,
            "api_key": self.api_key,
            "messages": usage["messages"],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": settings.LLM_MAX_COMPLETION_TOKENS,
            "num_retries": 0,
            "max_retries": 0,
            "timeout": httpx.Timeout(
                timeout,
                connect=min(settings.LLM_CONNECT_TIMEOUT, timeout),
                read=min(settings.LLM_READ_TIMEOUT, timeout),
                pool=min(settings.LLM_POOL_TIMEOUT, timeout),
            ),
        }
        if self.base_url:
            arguments["api_base"] = self.base_url
        return arguments

    def create_completion(self, context, timeout):
        """Use the synchronous SDK entry point for commands and offline domain scenarios."""
        return litellm.completion(**self.request_arguments(context, timeout))

    async def acreate_completion(self, context, timeout):
        """Use native async SDK I/O within the remaining cancellable model deadline."""
        arguments = self.request_arguments(context, timeout)
        if self.model.startswith("groq/") and shared_groq_handler() is not None:
            arguments["client"] = shared_groq_handler()
        async with asyncio.timeout(timeout):
            return await litellm.acompletion(**arguments)
