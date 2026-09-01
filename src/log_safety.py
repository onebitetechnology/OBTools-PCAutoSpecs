"""Credential-safe text and logging helpers."""

import re


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
