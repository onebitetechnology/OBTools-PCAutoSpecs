"""
RepairDesk OAuth 2.0 helpers for desktop auth-code flow.
"""

from __future__ import annotations

import logging
import secrets
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from settings import load_settings, save_settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_expiry(expires_at: str) -> Optional[datetime]:
    value = str(expires_at or "").strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def oauth_is_connected(settings: Optional[Dict] = None) -> bool:
    settings = settings or load_settings()
    return bool(settings.get("oauth_access_token", "").strip())


def clear_oauth_tokens(settings: Optional[Dict] = None) -> Dict:
    settings = settings or load_settings()
    settings["oauth_access_token"] = ""
    settings["oauth_refresh_token"] = ""
    settings["oauth_token_expires_at"] = ""
    save_settings(settings)
    return settings


def _store_token_response(token_data: Dict, settings: Optional[Dict] = None) -> Dict:
    settings = settings or load_settings()
    settings["oauth_access_token"] = str(token_data.get("access_token", "")).strip()
    settings["oauth_refresh_token"] = str(token_data.get("refresh_token", "")).strip()

    expires_in = token_data.get("expires_in")
    expires_at = token_data.get("expires_at")
    if not expires_at and expires_in:
        try:
            expires_at = (_utcnow() + timedelta(seconds=int(expires_in))).isoformat()
        except Exception:
            expires_at = ""
    settings["oauth_token_expires_at"] = str(expires_at or "")
    save_settings(settings)
    return settings


def token_needs_refresh(settings: Optional[Dict] = None, margin_seconds: int = 120) -> bool:
    settings = settings or load_settings()
    expires_at = _parse_expiry(settings.get("oauth_token_expires_at", ""))
    if not expires_at:
        return False
    return expires_at <= (_utcnow() + timedelta(seconds=margin_seconds))


def build_authorize_url(settings: Optional[Dict] = None, state: Optional[str] = None) -> str:
    settings = settings or load_settings()
    authorize_url = settings.get("oauth_authorize_url", "").strip()
    client_id = settings.get("oauth_client_id", "").strip()
    redirect_uri = settings.get("oauth_redirect_uri", "").strip()
    if not authorize_url or not client_id or not redirect_uri:
        raise RuntimeError("OAuth authorize URL, client ID, and redirect URI are required.")

    state = state or secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    separator = "&" if "?" in authorize_url else "?"
    return f"{authorize_url}{separator}{urlencode(params)}"


def exchange_code_for_token(code: str, settings: Optional[Dict] = None) -> Dict:
    settings = settings or load_settings()
    token_url = settings.get("oauth_token_url", "").strip()
    client_id = settings.get("oauth_client_id", "").strip()
    client_secret = settings.get("oauth_client_secret", "").strip()
    redirect_uri = settings.get("oauth_redirect_uri", "").strip()
    if not token_url or not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("OAuth token URL, client ID, client secret, and redirect URI are required.")

    response = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    _store_token_response(token_data, settings)
    return token_data


def refresh_access_token(settings: Optional[Dict] = None) -> Dict:
    settings = settings or load_settings()
    token_url = settings.get("oauth_token_url", "").strip()
    client_id = settings.get("oauth_client_id", "").strip()
    client_secret = settings.get("oauth_client_secret", "").strip()
    refresh_token = settings.get("oauth_refresh_token", "").strip()
    if not token_url or not client_id or not client_secret or not refresh_token:
        raise RuntimeError("OAuth refresh requires token URL, client ID, client secret, and refresh token.")

    response = requests.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    if "refresh_token" not in token_data:
        token_data["refresh_token"] = refresh_token
    _store_token_response(token_data, settings)
    return token_data


def ensure_valid_access_token(settings: Optional[Dict] = None) -> Optional[str]:
    settings = settings or load_settings()
    access_token = settings.get("oauth_access_token", "").strip()
    if not access_token:
        return None
    if token_needs_refresh(settings):
        try:
            token_data = refresh_access_token(settings)
            access_token = str(token_data.get("access_token", "")).strip() or access_token
        except Exception as e:
            logging.warning(f"RepairDesk OAuth token refresh failed: {e}")
    return access_token or None


def run_oauth_flow(settings: Optional[Dict] = None, timeout_seconds: int = 180) -> Dict:
    """
    Run the desktop OAuth authorization-code flow with a local loopback callback.
    """
    settings = dict(settings or load_settings())
    redirect_uri = settings.get("oauth_redirect_uri", "").strip()
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise RuntimeError("RepairDesk OAuth redirect URI must use http(s)://127.0.0.1 or localhost.")

    state = secrets.token_urlsafe(24)
    authorize_url = build_authorize_url(settings, state=state)

    result = {"code": None, "error": None, "state": None}
    received = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            result["code"] = (query.get("code") or [None])[0]
            result["error"] = (query.get("error") or [None])[0]
            result["state"] = (query.get("state") or [None])[0]

            body = (
                "<html><body style='font-family:Segoe UI,Arial,sans-serif;'>"
                "<h2>PC AutoSpec</h2>"
                "<p>You can close this browser window and return to the app.</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            received.set()

        def log_message(self, format, *args):  # noqa: A003
            return

    server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
    server.timeout = 1

    def _serve():
        while not received.is_set():
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    if not webbrowser.open(authorize_url):
        raise RuntimeError("Could not open the default browser for RepairDesk OAuth sign-in.")

    if not received.wait(timeout_seconds):
        raise RuntimeError("RepairDesk OAuth sign-in timed out before authorization completed.")

    server.server_close()

    if result["error"]:
        raise RuntimeError(f"RepairDesk authorization failed: {result['error']}")
    if result["state"] != state:
        raise RuntimeError("RepairDesk OAuth state verification failed.")
    if not result["code"]:
        raise RuntimeError("RepairDesk OAuth did not return an authorization code.")

    return exchange_code_for_token(result["code"], settings)
