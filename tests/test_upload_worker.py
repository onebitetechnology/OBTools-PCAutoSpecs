import logging

import pytest

from log_safety import install_credential_filter
from repairdesk_api import RequestOutcome
from workers import UploadWorker


class FakeRepairDeskAPI:
    def __init__(self, *, result=None, ticket_error=None, note_error=None):
        self.result = result or {"success": True}
        self.ticket_error = ticket_error
        self.note_error = note_error
        self.last_request_outcome = RequestOutcome()

    def get_ticket_customer(self, ticket_id):
        if self.ticket_error:
            self.last_request_outcome = RequestOutcome(attempts=1, status_code=None)
            raise self.ticket_error
        self.last_request_outcome = RequestOutcome(attempts=1, status_code=200)
        return {
            "id": "resolved-ticket-id",
            "customer_name": "Synthetic Customer",
            "device": "Synthetic Device",
            "ticket_number": ticket_id,
        }

    def add_diagnostic_note(self, ticket_id, note_html):
        if self.note_error:
            self.last_request_outcome = RequestOutcome(attempts=2, status_code=None)
            raise self.note_error
        status_code = 200 if self.result.get("success") else 500
        self.last_request_outcome = RequestOutcome(attempts=2, status_code=status_code)
        return dict(self.result)


@pytest.fixture
def capture_upload_logs(caplog):
    install_credential_filter(caplog.handler)
    caplog.set_level(logging.INFO)
    return caplog


def _run_worker(api, *, skip_confirmation=True, confirm=None):
    worker = UploadWorker(
        api,
        "12345",
        "<p>Synthetic diagnostic note</p>",
        skip_confirmation=skip_confirmation,
    )
    finished = []
    progress = []
    worker.finished.connect(lambda success, message: finished.append((success, message)))
    worker.progress.connect(lambda message, tag: progress.append((message, tag)))
    if confirm is not None:
        worker.confirm_customer.connect(lambda ticket: worker.set_confirmed(confirm))
    worker.run()
    return finished, progress


def _terminal_lines(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("RepairDesk upload terminal")
    ]


def test_success_logs_one_terminal_upload_event(capture_upload_logs):
    finished, _ = _run_worker(FakeRepairDeskAPI())

    assert finished == [(True, "resolved-ticket-id")]
    assert _terminal_lines(capture_upload_logs) == [
        "RepairDesk upload terminal outcome=success ticket=12345 attempts=2 http_status=200"
    ]


def test_technician_cancellation_logs_one_terminal_upload_event(capture_upload_logs):
    finished, _ = _run_worker(
        FakeRepairDeskAPI(),
        skip_confirmation=False,
        confirm=False,
    )

    assert finished == [(False, "Upload cancelled by tech")]
    assert _terminal_lines(capture_upload_logs) == [
        "RepairDesk upload terminal outcome=cancelled ticket=12345 attempts=1 http_status=200"
    ]


def test_failed_api_result_logs_one_terminal_upload_event(capture_upload_logs):
    api = FakeRepairDeskAPI(
        result={"success": False, "message": "Synthetic server rejection"}
    )

    finished, _ = _run_worker(api)

    assert finished == [(False, "Synthetic server rejection")]
    assert _terminal_lines(capture_upload_logs) == [
        "RepairDesk upload terminal outcome=failed ticket=12345 attempts=2 http_status=500"
    ]


def test_exception_is_redacted_and_logs_one_terminal_upload_event(capture_upload_logs):
    error = RuntimeError(
        "failed https://example.invalid?api_key=test-worker-exception-secret"
    )

    finished, _ = _run_worker(FakeRepairDeskAPI(ticket_error=error))

    assert finished == [
        (False, "failed https://example.invalid?api_key=[REDACTED]")
    ]
    assert "test-worker-exception-secret" not in capture_upload_logs.text
    assert _terminal_lines(capture_upload_logs) == [
        "RepairDesk upload terminal outcome=failed ticket=12345 attempts=1 http_status=none"
    ]


@pytest.mark.parametrize(
    "api",
    [
        FakeRepairDeskAPI(),
        FakeRepairDeskAPI(result={"success": False, "message": "Rejected"}),
        FakeRepairDeskAPI(ticket_error=RuntimeError("Synthetic failure")),
    ],
)
def test_terminal_event_excludes_customer_device_and_note(api, capture_upload_logs):
    _run_worker(api)

    terminal_text = "\n".join(_terminal_lines(capture_upload_logs))
    assert "Synthetic Customer" not in terminal_text
    assert "Synthetic Device" not in terminal_text
    assert "Synthetic diagnostic note" not in terminal_text
