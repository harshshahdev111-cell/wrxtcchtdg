"""Offline test settings, retaining PostgreSQL for real transaction and lock checks."""

from .base import *  # noqa: F403

DEBUG = False
LLM_BACKEND = "demo"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {}
