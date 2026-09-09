from types import SimpleNamespace as NS
import pytest
import system_specs as specs


@pytest.mark.parametrize('model,bus,smart,expected,source', [
    ('Example','SATA',{'bus_type':'NVMe'},'NVMe SSD','smart'),
    ('SanDisk X600','SATA',{'bus_type':'SATA','media_type':'SSD'},'SATA SSD','smart'),
    ('Example','RAID',{'bus_type':'RAID','media_type':'SSD'},'SSD','smart'),
    ('Example','SATA',{'media_type':'HDD'},'HDD','smart'),
    ('External','USB',None,'USB','windows'),
    ('VMware Virtual Disk','SATA',None,'Virtual Disk','model'),
    ('Example','SATA',None,'Unknown','unknown'),
])
def test_real_collection_and_overview(monkeypatch,model,bus,smart,expected,source):
    values = dict(Model=model,Size=500*1024**3,Index=0,MediaType='Fixed hard disk media',InterfaceType='SCSI')
    disk = NS(Properties_=lambda key:NS(Value=values[key]))
    monkeypatch.setattr(specs,'_query_com_wmi',lambda *a:NS(Count=1,ItemIndex=lambda i:disk))
    monkeypatch.setattr(specs,'_should_ignore_running_app_drive',lambda:False)
    monkeypatch.setattr(specs,'_get_disk_bus_type',lambda i:bus)
    monkeypatch.setattr(specs,'_get_disk_smart_structured',lambda i:smart)
    monkeypatch.setattr(specs,'_get_disk_drive_letters',lambda i:['C','D'])
    monkeypatch.setattr(specs.subprocess,'run',lambda *a,**k:NS(stdout='GPT'))
    drives = specs._get_storage_health_structured(object())
    assert len(drives) == 1
    drive = drives[0]
    assert (drive['physical_type'],drive['friendly_type'],drive['classification_source']) == (expected,expected,source)
    assert drive['windows_media_type'] == 'Fixed hard disk media'
    if smart:
        assert drive.get('smart_bus_type') == smart.get('bus_type')
    monkeypatch.setattr(specs.psutil,'disk_partitions',lambda:[NS(opts='',mountpoint=x+':\\',fstype='NTFS') for x in ['C','D']])
    monkeypatch.setattr(specs.psutil,'disk_usage',lambda p:NS(total=200*1024**3,free=100*1024**3,used=100*1024**3))
    overview = specs._get_storage_info(object(),drives)
    assert overview.count(f'({expected})') == 2
    assert 'Fixed hard disk media' not in overview
