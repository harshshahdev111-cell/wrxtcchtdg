"""Build and count the same text messages that the HTTP client sends."""

from functools import lru_cache

import tiktoken
from django.conf import settings

from integrations.exceptions import IntegrationConfigurationError

from .prompts import SYSTEM_PROMPT


def model_messages(context):
    """Return the complete application prompt without historical chat or tool reviews."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]


@lru_cache(maxsize=8)
def model_encoding(model):
    """Load the model's real tokenizer, rejecting unknown models rather than guessing."""
    model = model.removeprefix("groq/")
    if model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
        return tiktoken.get_encoding("o200k_harmony")
    model = model.removeprefix("openai/")
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError as exc:
        raise IntegrationConfigurationError("No tokenizer configured for this model") from exc


def token_usage(context, model=None):
    """Count all message text exactly and reserve a separate server-framing allowance.

    The hosted API controls its chat template. Its framing is not observable before
    a request, so the allowance is explicit rather than called an exact API count.
    Special-token-looking user text is counted as ordinary untrusted text.
    """
    model = settings.LLM_MODEL if model is None else model
    encoding = model_encoding(model)
    messages = model_messages(context)
    text_tokens = sum(
        len(encoding.encode_ordinary(message["role"]))
        + len(encoding.encode_ordinary(message["content"]))
        for message in messages
    )
    return {
        "model": model,
        "tokenizer": encoding.name,
        "messages": messages,
        "text_tokens": text_tokens,
        "framing_allowance": settings.LLM_FRAMING_TOKENS,
        "input_tokens": text_tokens + settings.LLM_FRAMING_TOKENS,
        "input_limit": settings.AGENT_INPUT_TOKENS,
        "output_reserved": settings.LLM_MAX_COMPLETION_TOKENS,
        "context_limit": settings.LLM_CONTEXT_TOKENS,
    }


def fits_budget(usage):
    """Check both the application input cap and space for the complete response."""
    return (
        usage["framing_allowance"] >= 0
        and usage["output_reserved"] > 0
        and usage["input_tokens"] <= usage["input_limit"]
        and usage["input_tokens"] + usage["output_reserved"] <= usage["context_limit"]
    )
