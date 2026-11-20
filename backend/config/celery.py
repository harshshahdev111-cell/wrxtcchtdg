"""Configure the shared background worker and its fixed periodic schedule."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
app = Celery("chat_agent")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
