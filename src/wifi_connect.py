"""
wifi_connect.py — Auto-connect to shop WiFi if no internet detected.
Uses only Windows built-in netsh — no extra packages required.
"""

import logging
import subprocess
import time

_POWERSHELL_EXE = "powershell.exe"
_NETSH = "netsh.exe"


def _has_internet(timeout: int = 3) -> bool:
    """Quick check — try to reach a reliable host."""
    try:
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command",
             "Test-Connection -ComputerName 8.8.8.8 -Count 1 -Quiet"],
            capture_output=True, text=True, timeout=timeout + 2,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.returncode == 0 and result.stdout.strip().lower() == 'true'
    except Exception:
        return False


def _profile_exists(ssid: str) -> bool:
    """Check if a WiFi profile for this SSID already exists on this machine."""
    try:
        result = subprocess.run(
            [_NETSH, "wlan", "show", "profile", f"name={ssid}"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.returncode == 0
    except Exception:
        return False


def _create_wifi_profile(ssid: str, password: str) -> bool:
    """Create a WPA2 WiFi profile using netsh and an XML template."""
    profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>"""

    import tempfile, os
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml',
                                         delete=False, encoding='utf-8') as f:
            f.write(profile_xml)
            tmp_path = f.name

        result = subprocess.run(
            [_NETSH, "wlan", "add", "profile", f"filename={tmp_path}"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        os.unlink(tmp_path)
        return result.returncode == 0
    except Exception as e:
        logging.warning(f"WiFi profile creation failed: {e}")
        return False


def _connect_to_ssid(ssid: str) -> bool:
    """Tell Windows to connect to the named SSID."""
    try:
        result = subprocess.run(
            [_NETSH, "wlan", "connect", f"name={ssid}"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.returncode == 0
    except Exception as e:
        logging.warning(f"WiFi connect command failed: {e}")
        return False


def ensure_internet(ssid: str, password: str, log_callback=None) -> dict:
    """
    Main entry point. Call this on startup.
    Returns dict with keys: connected (bool), method (str), message (str)
    """
    def _log(msg):
        logging.info(msg)
        if log_callback:
            log_callback(msg)

    # Step 1: Check if already online
    _log("Checking internet connectivity...")
    if _has_internet():
        _log("  Internet available — skipping WiFi setup")
        return {'connected': True, 'method': 'existing', 'message': 'Already connected'}

    _log(f"  No internet detected — attempting to connect to {ssid}...")

    # Step 2: Create profile if needed
    if not _profile_exists(ssid):
        _log(f"  Creating WiFi profile for {ssid}...")
        if not _create_wifi_profile(ssid, password):
            msg = f"Could not create WiFi profile for {ssid}"
            logging.warning(msg)
            return {'connected': False, 'method': 'failed', 'message': msg}

    # Step 3: Connect
    _log(f"  Connecting to {ssid}...")
    if not _connect_to_ssid(ssid):
        msg = f"Could not connect to {ssid}"
        logging.warning(msg)
        return {'connected': False, 'method': 'failed', 'message': msg}

    # Step 4: Wait up to 15 seconds for internet to come up
    _log("  Waiting for connection to establish...")
    for i in range(5):
        time.sleep(3)
        if _has_internet():
            _log(f"  Connected to {ssid} successfully!")
            return {'connected': True, 'method': 'wifi', 'message': f'Connected to {ssid}'}
        _log(f"  Still connecting... ({(i+1)*3}s)")

    msg = f"Connected to {ssid} but internet not reachable"
    logging.warning(msg)
    return {'connected': False, 'method': 'wifi_no_internet', 'message': msg}
