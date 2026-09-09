import pytest
from report_formatter import ReportFormatter


@pytest.mark.parametrize('identity',['NVMe SSD','SATA SSD','SSD','HDD','USB','Virtual Disk','Unknown'])
def test_normalized_identity_wins_every_report(identity):
    f = ReportFormatter()
    drive = dict(model='Example',physical_type=identity,friendly_type='SATA SSD',size_gb=500,status='Unknown')
    assert f._classify_drive_type(drive) == identity
    text = ' '.join(f._format_storage_health_comprehensive({'StorageHealth':[drive]}))
    assert f'Example ({identity})' in text
    overview = f._summarize_storage_line('Drive C: 500GB total, 100 GB free (80% used) - Example (HDD)',[drive])
    assert f'Example ({identity})' in overview
    assert '(HDD)' not in overview or identity == 'HDD'


def test_legacy_saved_identity():
    assert ReportFormatter._classify_drive_type({'friendly_type':'SATA SSD'}) == 'SATA SSD'


def test_ambiguous_benchmark_drive_is_not_guessed_by_type():
    f = ReportFormatter()
    drives = [dict(model=m,physical_type='SSD') for m in ['One','Two']]
    assert f._find_drive_for_disk_speed({'StorageHealth':drives,'Storage':'Drive C: 500GB (SSD)'}) is None
    drives[1]['drive_letters'] = ['C']
    assert f._find_drive_for_disk_speed({'StorageHealth':drives}) is drives[1]
