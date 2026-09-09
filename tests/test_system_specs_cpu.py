from types import SimpleNamespace
import pytest
import system_specs
from hardware_classification import classify_intel_cpu


@pytest.mark.parametrize('name', ['Intel Core i5-6300U', 'Intel Core i7-1165G7',
    'Intel Core i7-1195G7', 'Intel Core i7-1255U', '13th Gen Intel Core i7-1355U',
    'Intel Core i9-14900HX', 'Intel Core Ultra 9 185H', 'Intel Core i7 processor'])
def test_integrated_identity(name):
    details = system_specs._get_cpu_enhanced_details(name)
    identity = classify_intel_cpu(name)
    assert details['generation'] == identity.generation
    assert details['windows_compatibility'] == identity.windows_compatibility


def test_exact_details_preserved():
    details = system_specs._get_cpu_enhanced_details('Intel Core i9-9900K')
    assert {k: details[k] for k in ('architecture','year','socket','max_ram_speed','tdp','upgrade_path')} == {
        'architecture':'Coffee Lake Refresh','year':2018,'socket':'LGA1151',
        'max_ram_speed':'DDR4-2666','tdp':95,'upgrade_path':['i9-9900KS']}


@pytest.mark.parametrize('base,boost,current,maximum,expected', [
    (2600,4400,2600,4400,' | Base: 2.60 GHz | Boost: 4.40 GHz'),
    (None,None,2610,1700,' | Current: 2.61 GHz | WMI max: 1.70 GHz')])
def test_collected_clock_labels(monkeypatch,base,boost,current,maximum,expected):
    values = dict(Name='CPU',NumberOfCores=4,NumberOfLogicalProcessors=8,
                  MaxClockSpeed=maximum,CurrentClockSpeed=current)
    cpu = SimpleNamespace(Properties_=lambda name: SimpleNamespace(Value=values[name]))
    monkeypatch.setattr(system_specs,'_query_com_wmi',lambda *a: SimpleNamespace(Count=1,ItemIndex=lambda i:cpu))
    monkeypatch.setattr(system_specs,'_get_base_clock_from_registry',lambda name:base)
    monkeypatch.setattr(system_specs,'_parse_cpu_boost_speed',lambda name:boost)
    assert system_specs._get_cpu_info(object()) == 'CPU'+expected+' (4C/8T)'


def test_unknown_registry_clock_is_not_base(monkeypatch):
    monkeypatch.setattr(system_specs.platform,'system',lambda:'Windows')
    assert system_specs._get_base_clock_from_registry('Intel Core i7-1355U') is None
