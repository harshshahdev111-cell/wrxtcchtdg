"""Run ASGI workers; timeout watches worker heartbeats, not individual async requests."""

import os

from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

bind = "0.0.0.0:8000"
worker_class = "uvicorn_worker.UvicornWorker"
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
threads = 1
timeout = settings.GUNICORN_TIMEOUT
accesslog = "-"
errorlog = "-"

if timeout <= settings.AGENT_MAX_SECONDS:
    raise ValueError("GUNICORN_TIMEOUT must be greater than AGENT_MAX_SECONDS")
