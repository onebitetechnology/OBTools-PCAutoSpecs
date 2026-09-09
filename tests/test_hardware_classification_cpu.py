import pytest
import json
from hardware_classification import classify_intel_cpu, normalize_cpu_clocks


@pytest.mark.parametrize('name,gen,num', [
    ('Intel Core i5-6300U', '6th Gen', 6),
    ('Intel Core i7-1165G7', '11th Gen', 11),
    ('Intel Core i7-1195G7', '11th Gen', 11),
    ('Intel Core i7-1255U', '12th Gen', 12),
    ('13th Gen Intel Core i7-1355U', '13th Gen', 13),
    ('Intel Core i9-14900HX', '14th Gen', 14),
    ('Intel Core Ultra 9 185H', 'Core Ultra Series 1', None),
    ('Intel(R) Core(TM) Ultra 7 155H', 'Core Ultra Series 1', None),
    ('Intel Core i7 processor', 'Unknown', None),
    ('12th Gen Intel Core i7-1355U', '12th Gen', 12),
    ('Intel Core i7-99999Z', 'Unknown', None),
])
def test_identity(name, gen, num):
    result = classify_intel_cpu(name)
    assert result.generation == gen
    assert result.generation_number == num
    expected = 'Unknown — verify manually' if gen == 'Unknown' else (
        'Windows 10 only' if num and num < 8 else 'Windows 11 compatible')
    assert result.windows_compatibility == expected
    assert result.family == ('Intel Core Ultra' if 'Ultra' in name else 'Intel Core')


def test_clock_provenance_and_duplicates():
    result = normalize_cpu_clocks(authoritative_base_mhz=2600, authoritative_boost_mhz=4400,
                                  current_mhz=2600, wmi_max_mhz=4400)
    assert result.display_parts() == ('Base: 2.60 GHz', 'Boost: 4.40 GHz')
    result = normalize_cpu_clocks(authoritative_base_mhz=None, authoritative_boost_mhz=None,
                                  current_mhz=2610, wmi_max_mhz=1700)
    assert result.display_parts() == ('Current: 2.61 GHz', 'WMI max: 1.70 GHz')


def test_invalid_clocks():
    result = normalize_cpu_clocks(authoritative_base_mhz=-1, authoritative_boost_mhz=0,
                                  current_mhz=None, wmi_max_mhz=None)
    assert result.display_parts() == ()


def test_audited_identity_fixture(fixture_dir):
    from dataclasses import asdict
    for case in json.loads((fixture_dir / 'cpu_identity_cases.json').read_text(encoding='utf-8')):
        name = case.pop('name')
        assert asdict(classify_intel_cpu(name)) == case
