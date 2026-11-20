"""Fit current request facts into a small, deterministic model input."""

import json

from integrations.llm.tokens import fits_budget, token_usage

from chat_agent.exceptions import LimitExceeded


def bounded_context(context):
    """Drop optional saved preferences before rejecting an oversized current request.

    Request facts and the current message are never silently truncated. Preference
    removal affects only this prompt; the saved memories remain available to the
    booking workflow. Stable removal order keeps replayed inputs reproducible.
    """
    context = {**context, "preferences": dict(context["preferences"])}
    dropped = []
    while True:
        encoded = json.dumps(context, ensure_ascii=False)
        usage = token_usage(encoded)
        if fits_budget(usage):
            return encoded, {**usage, "dropped_preferences": dropped}
        if not context["preferences"]:
            raise LimitExceeded("Token budget reached; please send a shorter message")
        key = sorted(context["preferences"])[-1]
        del context["preferences"][key]
        dropped.append(key)
