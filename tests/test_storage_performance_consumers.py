from types import SimpleNamespace as NS
import pytest
from report_formatter import ReportFormatter
from panels import SystemInfoPanel


@pytest.mark.parametrize('identity,read,band',[('NVMe SSD',350,'Standard'),('HDD',2200,'Very fast')])
def test_actual_panel_report_and_issues(identity,read,band):
    drive = dict(model='Example',physical_type=identity,friendly_type='SATA SSD',status='Unknown')
    specs = {'Storage':'Drive C: 500GB total, 100 GB free (80% used) - Example (SATA SSD)',
             'StorageHealth':[drive], 'AdvancedHealth':{'disk_speed':{'status':'ok','read_mb_s':read,'write_mb_s':180,'cached_read_likely':True}}}
    rows=[]
    sec=NS(clear_dynamic=lambda:None,add_info_row=lambda key,value,**kw:rows.append((key,value)))
    panel=NS(_sec_storage=sec,_detect_drive_type=SystemInfoPanel._detect_drive_type,
             _format_storage_capacity_summary=SystemInfoPanel._format_storage_capacity_summary,
             _display_smart_rows=lambda *a:None)
    SystemInfoPanel._update_storage(panel,specs)
    assert ('Drive C',f'Example ({identity})') in rows
    assert any(key=='Disk Speed' and f'{band} performance' in value for key,value in rows)
    f=ReportFormatter()
    text=' '.join(f._format_storage_health_comprehensive(specs))
    assert f'Example ({identity})' in text
    assert f'{band} performance' in text
    assert 'cached read corrected' in text
    issue=f._build_drive_speed_issue(specs)
    if identity == 'NVMe SSD':
        assert '(NVMe SSD) measured below the SSD service threshold' in issue
    else:
        assert issue is None
    assert not any(x in text+str(issue) for x in ('HDD/Slow','SATA SSD performance','NVMe SSD performance'))


def test_real_boot_collector_and_dialog_do_not_infer_drive_type(monkeypatch):
    import json
    import subprocess
    import panels
    from diagnostics.advanced_health import collect_boot_time
    monkeypatch.setattr(subprocess,'CREATE_NO_WINDOW',0,raising=False)
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:NS(returncode=0,stdout=json.dumps({'BootTimeMs':10000})))
    result=collect_boot_time()
    assert result['classification'] == 'Excellent (boot performance)'
    rows=[]
    monkeypatch.setattr(panels,'DetailDialog',lambda *a,**kw:NS(add_row=lambda *a,**kw:rows.append(a),exec=lambda:None))
    SystemInfoPanel._detail_boot_time(NS(window=lambda:None),result)
    assert any('Excellent' in str(row) for row in rows)
    assert not any(word in str(rows)+str(result) for word in ('NVMe','SSD','HDD'))
