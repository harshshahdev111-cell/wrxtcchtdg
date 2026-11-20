"""Errors raised by the agent's execution limits."""


class LimitExceeded(Exception):
    """A run exhausted its time, tool-call, or context budget."""


class ExecutionInProgress(Exception):
    """Another run owns the proposal execution claim."""


class BookingOutcomeUnknown(Exception):
    """Provider status does not establish a safe, final booking outcome."""


class BookingDetailsChanged(Exception):
    """The held provider, slot or price differs from the approved proposal."""
