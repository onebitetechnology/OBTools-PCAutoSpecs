"""
RepairDesk API Client
Handles communication with the RepairDesk API.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from config import get_api_base_url, get_api_key, get_auth_mode, get_tickets_per_page
from log_safety import redact_sensitive_text as _redact_sensitive_text
from oauth_repairdesk import ensure_valid_access_token


@dataclass(frozen=True)
class RequestOutcome:
    attempts: int = 0
    status_code: Optional[int] = None


class RepairDeskAPI:
    """Client for interacting with RepairDesk API."""

    def __init__(self, api_key=None, base_url=None, auth_mode=None):
        self.api_key = api_key or get_api_key()
        self.base_url = base_url or get_api_base_url()
        self.tickets_per_page = get_tickets_per_page()
        self.auth_mode = auth_mode or get_auth_mode()
        self.last_request_outcome = RequestOutcome()

    def _build_endpoints(self):
        base = (self.base_url or get_api_base_url()).rstrip("/")
        per_page = self.tickets_per_page or get_tickets_per_page()
        return {
            "addnote": f"{base}/ticket/addnote",
            "tickets": f"{base}/tickets",
            "customer": f"{base}/customers",
            "tickets_per_page": per_page,
        }

    def _request(self, method, url, *, params=None, json_payload=None, timeout=30):
        self.last_request_outcome = RequestOutcome()
        params = dict(params or {})
        headers = {"Content-Type": "application/json"}
        auth_mode = self.auth_mode or get_auth_mode()

        if auth_mode == "oauth":
            access_token = ensure_valid_access_token()
            if not access_token:
                raise Exception("RepairDesk OAuth is selected but no valid access token is available.")
            headers["Authorization"] = f"Bearer {access_token}"
        else:
            key = self.api_key or get_api_key()
            if not key:
                raise Exception("No RepairDesk API key configured.")
            params["api_key"] = key

        attempts = 3
        last_response = None
        for attempt in range(1, attempts + 1):
            try:
                response = requests.request(
                    method,
                    url,
                    params=params,
                    json=json_payload,
                    headers=headers,
                    timeout=timeout,
                )
            except Exception:
                self.last_request_outcome = RequestOutcome(
                    attempts=attempt,
                    status_code=None,
                )
                raise
            last_response = response
            self.last_request_outcome = RequestOutcome(
                attempts=attempt,
                status_code=response.status_code,
            )

            if response.status_code != 429:
                response.raise_for_status()
                return response

            if attempt >= attempts:
                break

            retry_after = response.headers.get("Retry-After")
            try:
                wait_seconds = float(retry_after) if retry_after else 1.0 * attempt
            except (TypeError, ValueError):
                wait_seconds = 1.0 * attempt
            wait_seconds = max(0.5, min(wait_seconds, 5.0))
            logging.warning(
                "RepairDesk rate limit hit (%s %s), retrying in %.1fs (attempt %s/%s)",
                method,
                _redact_sensitive_text(url),
                wait_seconds,
                attempt + 1,
                attempts,
            )
            time.sleep(wait_seconds)

        if last_response is not None:
            last_response.raise_for_status()
        raise requests.exceptions.RequestException("No response received from RepairDesk API")

    def test_connection(self):
        """Test API connectivity. Returns (success: bool, message: str)."""
        endpoints = self._build_endpoints()
        try:
            response = self._request(
                "GET",
                endpoints["tickets"],
                params={"per-page": endpoints["tickets_per_page"], "page": 1},
                timeout=15,
            )
            if response.status_code == 200:
                return True, "Connected successfully"
            if response.status_code == 401:
                return False, "Authentication failed"
            if response.status_code == 403:
                return False, "Access denied — check RepairDesk permissions"
            return False, f"HTTP {response.status_code}: {response.reason}"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except requests.exceptions.ConnectionError:
            return False, "Could not reach RepairDesk API — check internet connection"
        except Exception as e:
            return False, f"Connection error: {_redact_sensitive_text(e)}"

    def resolve_ticket_id(self, ticket_short_id):
        result = self._find_ticket(ticket_short_id)
        return result["id"]

    def get_ticket_customer(self, ticket_short_id):
        return self._find_ticket(ticket_short_id)

    def _find_ticket(self, ticket_short_id):
        endpoints = self._build_endpoints()
        current_page = 1

        while True:
            try:
                response = self._request(
                    "GET",
                    endpoints["tickets"],
                    params={
                        "per-page": endpoints["tickets_per_page"],
                        "page": current_page,
                    },
                    timeout=30,
                )
                data = response.json()

                if "data" not in data or "ticketData" not in data["data"]:
                    break

                ticket_data = data["data"]["ticketData"]
                for ticket in ticket_data:
                    if "summary" not in ticket:
                        continue
                    summary = ticket["summary"]
                    ticket_ref = (
                        summary.get("order_id")
                        or summary.get("ticket_number")
                        or summary.get("ref_id")
                        or str(summary.get("id", ""))
                    )
                    if ticket_ref != ticket_short_id:
                        continue

                    logging.debug(f"Ticket summary fields: {list(summary.keys())}")
                    customer = (
                        summary.get("customer_name")
                        or summary.get("client_name")
                        or summary.get("name")
                        or f"{summary.get('first_name', '')} {summary.get('last_name', '')}".strip()
                    )
                    if not customer:
                        cust_obj = summary.get("customer", {})
                        customer = (
                            cust_obj.get("fullName")
                            or cust_obj.get("full_name")
                            or f"{cust_obj.get('firstName', '')} {cust_obj.get('lastName', '')}".strip()
                            or cust_obj.get("email", "")
                        )

                    if not customer:
                        cust_id = (
                            summary.get("customer_id")
                            or summary.get("client_id")
                            or summary.get("customerId")
                        )
                        if cust_id:
                            try:
                                cust_resp = self._request(
                                    "GET",
                                    f"{endpoints['customer']}/{cust_id}",
                                    timeout=10,
                                )
                                cust_data = cust_resp.json()
                                cd = cust_data.get("data", cust_data)
                                customer = (
                                    cd.get("customer_name")
                                    or cd.get("name")
                                    or f"{cd.get('first_name', '')} {cd.get('last_name', '')}".strip()
                                    or cd.get("email", "")
                                )
                            except Exception as e:
                                logging.debug(f"Customer lookup failed: {e}")

                    customer = customer or "Unknown Customer"
                    device = (
                        summary.get("device")
                        or summary.get("item_name")
                        or summary.get("product_name")
                        or ""
                    )
                    return {
                        "id": summary["id"],
                        "customer_name": customer,
                        "device": device,
                        "ticket_number": ticket_ref,
                    }

                pagination = data["data"].get("pagination", {})
                if pagination.get("next_page_exist", 0) == 1:
                    current_page += 1
                    continue
                break

            except requests.exceptions.RequestException as e:
                raise Exception(f"API request failed: {_redact_sensitive_text(e)}")
            except json.JSONDecodeError as e:
                raise Exception(f"Invalid JSON response: {str(e)}")
            except Exception as e:
                raise Exception(f"Error resolving ticket ID: {_redact_sensitive_text(e)}")

        raise Exception(f"Ticket number {ticket_short_id} not found.")

    def add_diagnostic_note(self, ticket_id, note):
        endpoints = self._build_endpoints()
        try:
            payload = {
                "id": ticket_id,
                "note": note,
                "type": 1,
                "is_flag": 0,
            }
            response = self._request(
                "POST",
                endpoints["addnote"],
                json_payload=payload,
                timeout=30,
            )
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"success": False, "message": f"Request error: {_redact_sensitive_text(e)}"}
        except json.JSONDecodeError as e:
            return {"success": False, "message": f"Invalid JSON response: {str(e)}"}
        except Exception as e:
            return {"success": False, "message": f"Unexpected error: {_redact_sensitive_text(e)}"}
