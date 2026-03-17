"""
RepairDesk API Client
Handles communication with the RepairDesk API
"""

import requests
import json
import logging
from config import get_api_key, get_api_base_url, get_tickets_per_page


class RepairDeskAPI:
    """Client for interacting with RepairDesk API"""

    def __init__(self, api_key=None, base_url=None):
        """
        Initialize the API client.
        Reads from settings if no explicit values are passed.
        """
        self.api_key = api_key or get_api_key()
        self.base_url = base_url or get_api_base_url()
        self.tickets_per_page = get_tickets_per_page()

    def _build_endpoints(self):
        """Build endpoint URLs from current key/url (called fresh each time
        so that settings changes mid-session are picked up)."""
        key = self.api_key or get_api_key()
        base = self.base_url or get_api_base_url()
        per_page = self.tickets_per_page or get_tickets_per_page()
        return {
            'addnote': f"{base}/ticket/addnote?api_key={key}",
            'tickets': f"{base}/tickets?api_key={key}&per-page={per_page}",
        }

    def test_connection(self):
        """Test API connectivity. Returns (success: bool, message: str)."""
        key = self.api_key or get_api_key()
        if not key:
            return False, "No API key configured"
        endpoints = self._build_endpoints()
        try:
            response = requests.get(f"{endpoints['tickets']}&page=1", timeout=15)
            if response.status_code == 200:
                return True, "Connected successfully"
            elif response.status_code == 401:
                return False, "Invalid API key"
            elif response.status_code == 403:
                return False, "Access denied — check API key permissions"
            else:
                return False, f"HTTP {response.status_code}: {response.reason}"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except requests.exceptions.ConnectionError:
            return False, "Could not reach RepairDesk API — check internet connection"
        except Exception as e:
            return False, f"Connection error: {str(e)}"

    def resolve_ticket_id(self, ticket_short_id):
        """
        Resolve a ticket short ID (e.g. "T-12126") to the internal ticket ID.
        Paginates through tickets until found.
        """
        result = self._find_ticket(ticket_short_id)
        return result['id']

    def get_ticket_customer(self, ticket_short_id):
        """
        Return a dict with customer info for confirmation before upload.
        Keys: id, customer_name, device, ticket_number
        """
        return self._find_ticket(ticket_short_id)

    def _find_ticket(self, ticket_short_id):
        """
        Shared lookup — finds ticket by short ID and returns full summary dict
        with normalized keys: id, customer_name, device, ticket_number.
        """
        endpoints = self._build_endpoints()
        current_page = 1

        while True:
            try:
                url = f"{endpoints['tickets']}&page={current_page}"
                response = requests.get(url, timeout=30)
                response.raise_for_status()

                data = response.json()

                if 'data' in data and 'ticketData' in data['data']:
                    ticket_data = data['data']['ticketData']

                    for ticket in ticket_data:
                        if 'summary' not in ticket:
                            continue
                        summary = ticket['summary']
                        ticket_ref = (
                            summary.get('order_id') or
                            summary.get('ticket_number') or
                            summary.get('ref_id') or
                            str(summary.get('id', ''))
                        )
                        if ticket_ref == ticket_short_id:
                            logging.debug(f"Ticket summary fields: {list(summary.keys())}")

                            # Try direct name fields first
                            customer = (
                                summary.get('customer_name') or
                                summary.get('client_name') or
                                summary.get('name') or
                                f"{summary.get('first_name', '')} {summary.get('last_name', '')}".strip()
                            )

                            # RepairDesk nests customer info under a 'customer' object
                            if not customer:
                                cust_obj = summary.get('customer', {})
                                customer = (
                                    cust_obj.get('fullName') or
                                    cust_obj.get('full_name') or
                                    f"{cust_obj.get('firstName', '')} {cust_obj.get('lastName', '')}".strip() or
                                    cust_obj.get('email', '')
                                )

                            # Fallback: look up via customer_id endpoint
                            if not customer:
                                cust_id = (
                                    summary.get('customer_id') or
                                    summary.get('client_id') or
                                    summary.get('customerId')
                                )
                                if cust_id:
                                    try:
                                        key = self.api_key or get_api_key()
                                        base = self.base_url or get_api_base_url()
                                        cust_url = f"{base}/customers/{cust_id}?api_key={key}"
                                        cust_resp = requests.get(cust_url, timeout=10)
                                        cust_data = cust_resp.json()
                                        cd = cust_data.get('data', cust_data)
                                        customer = (
                                            cd.get('customer_name') or
                                            cd.get('name') or
                                            f"{cd.get('first_name', '')} {cd.get('last_name', '')}".strip() or
                                            cd.get('email', '')
                                        )
                                    except Exception as e:
                                        logging.debug(f"Customer lookup failed: {e}")

                            customer = customer or 'Unknown Customer'
                            device = (
                                summary.get('device') or
                                summary.get('item_name') or
                                summary.get('product_name') or
                                ''
                            )
                            return {
                                'id': summary['id'],
                                'customer_name': customer,
                                'device': device,
                                'ticket_number': ticket_ref,
                            }

                    pagination = data['data'].get('pagination', {})
                    if pagination.get('next_page_exist', 0) == 1:
                        current_page += 1
                        continue
                    else:
                        break
                else:
                    break

            except requests.exceptions.RequestException as e:
                raise Exception(f"API request failed: {str(e)}")
            except json.JSONDecodeError as e:
                raise Exception(f"Invalid JSON response: {str(e)}")
            except Exception as e:
                raise Exception(f"Error resolving ticket ID: {str(e)}")

        raise Exception(f"Ticket number {ticket_short_id} not found.")

    def add_diagnostic_note(self, ticket_id, note):
        """Add a diagnostic note to a ticket."""
        endpoints = self._build_endpoints()
        try:
            payload = {
                "id": ticket_id,
                "note": note,
                "type": 1,
                "is_flag": 0
            }

            response = requests.post(
                endpoints['addnote'],
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()

            return response.json()

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "message": f"Request error: {str(e)}"
            }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "message": f"Invalid JSON response: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}"
            }
