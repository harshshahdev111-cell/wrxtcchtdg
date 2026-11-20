import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    """Use DRF error responses and provide a safe fallback for unexpected API failures."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response
    logger.exception("Unexpected API failure", exc_info=exc)
    return Response(
        {"detail": "Unexpected error. Check the conversation trace before retrying."}, status=500
    )
