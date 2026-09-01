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
_SENSITIVE_MAPPING_KEYS = set(_SENSITIVE_KEYS) | {"x_api_key"}
_KEY_PATTERN = "|".join(_SENSITIVE_KEYS)
_REDACTION_PATTERNS = (
    (
        re.compile(
            rf"(?P<prefix>[?&](?:{_KEY_PATTERN})=)"
            r"(?P<value>[^&\s\"'<>}}]+)",
            re.IGNORECASE,
        ),
        r"\g<prefix>[REDACTED]",
    ),
    (
        re.compile(
            r"(?P<prefix>(?:X-API-Key|API-Key|API_KEY)[\"']?\s*[:=]\s*[\"']?)"
            r"(?P<value>[^&\s\"'<>},]+)",
            re.IGNORECASE,
        ),
        r"\g<prefix>[REDACTED]",
    ),
    (
        re.compile(
            r"(?P<prefix>Authorization[\"']?\s*[:=]\s*[\"']?\s*)"
            r"(?P<scheme>[A-Za-z][A-Za-z0-9_-]*\s+)?"
            r"(?P<value>[^\s\"'<>},]+)",
            re.IGNORECASE,
        ),
        lambda match: (
            f"{match.group('prefix')}"
            f"{match.group('scheme') or ''}"
            "[REDACTED]"
        ),
    ),
    (
        re.compile(
            rf"(?P<prefix>[\"']?(?:{_KEY_PATTERN})[\"']?\s*[:=]\s*[\"']?)"
            r"(?P<value>[^&\s\"'<>},]+)",
            re.IGNORECASE,
        ),
        r"\g<prefix>[REDACTED]",
    ),
)


def redact_sensitive_text(value: object) -> str:
    """Return text with recognized credentials replaced by ``[REDACTED]``."""
    text = str(value or "")
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _sanitize_log_value(value):
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        sanitized = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _SENSITIVE_MAPPING_KEYS:
                sanitized[key] = "[REDACTED]"
            elif normalized_key == "authorization":
                if isinstance(item, str) and " " in item.strip():
                    scheme = item.strip().split(None, 1)[0]
                    sanitized[key] = f"{scheme} [REDACTED]"
                else:
                    sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize_log_value(item)
        return sanitized
    if isinstance(value, tuple):
        return tuple(_sanitize_log_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_log_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_sensitive_text(value)


class CredentialRedactionFilter(logging.Filter):
    """Sanitize log messages and exception chains before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _sanitize_log_value(record.msg)
        record.args = _sanitize_log_value(record.args)
        try:
            formatted_message = record.getMessage()
        except Exception:
            formatted_message = f"{record.msg} args={record.args}"
        record.msg = redact_sensitive_text(formatted_message)
        record.args = ()

        if record.exc_info:
            rendered = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact_sensitive_text(rendered)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_sensitive_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_sensitive_text(record.stack_info)
        return True


def install_credential_filter(*handlers: logging.Handler) -> None:
    """Attach a credential-redaction filter to each supplied handler."""
    for handler in handlers:
        handler.addFilter(CredentialRedactionFilter())
