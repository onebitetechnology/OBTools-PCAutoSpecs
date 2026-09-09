import pytest
from log_integrity import inspect_log_bytes, inspect_log_file

@pytest.mark.parametrize('data,status', [(b'Session Start\nSession End\nFinal Status: COMPLETE','complete'), (b'Session Start','incomplete'), (b'','incomplete'), (b'Session Start\x00\x01','corrupt'), (b'\xff','corrupt'), (b'Session End\nFinal Status\nSession Start','incomplete')])
def test_bytes(data,status):
    result = inspect_log_bytes(data)
    assert result.status == status
    assert 'secret' not in result.reason

def test_missing(tmp_path):
    assert inspect_log_file(tmp_path/'missing').status == 'unreadable'

def test_unreadable(tmp_path,monkeypatch):
    from pathlib import Path
    def denied(*args):
        raise PermissionError('secret contents must not enter reason')
    monkeypatch.setattr(Path,'read_bytes',denied)
    result=inspect_log_file(tmp_path/'existing')
    assert result.status=='unreadable'
    assert 'secret' not in result.reason

def test_appended_session_needs_own_footer():
    complete=b'Session Start\nSession End\nFinal Status: COMPLETE\n'
    assert inspect_log_bytes(complete+b'Session Start\n').status=='incomplete'
    assert inspect_log_bytes(complete+complete).status=='complete'
