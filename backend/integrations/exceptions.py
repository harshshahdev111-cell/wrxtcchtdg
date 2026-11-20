"""Application-specific errors raised by integration adapters."""


class IntegrationConfigurationError(ValueError):
    """An integration has missing or unsupported configuration."""


class ProviderError(Exception):
    """A provider tool could not complete its request."""


class ModelResponseError(ValueError):
    """A model response could not be decoded; retain usage without retaining raw response text."""

    def __init__(self, usage=None):
        """Keep provider-reported token counts available to failed-call tracing."""
        super().__init__("Invalid model response")
        self.usage = usage
