import pytest

from log_safety import redact_sensitive_text


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("?api_key=test-api-secret-123", "?api_key=[REDACTED]"),
        ("&API_KEY=test-api-secret-123", "&API_KEY=[REDACTED]"),
        (
            "Authorization: Bearer test-bearer-secret",
            "Authorization: Bearer [REDACTED]",
        ),
        (
            '{"client_secret":"test-client-secret"}',
            '{"client_secret":"[REDACTED]"}',
        ),
        (
            "{'access_token': 'test-access-token'}",
            "{'access_token': '[REDACTED]'}",
        ),
        (
            '{"refresh_token":"test-refresh-token"}',
            '{"refresh_token":"[REDACTED]"}',
        ),
        ("?code=test-oauth-code", "?code=[REDACTED]"),
    ],
)
def test_redact_sensitive_text_removes_supported_credentials(source, expected):
    assert redact_sensitive_text(source) == expected


def test_redact_sensitive_text_preserves_non_sensitive_context():
    source = "https://example.invalid/tickets/123 status=429 attempts=3"

    assert redact_sensitive_text(source) == source


def test_redact_sensitive_text_accepts_non_string_values():
    assert redact_sensitive_text(None) == ""
    assert redact_sensitive_text(429) == "429"
