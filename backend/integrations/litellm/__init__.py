"""LiteLLM SDK integration with bundled metadata and no optional background fetches."""

import os

os.environ.setdefault("LITELLM_OFFLINE_MODE", "true")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402

litellm.suppress_debug_info = True
litellm.DEFAULT_MAX_RETRIES = 0

from .client import LiteLLMClient  # noqa: E402
from .service import LiteLLMService  # noqa: E402

__all__ = ["LiteLLMClient", "LiteLLMService"]
