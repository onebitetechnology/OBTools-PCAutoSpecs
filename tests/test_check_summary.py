import pytest
from diagnostics.check_summary import CheckRecord, normalize_check_outcome, summarize_check_records

@pytest.mark.parametrize('value,expected', [({'status':'ok'},'passed'), (42,'passed'), (None,'unavailable'), ({},'unavailable'), ({'status':'detected'},'unavailable'), ({'status':'critical'},'failed'), ({'status':'failed'},'failed'), ({'status':'error'},'failed'), ({'status':'cancelled'},'skipped'), ({'status':'skipped'},'skipped')])
def test_normalize(value, expected):
    assert normalize_check_outcome(value) == expected

def test_counts():
    records = [CheckRecord(str(i), str(i), state) for i,state in enumerate(['passed','failed','unavailable','skipped'])]
    counts = summarize_check_records(records)
    assert (counts.registered, counts.attempted, counts.passed, counts.failed, counts.unavailable, counts.skipped) == (4,3,1,1,1,1)
    assert summarize_check_records([]).registered == 0
    with pytest.raises(ValueError):
        summarize_check_records(records + records)
