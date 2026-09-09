"""Shared display grouping for GUI and offline reports."""


def display_groups(specs):
    """Return panel details and role groups; structured inventory wins over legacy text."""
    panel = specs.get('PanelDetails') or {}
    if not isinstance(panel, dict):
        panel = {}
    if 'DisplayDetails' not in specs:
        text = specs.get('Display') or ''
        lines = text.splitlines() if isinstance(text, str) else text
        return panel, [], [str(s).strip() for s in lines if str(s).strip()], []
    records = [d for d in (specs.get('DisplayDetails') or []) if isinstance(d, dict)]
    internal = [d for d in records if d.get('role') == 'internal']
    if internal:
        first = next((d for d in internal if d.get('instance_name') == panel.get('instance_name')), internal[0])
        if panel.get('instance_name') and panel['instance_name'] != first.get('instance_name'):
            panel = {}
        panel = dict(panel)
        for key, source in (('model_code', 'model'), ('manufacturer', 'manufacturer'),
                            ('connection_type', 'connection_type'), ('instance_name', 'instance_name')):
            panel.setdefault(key, first.get(source))
        panel.setdefault('name', first.get('name') or first.get('display_id') or 'Unknown')
        additional = [d.get('name') or 'Unknown' for d in internal if d is not first]
    else:
        panel, additional = {}, []
    external = [d.get('name') or 'Unknown' for d in records if d.get('role') == 'external']
    unknown = [d.get('name') or 'Unknown' for d in records if d.get('role') not in ('internal', 'external')]
    return panel, additional, external, unknown
