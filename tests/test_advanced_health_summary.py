import sys
from types import SimpleNamespace as NS
import pytest
from diagnostics import advanced_health as health

KEYS = {'event_viewer','windows_update','defender','temperatures','startup_impact','device_manager','power_plan','boot_time','wifi','webcam','disk_speed','cpu_load_temp','memory_temp_c','gpu_load_temp'}

@pytest.fixture
def collectors(monkeypatch):
    names = ['collect_event_viewer_summary','collect_windows_update_health','collect_defender_status','collect_temperatures','collect_startup_impact','collect_device_manager_errors','collect_active_power_plan','collect_boot_time','collect_wifi_info','collect_webcam_info','collect_disk_speed_test','collect_cpu_temp_under_load','collect_gpu_temp_under_load']
    for name in names:
        monkeypatch.setattr(health,name,lambda *a,**kw:{'status':'ok'})
    monkeypatch.setattr(health,'_run_with_timeout',lambda f,*a:f())
    monkeypatch.setattr(health,'_collect_memory_temp_lhm',lambda:42)
    monkeypatch.setattr(health,'_query_nvidia_gpu_name',lambda:'Fixture dedicated GPU')
    monkeypatch.setitem(sys.modules,'wmi',NS(WMI=lambda:NS(Win32_VideoController=lambda:[])))

def test_all_pass(collectors):
    logs=[]
    result=health.collect_advanced_health_summary(log_callback=logs.append)
    assert set(health.ADVANCED_CHECK_REGISTRY) == KEYS == {k for k in result if not k.startswith('_')}
    assert result['_check_summary'] == dict(registered=14,attempted=14,passed=14,failed=0,unavailable=0,skipped=0)
    assert logs[-1] == 'Advanced health: 14/14 attempted checks passed; 0 failed; 0 unavailable; 0 skipped'

def test_mixed_and_metadata(collectors,monkeypatch):
    monkeypatch.setattr(health,'collect_defender_status',lambda:{'status':'failed'})
    monkeypatch.setattr(health,'collect_cpu_temp_under_load',lambda **kw:{'status':'cancelled'})
    monkeypatch.setattr(health,'_collect_memory_temp_lhm',lambda:None)
    monkeypatch.setattr(health,'collect_device_manager_errors',lambda:{'status':'ok','devices':[{'status':'ok'}]})
    result=health.collect_advanced_health_summary(skip_categories={'network','display'})
    assert result['_check_summary'] == dict(registered=14,attempted=11,passed=9,failed=1,unavailable=1,skipped=3)
    assert result['_device_manager_errors']

def test_skipped_categories(collectors):
    result=health.collect_advanced_health_summary(skip_categories={'event_logs','windows_update','defender','startup_items','device_manager','power_boot','network','display','cpu','gpu','ram','storage'})
    assert result['_check_summary'] == dict(registered=14,attempted=0,passed=0,failed=0,unavailable=0,skipped=14)

def test_no_dedicated_gpu(collectors,monkeypatch):
    monkeypatch.setattr(health,'_query_nvidia_gpu_name',lambda:None)
    result=health.collect_advanced_health_summary()
    assert result['gpu_load_temp']['status']=='skipped'
    assert result['_check_summary']['attempted']==13

def test_dynamic_failures_remain_registered(collectors,monkeypatch):
    def unavailable(*args,**kwargs):
        raise RuntimeError('fixture unavailable')
    for name in ('collect_disk_speed_test','collect_cpu_temp_under_load','collect_gpu_temp_under_load','_collect_memory_temp_lhm'):
        monkeypatch.setattr(health,name,unavailable)
    result=health.collect_advanced_health_summary()
    assert result['_check_summary']==dict(registered=14,attempted=14,passed=10,failed=0,unavailable=4,skipped=0)
