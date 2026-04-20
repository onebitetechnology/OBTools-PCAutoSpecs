"""
PC AutoSpec — Settings Management
Handles persistent configuration: API keys and preferences.
Settings file lives NEXT TO the executable — on the USB stick, not on the customer PC.
"""

import os
import sys
import json
import logging

APP_NAME = 'PC AutoSpec'
APP_VERSION = '2.2.45-beta.21'

# ---------------------------------------------------------------------------
# Paths — everything lives next to the exe (portable)
# ---------------------------------------------------------------------------

def get_app_dir():
    """Directory where the exe (or script) lives. This is where settings go.
    Frozen: same folder as the .exe (on USB)
    Script: project root (one level above src/)
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_settings_path():
    return os.path.join(get_app_dir(), 'settings.json')


def get_assets_dir():
    """Assets directory — bundled inside exe or relative to source."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'assets')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')


def get_lhm_path():
    """Path to LibreHardwareMonitor.exe in the assets/LibreHardwareMonitor/ subfolder."""
    return os.path.join(get_assets_dir(), 'LibreHardwareMonitor', 'LibreHardwareMonitor.exe')


def get_vendor_dir():
    """Vendor folder — pre-installed Python packages shipped on the USB."""
    return os.path.join(get_app_dir(), 'vendor')


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULTS = {
    'auth_mode': 'api_key',
    'api_key': '',
    'store_name': '',
    'api_base_url': 'https://api.repairdesk.co/api/web/v1',
    'oauth_authorize_url': 'https://api.repairdesk.co/api/web/v1/oauth2/authorize',
    'oauth_token_url': 'https://api.repairdesk.co/api/web/v1/oauth2/token',
    'oauth_redirect_uri': 'http://127.0.0.1:8765/callback',
    'oauth_client_id': '',
    'oauth_client_secret': '',
    'oauth_access_token': '',
    'oauth_refresh_token': '',
    'oauth_token_expires_at': '',
    'tickets_per_page': 100,
    'include_beta_updates': False,
    'wifi_ssid': '',
    'wifi_password': '',
    'wifi_auto_connect': True,
    # Per-technician API keys: list of {'name': str, 'api_key': str}
    # When populated, the startup dialog shows a tech selector and uses the
    # selected tech's key instead of the global api_key.
    'technicians': [],
    'last_tech_name': '',
}


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_settings():
    """Load settings from disk, falling back to defaults for missing keys."""
    settings = dict(DEFAULTS)
    path = get_settings_path()
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            settings.update(saved)
        except Exception as e:
            logging.warning(f"Could not load settings from {path}: {e}")

    # Migrate earlier OAuth defaults that pointed at the wrong base path.
    if settings.get('oauth_authorize_url') == 'https://api.repairdesk.co/v1/oauth2/authorize':
        settings['oauth_authorize_url'] = DEFAULTS['oauth_authorize_url']
    if settings.get('oauth_token_url') == 'https://api.repairdesk.co/v1/oauth2/token':
        settings['oauth_token_url'] = DEFAULTS['oauth_token_url']
    return settings


def save_settings(settings):
    """Persist settings to disk (next to the exe on USB)."""
    path = get_settings_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        logging.info(f"Settings saved to {path}")
    except Exception as e:
        logging.error(f"Could not save settings to {path}: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_configured():
    """True if the app has valid auth configured."""
    settings = load_settings()
    if settings.get('auth_mode', 'api_key') == 'oauth':
        return bool(settings.get('oauth_access_token', '').strip())
    return bool(settings.get('api_key', '').strip())


def is_first_run_setup_complete():
    """True when the initial setup fields required for a fresh install are present."""
    settings = load_settings()
    has_store = bool(settings.get('store_name', '').strip())
    if settings.get('auth_mode', 'api_key') == 'oauth':
        has_auth = bool(settings.get('oauth_access_token', '').strip())
    else:
        has_auth = bool(settings.get('api_key', '').strip())
    return has_store and has_auth


def get_window_title():
    """Window title — includes version number for easy identification."""
    return f"{APP_NAME}  v{APP_VERSION}"


def get_store_name():
    """Store/shop name for branding."""
    return load_settings().get('store_name', '').strip()


def get_report_title():
    """Title line for diagnostic reports — branded with store name."""
    store = get_store_name()
    if store:
        return f"{store} \u2014 System Device Report"
    return "System Device Report"

UPLOAD_SCOPE_OVERVIEW = 'overview'
UPLOAD_SCOPE_FULL = 'full'
DEFAULT_UPLOAD_SCOPE = UPLOAD_SCOPE_OVERVIEW

UPLOAD_SCOPE_CHOICES = [
    (UPLOAD_SCOPE_OVERVIEW, 'Upload System Overview only'),
    (UPLOAD_SCOPE_FULL, 'Upload full results'),
]

# Test categories shown in the startup dialog and used for selective scanning
SCAN_CATEGORY_GROUPS = [
    ('Hardware', [
        ('cpu',          'CPU'),
        ('ram',          'RAM'),
        ('gpu',          'GPU'),
        ('motherboard',  'Motherboard & BIOS'),
        ('storage',      'Storage'),
        ('network',      'Network & WiFi'),
        ('display',      'Display & Webcam'),
        ('battery',      'Battery'),
    ]),
    ('Diagnostics', [
        ('event_logs',      'Event Logs'),
        ('windows_update',  'Windows Update'),
        ('defender',        'Defender'),
        ('startup_items',   'Startup Items'),
        ('device_manager',  'Device Manager'),
        ('power_boot',      'Power & Boot'),
    ]),
]

SCAN_CATEGORIES = [
    category
    for _, categories in SCAN_CATEGORY_GROUPS
    for category in categories
]


# ---------------------------------------------------------------------------
# Technician helpers
# ---------------------------------------------------------------------------

def get_technicians():
    """Return list of technician dicts: [{'name': str, 'api_key': str}, ...]"""
    return load_settings().get('technicians', [])


def get_tech_names():
    """Return sorted list of technician names."""
    return sorted(t['name'] for t in get_technicians() if t.get('name'))


def get_tech_api_key(tech_name):
    """Return the API key for a given technician name, or None if not found."""
    for t in get_technicians():
        if t.get('name') == tech_name:
            return t.get('api_key', '').strip() or None
    return None


def save_technicians(tech_list, settings=None):
    """Persist the technician roster. tech_list is [{'name':..., 'api_key':...}]"""
    if settings is None:
        settings = load_settings()
    settings['technicians'] = tech_list
    save_settings(settings)


def get_active_api_key(tech_name=None):
    """
    Resolve the API key to use for a given upload.
    Priority: tech-specific key > global api_key.
    Returns (api_key, source_label) where source_label is the tech name or 'global'.
    """
    if tech_name:
        key = get_tech_api_key(tech_name)
        if key:
            return key, tech_name
    settings = load_settings()
    global_key = settings.get('api_key', '').strip()
    return global_key, 'global'


def get_auth_mode():
    """Return the configured auth mode: api_key or oauth."""
    return load_settings().get('auth_mode', DEFAULTS['auth_mode'])
