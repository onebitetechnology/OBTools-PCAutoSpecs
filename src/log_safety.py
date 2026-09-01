"""Credential-safe text and logging helpers."""

import logging
import re
import traceback
from typing import Mapping


_SENSITIVE_KEYS = (
    "api_key",
    "client_secret",
    "refresh_token",
    "access_token",
    "code",
)
_KEY_PATTERN = "|".join(_SENSITIVE_KEYS)
_REDACTION_PATTERNS = (
    re.compile(
        rf"(?P<prefix>[?&](?:{_KEY_PATTERN})=)"
        r"(?P<value>[^&\s\"'<>}}]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<prefix>Authorization[\"']?\s*[:=]\s*[\"']?\s*Bearer\s+)"
        r"(?P<value>[A-Za-z0-9._\-~+/=]+)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<prefix>[\"']?(?:{_KEY_PATTERN})[\"']?\s*[:=]\s*[\"']?)"
        r"(?P<value>[^&\s\"'<>},]+)",
        re.IGNORECASE,
    ),
)


def redact_sensitive_text(value: object) -> str:
    """Return text with recognized credentials replaced by ``[REDACTED]``."""
    text = str(value or "")
    for pattern in _REDACTION_PATTERNS:
        text = pattern.sub(r"\g<prefix>[REDACTED]", text)
    return text


def _sanitize_log_value(value):
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in _SENSITIVE_KEYS:
                sanitized[key] = "[REDACTED]"
            elif normalized_key == "authorization" and isinstance(item, str):
                sanitized[key] = redact_sensitive_text(
                    f"Authorization: {item}"
                ).split(": ", 1)[-1]
            else:
                sanitized[key] = _sanitize_log_value(item)
        return sanitized
    if isinstance(value, tuple):
        return tuple(_sanitize_log_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_log_value(item) for item in value]
    return value


class CredentialRedactionFilter(logging.Filter):
    """Sanitize log messages and exception chains before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _sanitize_log_value(record.msg)
        record.args = _sanitize_log_value(record.args)

        if record.exc_info:
            rendered = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact_sensitive_text(rendered)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_sensitive_text(record.exc_text)
        return True


def install_credential_filter(*handlers: logging.Handler) -> None:
    """Attach a credential-redaction filter to each supplied handler."""
    for handler in handlers:
        handler.addFilter(CredentialRedactionFilter())
