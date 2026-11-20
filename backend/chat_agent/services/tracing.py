"""Sanitize trace data without changing the values used by the actual workflow."""

import json
import re
import time

from django.conf import settings

REDACTED = "[REDACTED]"
SECRET_FIELDS = {
    "apikey",
    "authorization",
    "proxyauthorization",
    "password",
    "passwd",
    "secret",
    "secretkey",
    "clientsecret",
    "token",
    "accesstoken",
    "refreshtoken",
    "cookie",
    "setcookie",
}
AUTH_VALUE = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
API_KEY_VALUE = re.compile(r"\b(?:gsk_|sk-)[A-Za-z0-9_-]{12,}")
SECRET_ASSIGNMENT = re.compile(
    r"\b(api[_-]?key|password|passwd|secret|client[_-]?secret|access[_-]?token|"
    r"refresh[_-]?token|cookie|sessionid)\b(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)


def sanitize_trace(data):
    """Copy nested data, redacting credential fields, recognizable credentials and configured secrets.

    Reviews and booking IDs remain useful evidence. This is credential redaction,
    not anonymization of all personal information. Original tool/model data is unchanged.
    """
    secrets = (
        settings.LLM_API_KEY,
        settings.SECRET_KEY,
        settings.DATABASES["default"].get("PASSWORD", ""),
    )
    return sanitize_value(data, tuple(secret for secret in secrets if secret))


def sanitize_value(value, secrets):
    """Recursively sanitize JSON-compatible values and strings containing JSON."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if is_secret_field(key) else sanitize_value(item, secrets))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    # Keep normal context strings byte-for-byte intact; rewrite embedded JSON only if redacted.
    if value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            pass
        else:
            cleaned = sanitize_value(decoded, secrets)
            if cleaned != decoded:
                return json.dumps(cleaned, ensure_ascii=False)
    for secret in secrets:
        if value == secret:
            return REDACTED
        if len(secret) >= 8:
            value = value.replace(secret, REDACTED)
    value = AUTH_VALUE.sub(REDACTED, value)
    value = API_KEY_VALUE.sub(REDACTED, value)
    return SECRET_ASSIGNMENT.sub(r"\1\2" + REDACTED, value)


def is_secret_field(key):
    """Recognize normal credential fields and prefixed names such as LLM_API_KEY."""
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in SECRET_FIELDS or normalized.endswith(
        ("apikey", "password", "secret", "secretkey", "accesstoken", "refreshtoken")
    )


def finish_trace(event, data, started):
    """Finish an existing call event, preserving its identity and sanitized latency/result."""
    event.data = sanitize_trace(data)
    event.latency_ms = max(0, (time.perf_counter() - started) * 1000)
    event.save(update_fields=["data", "latency_ms"])
