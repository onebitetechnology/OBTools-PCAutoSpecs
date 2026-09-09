import pytest
import json
from diagnostics.thermal_summary import summarize_cpu_temperature, should_show_ramp_peak


@pytest.mark.parametrize('ramp,load,evidence,expected', [
    ([55,72,95], [74,75,74,75], None, 'suspected'),
    ([75], [74,75], None, 'unavailable'),
    ([95], [75], False, 'none'),
    ([75], [75], True, 'confirmed'),
    ([], [], None, 'unavailable'),
    ([95], [], None, 'unavailable'),
])
def test_summary(ramp, load, evidence, expected):
    result = summarize_cpu_temperature(ramp_samples=ramp, load_samples=load,
        thermal_limit_c=100, aborted=False, independent_throttle_detected=evidence)
    assert result['throttling_evidence'] == expected
    assert result['load_peak_temp_c'] == (max(load) if load else None)
    assert result['ramp_peak_temp_c'] == (max(ramp) if ramp else None)
    assert result['peak_temp_c'] == result['overall_peak_temp_c']
    if len(load) == 4:
        assert result['load_median_temp_c'] == 74.5
        assert should_show_ramp_peak(result)


@pytest.mark.parametrize('ramp,load,show', [(80,75,True),(79,75,False),(90,92,True)])
def test_ramp_visibility(ramp,load,show):
    assert should_show_ramp_peak({'ramp_peak_temp_c': ramp,'load_peak_temp_c': load}) is show


def test_audited_thermal_fixture(fixture_dir):
    for case in json.loads((fixture_dir / 'cpu_thermal_cases.json').read_text(encoding='utf-8')):
        result = summarize_cpu_temperature(ramp_samples=case['ramp'],load_samples=case['load'],
            thermal_limit_c=100,aborted=case['aborted'],independent_throttle_detected=case['evidence'])
        assert result['throttling_evidence'] == case['expected']
        assert result['aborted'] == case['aborted']
