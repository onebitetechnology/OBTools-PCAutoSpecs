import pytest
import requests

import repairdesk_api
from repairdesk_api import RepairDeskAPI, RequestOutcome


def _response(status_code, *, retry_after=None):
    response = requests.Response()
    response.status_code = status_code
    response.reason = "synthetic response"
    response.url = "https://example.invalid/tickets"
    response._content = b"{}"
    response.request = requests.Request("GET", response.url).prepare()
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _api():
    return RepairDeskAPI(
        api_key="test-api-secret-123",
        base_url="https://example.invalid",
        auth_mode="api_key",
    )


def test_request_outcome_starts_empty():
    assert _api().last_request_outcome == RequestOutcome()


def test_request_outcome_records_first_attempt_success(monkeypatch):
    api = _api()
    monkeypatch.setattr(repairdesk_api.requests, "request", lambda *args, **kwargs: _response(200))

    response = api._request("GET", "https://example.invalid/tickets")

    assert response.status_code == 200
    assert api.last_request_outcome == RequestOutcome(attempts=1, status_code=200)


def test_request_outcome_records_retries_then_success(monkeypatch):
    api = _api()
    responses = iter([_response(429, retry_after=0), _response(429), _response(200)])
    sleep_calls = []
    monkeypatch.setattr(repairdesk_api.requests, "request", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(repairdesk_api.time, "sleep", sleep_calls.append)

    response = api._request("GET", "https://example.invalid/tickets")

    assert response.status_code == 200
    assert api.last_request_outcome == RequestOutcome(attempts=3, status_code=200)
    assert sleep_calls == [0.5, 2.0]


def test_request_outcome_records_final_rate_limit_failure(monkeypatch):
    api = _api()
    responses = iter([_response(429), _response(429), _response(429)])
    monkeypatch.setattr(repairdesk_api.requests, "request", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(repairdesk_api.time, "sleep", lambda seconds: None)

    with pytest.raises(requests.exceptions.HTTPError):
        api._request("GET", "https://example.invalid/tickets")

    assert api.last_request_outcome == RequestOutcome(attempts=3, status_code=429)


def test_request_outcome_records_attempt_without_response(monkeypatch):
    api = _api()

    def raise_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("synthetic connection failure")

    monkeypatch.setattr(repairdesk_api.requests, "request", raise_connection_error)

    with pytest.raises(requests.exceptions.ConnectionError):
        api._request("GET", "https://example.invalid/tickets")

    assert api.last_request_outcome == RequestOutcome(attempts=1, status_code=None)


def test_request_outcome_resets_for_each_request(monkeypatch):
    api = _api()
    monkeypatch.setattr(repairdesk_api.requests, "request", lambda *args, **kwargs: _response(200))
    api._request("GET", "https://example.invalid/tickets")

    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("synthetic timeout")

    monkeypatch.setattr(repairdesk_api.requests, "request", raise_timeout)

    with pytest.raises(requests.exceptions.Timeout):
        api._request("GET", "https://example.invalid/tickets")

    assert api.last_request_outcome == RequestOutcome(attempts=1, status_code=None)
