import pytest
from hardware_classification import classify_drive_identity, assess_drive_performance


def test_sanitized_fixture_contract(fixture_dir):
    import json
    for case in json.loads((fixture_dir / 'storage_identity_cases.json').read_text()):
        assert classify_drive_identity(case['evidence']).physical_type == case['physical_type']


@pytest.mark.parametrize('evidence,expected,source', [
    ({'windows_media_type':'Fixed hard disk media','smart_bus_type':'NVMe'},'NVMe SSD','smart'),
    ({'smart_bus_type':'SATA','smart_media_type':'Solid State'},'SATA SSD','smart'),
    ({'smart_bus_type':'SATA','smart_media_type':'Hard Disk'},'HDD','smart'),
    ({'model':'SanDisk X600','smart_bus_type':'SATA','smart_media_type':'SSD'},'SATA SSD','smart'),
    ({'smart_bus_type':'RAID','smart_media_type':'SSD'},'SSD','smart'),
    ({'model':'Microsoft Virtual Disk','smart_media_type':'SSD'},'Virtual Disk','model'),
    ({'model':'VMware disk'},'Virtual Disk','model'),
    ({'model':'VBOX HARDDISK'},'Virtual Disk','model'),
    ({'windows_bus_type':'USB','model':'Example NVMe SSD'},'USB','windows'),
    ({'windows_bus_type':'SATA','windows_media_type':'Fixed hard disk media'},'Unknown','unknown'),
    ({'model':'Example SSD'},'SSD','model'),
    ({'available_spare':100},'Unknown','unknown'),
    ({'smart_available_spare':100},'NVMe SSD','smart'),
    ({'status':'Failed','health_percent':None},'Unknown','unknown'),
    ({'smart_bus_type':'Unknown','windows_bus_type':'NVMe'},'NVMe SSD','windows'),
    ({'smart_bus_type':'Unspecified','windows_bus_type':'SATA','windows_media_type':'SSD','model':'Example HDD'},'SATA SSD','windows'),
    ({'windows_media_type':'HDD','model':'Example SSD'},'SSD','model'),
    ({'windows_media_type':'Hard Disk','windows_bus_type':'SATA','model':'Example NVMe SSD'},'NVMe SSD','model'),
    ({'smart_media_type':'HDD','model':'Example SSD'},'HDD','smart'),
])
def test_identity(evidence, expected, source):
    result = classify_drive_identity(evidence)
    assert (result.physical_type, result.evidence_source) == (expected, source)


@pytest.mark.parametrize('read,band,severity', [
    (2001,'Very fast','success'),(2000,'Fast','success'),(401,'Fast','success'),
    (400,'Standard','info'),(100,'Standard','info'),(99,'Slow','warning'),
    (0,'Unavailable','unavailable'),(-1,'Unavailable','unavailable'),
    (None,'Unavailable','unavailable'),(float('nan'),'Unavailable','unavailable'),
])
def test_performance(read, band, severity):
    result = assess_drive_performance(read_mb_s=read, write_mb_s=None)
    assert (result.band, result.severity) == (band, severity)
