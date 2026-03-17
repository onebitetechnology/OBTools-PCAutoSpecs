"""
PC AutoSpec — Configuration bridge
Reads API settings from the settings system. No hardcoded credentials.
"""

from settings import load_settings

def get_api_key():
    return load_settings().get('api_key', '')

def get_api_base_url():
    return load_settings().get('api_base_url', 'https://api.repairdesk.co/api/web/v1')

def get_tickets_per_page():
    return load_settings().get('tickets_per_page', 100)
