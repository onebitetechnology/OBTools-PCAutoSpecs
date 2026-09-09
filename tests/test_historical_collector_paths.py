import json
import sys
from types import SimpleNamespace as NS
import pytest
from diagnostics import advanced_health as health

@pytest.mark.parametrize('available,drivers', [(None,[]),({},[]),({'DriverTitles':'Fixture driver'},['Fixture driver']),({'DriverTitles':['Fixture driver'],'OptionalTitles':'Fixture optional'},['Fixture driver'])])
def test_available_updates_driver_titles_offline(monkeypatch,available,drivers):
    values=iter([None,None,None,available])
    monkeypatch.setattr(health,'_run_powershell_json',lambda *a,**kw:next(values))
    result=health.collect_windows_update_health()
    assert result['status']=='ok'
    assert result['driver_update_titles']==drivers

@pytest.mark.parametrize('raw,expected', [([],0),({'Name':'Fixture webcam','Status':'OK'},1),([{'Name':'Fixture webcam','Status':'OK'}],1)])
def test_cameras_without_opencv_offline(monkeypatch,raw,expected):
    monkeypatch.setattr(health.subprocess,'run',lambda *a,**kw:NS(returncode=0,stdout=json.dumps(raw)))
    monkeypatch.setitem(sys.modules,'cv2',None)
    monkeypatch.setattr(health,'_can_auto_install_vendor_deps',lambda:False)
    result=health.collect_webcam_info()
    assert len(result['cameras'])==expected
    assert result['status']==('unavailable' if expected else 'none')
