import io
import logging

import pytest
import requests

from log_safety import install_credential_filter, redact_sensitive_text


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


def _capturing_logger(name):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    install_credential_filter(handler)

    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, handler, stream


def test_logging_filter_redacts_secret_embedded_in_message():
    logger, _, stream = _capturing_logger("tests.log_safety.message")

    logger.error("failed https://example.invalid?api_key=test-message-secret")

    assert "test-message-secret" not in stream.getvalue()
    assert "api_key=[REDACTED]" in stream.getvalue()


def test_logging_filter_redacts_tuple_arguments_without_changing_numbers():
    logger, _, stream = _capturing_logger("tests.log_safety.tuple")

    logger.error(
        "failed %s status=%d",
        "https://example.invalid?api_key=test-tuple-secret",
        429,
    )

    output = stream.getvalue()
    assert "test-tuple-secret" not in output
    assert "api_key=[REDACTED]" in output
    assert "status=429" in output


def test_logging_filter_redacts_nested_mapping_arguments():
    logger, _, stream = _capturing_logger("tests.log_safety.mapping")

    logger.error(
        "payload=%(payload)s",
        {"payload": {"access_token": "test-mapping-secret", "attempts": 3}},
    )

    output = stream.getvalue()
    assert "test-mapping-secret" not in output
    assert "[REDACTED]" in output
    assert "'attempts': 3" in output


def test_logging_filter_redacts_complete_chained_traceback():
    logger, _, stream = _capturing_logger("tests.log_safety.traceback")

    try:
        try:
            raise requests.exceptions.HTTPError(
                "429 for https://example.invalid?api_key=test-inner-secret"
            )
        except requests.exceptions.HTTPError as inner:
            raise RuntimeError("outer upload failure") from inner
    except RuntimeError:
        logger.exception("upload failed")

    output = stream.getvalue()
    assert "test-inner-secret" not in output
    assert "api_key=[REDACTED]" in output
    assert "HTTPError" in output
    assert "RuntimeError: outer upload failure" in output


def test_logging_filter_redacts_cached_exception_text():
    logger, handler, stream = _capturing_logger("tests.log_safety.cached")
    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "cached failure",
        (),
        None,
    )
    record.exc_text = "access_token=test-cached-secret"

    handler.handle(record)

    output = stream.getvalue()
    assert "test-cached-secret" not in output
    assert "access_token=[REDACTED]" in output


def test_setup_logging_installs_redaction_on_file_handler(tmp_path, monkeypatch):
    import AutoSpecUploaderGUI

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    monkeypatch.setattr(AutoSpecUploaderGUI, "get_app_dir", lambda: str(tmp_path))

    try:
        log_file = AutoSpecUploaderGUI.setup_logging()
        logging.error(
            "failed https://example.invalid?api_key=test-setup-logging-secret"
        )
        for handler in root.handlers:
            handler.flush()

        output = log_file.read_text(encoding="utf-8")
        assert "test-setup-logging-secret" not in output
        assert "api_key=[REDACTED]" in output
    finally:
        installed_handlers = list(root.handlers)
        root.handlers = original_handlers
        for handler in installed_handlers:
            handler.close()
