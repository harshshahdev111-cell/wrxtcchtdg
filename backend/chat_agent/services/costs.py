"""Use LiteLLM's bundled model prices and reported usage for best-effort USD estimates."""

import math
from urllib.parse import urlparse

from integrations.litellm import litellm


def estimate_cost(model, usage, base_url=""):
    """Delegate pricing arithmetic to LiteLLM; unknown usage/prices must remain unknown.

    Provider-qualified model names select the SDK price entry. An override pointing
    at an unverified host is not priced as if it were the official provider. No local
    token estimate is substituted for missing usage, and SDK lookup failures never
    break the booking workflow.
    """
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    details = usage.get("prompt_tokens_details", usage.get("input_tokens_details")) or {}
    if not isinstance(details, dict):
        return None
    cached_tokens = details.get("cached_tokens", 0)
    if any(
        type(value) is not int or value < 0
        for value in (input_tokens, output_tokens, cached_tokens)
    ):
        return None
    if cached_tokens > input_tokens:
        return None
    hosts = {"groq": "api.groq.com", "openai": "api.openai.com"}
    if base_url and urlparse(base_url).hostname != hosts.get(model.split("/", 1)[0]):
        return None
    normalized_usage = {
        **usage,
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "prompt_tokens_details": {**details, "cached_tokens": cached_tokens},
    }
    try:
        amount = litellm.completion_cost(
            completion_response={"model": model, "usage": normalized_usage}, model=model
        )
    except Exception:
        return None
    if amount is None or not math.isfinite(amount) or amount < 0:
        return None
    return {
        "amount": f"{amount:.12f}".rstrip("0").rstrip(".") or "0",
        "currency": "USD",
        "source": "litellm",
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
    }
