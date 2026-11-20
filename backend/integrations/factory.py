"""Select configured vendors in one place; callers use their shared contracts."""

from django.conf import settings

from .exceptions import IntegrationConfigurationError
from .litellm import LiteLLMService
from .llm.demo import DemoModel
from .providers import MockProviderClient


def get_model(backend=None):
    """Create the configured model adapter, or select an explicit backend override.

    Unsupported backends raise IntegrationConfigurationError rather than silently
    falling back to a different model.
    """
    backend = settings.LLM_BACKEND if backend is None else backend
    if backend == "demo":
        return DemoModel()
    if backend == "live":
        return LiteLLMService()
    raise IntegrationConfigurationError("LLM_BACKEND must be demo or live")


def get_provider_client(*, scenario="success", seed=42, conflict_slots=None):
    # The assignment permits mock providers only.
    """Create the local mock provider with reproducible scenario and seed settings."""
    return MockProviderClient(scenario=scenario, seed=seed, conflict_slots=conflict_slots)
