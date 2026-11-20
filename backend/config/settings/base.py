"""Shared Django settings for development, Docker and offline tests."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR.parent / ".env", override=False)
SECRET_KEY = os.getenv("SECRET_KEY", "local-only-chat-agent-development")
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "adrf",
    "chat_agent",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "chat_agent"),
        "USER": os.getenv("POSTGRES_USER", "chat_agent"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "chat-agent-local-only"),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5440"),
        "OPTIONS": {"connect_timeout": 5},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
# Interpret appointments and display user-facing timestamps in Indian Standard Time.
TIME_ZONE = "Asia/Kolkata"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "common.api.exception_handler",
}
LLM_BACKEND = os.getenv("LLM_BACKEND", "demo")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "groq/openai/gpt-oss-20b")
LLM_MAX_COMPLETION_TOKENS = int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "1024"))
LLM_CONNECT_TIMEOUT = float(os.getenv("LLM_CONNECT_TIMEOUT", "5"))
LLM_READ_TIMEOUT = float(os.getenv("LLM_READ_TIMEOUT", "20"))
LLM_MAX_CONNECTIONS = int(os.getenv("LLM_MAX_CONNECTIONS", "50"))
LLM_POOL_TIMEOUT = float(os.getenv("LLM_POOL_TIMEOUT", "1"))
AGENT_MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "12"))
AGENT_MAX_SECONDS = float(os.getenv("AGENT_MAX_SECONDS", "25"))
GUNICORN_TIMEOUT = int(os.getenv("GUNICORN_TIMEOUT", "30"))
AGENT_INPUT_TOKENS = int(os.getenv("AGENT_INPUT_TOKENS", "2048"))
LLM_CONTEXT_TOKENS = int(os.getenv("LLM_CONTEXT_TOKENS", "8192"))
LLM_FRAMING_TOKENS = int(os.getenv("LLM_FRAMING_TOKENS", "128"))
BOOKING_MAX_ATTEMPTS = int(os.getenv("BOOKING_MAX_ATTEMPTS", "2"))
# In the slot_conflict scenario, block these India-local times or exact ISO datetimes.
MOCK_CONFLICT_SLOTS = [
    slot.strip() for slot in os.getenv("MOCK_CONFLICT_SLOTS", "14:00").split(",") if slot.strip()
]

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULE = {
    "check-provider-cancellations": {
        "task": "chat_agent.tasks.check_provider_cancellations",
        "schedule": 10.0,
        "options": {"expires": 10},
    }
}
