"""Load the configured Celery app so Django uses its task settings too."""

from .celery import app as celery_app

__all__ = ("celery_app",)
