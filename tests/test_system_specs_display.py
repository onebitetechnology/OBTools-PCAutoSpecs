import json
import os
import shutil
import sys
from types import SimpleNamespace as NS
import pytest
import system_specs as specs


def connection_json(monkeypatch, payload, returncode=0):
    monkeypatch.setattr(specs.subprocess, 'run', lambda *a, **kw: NS(returncode=returncode, stdout=payload))


def test_connection_collector(monkeypatch, fixture_dir):
    connection_json(monkeypatch, (fixture_dir / 'display_connections.json').read_text())
    records = specs._get_monitor_connection_records()
    assert [r['role'] for r in records] == ['internal', 'external', 'unknown']
    assert records[0]['instance_name'] == r'DISPLAY\CMN15F5\INTERNAL'
    assert records[0]['display_id'] == 'CMN15F5'


@pytest.mark.parametrize('payload,code', [('[]',0),('null',0),('garbage',0),('',1)])
def test_failed_connections(monkeypatch,payload,code):
    connection_json(monkeypatch,payload,code)
    assert specs._get_monitor_connection_records() == []


def test_topology_distinct_same_model_and_identity_join(monkeypatch):
    connections = [dict(instance_name=r'DISPLAY\CMN15F5\INTERNAL_0',video_output_technology=11),
                   dict(instance_name=r'DISPLAY\DEL1234\FIRST_0',video_output_technology=5),
                   dict(instance_name=r'DISPLAY\DEL1234\SECOND_0',video_output_technology=5)]
    connection_json(monkeypatch,json.dumps(connections))
    def query(_wmi, name):
        rows = []
        if 'PnPEntity' in name:
            rows = [dict(DeviceID=c['instance_name'][:-2],Name='Panel' if i == 0 else f'Dell {i}') for i,c in enumerate(connections)]
        if name == 'Win32_DesktopMonitor':
            rows = [dict(PNPDeviceID=c['instance_name'][:-2],Name='Generic PnP Monitor',ScreenWidth=1920,ScreenHeight=1080) for c in connections]
        assert 'VideoController' not in name
        return NS(Count=len(rows),ItemIndex=lambda i:NS(Properties_=lambda k:NS(Value=rows[i].get(k))))
    monkeypatch.setattr(specs,'_query_com_wmi',query)
    text, details = specs._get_display_info(object())
    assert len(details) == 3
    assert [d['role'] for d in details] == ['internal','external','external']
    assert text == 'Dell 1 - 1920x1080\nDell 2 - 1920x1080'


@pytest.mark.parametrize('technology,text_expected',[(11,''),(5,'CMN15F5'),(-1,'CMN15F5')])
def test_connection_only_topology(monkeypatch,technology,text_expected):
    connection_json(monkeypatch,json.dumps(dict(instance_name=r'DISPLAY\CMN15F5\ONE_0',video_output_technology=technology)))
    text, details = specs._get_display_info(None)
    assert text == text_expected
    assert len(details) == 1


def test_no_gpu_identity_fallback(monkeypatch):
    connection_json(monkeypatch,'[]')
    monkeypatch.setattr(specs,'_query_com_wmi',lambda *a:None)
    assert specs._get_display_info(object()) == ('Display information unavailable', [])


def test_ambiguous_model_only_identity_does_not_add_monitor(monkeypatch):
    connections = [dict(instance_name=rf'DISPLAY\DEL1234\{instance}_0',
                        video_output_technology=5) for instance in ('FIRST', 'SECOND')]
    connection_json(monkeypatch, json.dumps(connections))
    def query(_wmi, name):
        rows = [dict(DeviceID=r'DISPLAY\DEL1234', Name='Dell monitor')] if 'PnPEntity' in name else []
        return NS(Count=len(rows), ItemIndex=lambda i: NS(Properties_=lambda k: NS(Value=rows[i].get(k))))
    monkeypatch.setattr(specs, '_query_com_wmi', query)
    text, details = specs._get_display_info(object())
    assert len(details) == 2
    assert {d['instance_name'] for d in details} == {r'DISPLAY\DEL1234\FIRST', r'DISPLAY\DEL1234\SECOND'}
    assert all(d['role'] == 'external' for d in details)
    assert text.splitlines() == ['DEL1234', 'DEL1234']


def test_selected_panel_and_no_arbitrary_fallback(monkeypatch):
    monkeypatch.setattr(specs.platform,'system',lambda:'Windows')
    panel = dict(instance_name=r'DISPLAY\CMN15F5\INTERNAL_0',manufacturer='Chimei Innolux',
                 video_output_technology=11,size_inches_exact=16.3,size_cm_h=36,size_cm_v=21,
                 resolution_h=1920,resolution_v=1080,is_touch=False)
    connection_json(monkeypatch,json.dumps(panel))
    assert specs._get_panel_details() is None
    result = specs._get_panel_details(r'DISPLAY\CMN15F5\INTERNAL')
    assert result['model_code'] == 'CMN15F5'
    assert result['size_inches_exact'] == 16.3
    assert result['size_inches'] == result['size_inches_nominal'] == 16
    assert result['connection_role'] == 'internal'
    assert result['is_touch'] is False
    assert specs._get_panel_details(r'DISPLAY\OTHER\EXTERNAL') is None


def test_actual_powershell_with_external_record_first(monkeypatch, fixture_dir):
    """Execute emitted production scripts against a WMI fixture when pwsh is installed."""
    pwsh = (os.environ.get('PCAUTOSPEC_TEST_PWSH') or shutil.which('powershell.exe')
            or shutil.which('pwsh'))
    if not pwsh:
        if sys.platform == 'win32':
            pytest.fail('Windows PowerShell must be available to verify production scripts')
        pytest.skip('PowerShell runtime unavailable; enable PCAUTOSPEC_TEST_PWSH')
    run = specs.subprocess.run
    prelude = (fixture_dir / 'display_wmi.ps1').read_text()
    def fixture_run(args, **kwargs):
        # PowerShell parses and runs the exact script emitted by the collector.
        return run([pwsh, '-NoProfile', '-Command', prelude + '\n' + args[-1]],
                   capture_output=True, text=True, timeout=20)
    monkeypatch.setattr(specs.subprocess, 'run', fixture_run)
    monkeypatch.setattr(specs.platform, 'system', lambda: 'Windows')
    connections = specs._get_monitor_connection_records()
    assert [c['role'] for c in connections] == ['external', 'internal']
    assert connections[0]['connection_type'] == 'HD15/VGA'
    panel = specs._get_panel_details(connections[1]['instance_name'])
    assert panel['manufacturer'] == 'Chimei Innolux'
    assert panel['serial_number'] == 'INT'
    assert panel['manufacture_year'] == 2023
    assert (panel['resolution_h'],panel['resolution_v']) == (1920,1080)
    assert (panel['size_cm_h'],panel['size_cm_v']) == (35,22)
    assert panel['size_inches_exact'] == 16.3
    assert panel['size_inches_nominal'] == 16
    assert panel['is_touch'] is False
