from diagnostics.thermal_summary import summarize_cpu_temperature, cpu_temperature_rows, cpu_temperature_issues
from report_formatter import ReportFormatter


def make_summary(ramp=(55,72,95),load=(74,75,74,75),aborted=False,evidence=None):
    return dict(status='ok', **summarize_cpu_temperature(ramp_samples=ramp,load_samples=load,
        thermal_limit_c=100,aborted=aborted,independent_throttle_detected=evidence))


def test_transient_is_not_load_or_critical():
    summary=make_summary()
    rows=cpu_temperature_rows(summary)
    assert rows[0][0] == 'Temp — Load'
    assert rows[0][1].startswith('75°C')
    assert 'possible throttling' in rows[0][1]
    assert ('Transient ramp peak','95°C','warning') in rows
    specs={'CPUDetails':{'generation':'13th Gen'},'AdvancedHealth':{'cpu_load_temp':summary}}
    formatter=ReportFormatter()
    for render in (formatter._format_cpu_section, formatter._format_hardware_config):
        text=' '.join(render(specs))
        assert 'Load:</strong> 75°C' in text
        assert 'Transient ramp peak:</strong> 95°C' in text
        assert 'throttling detected' not in text
    assert not any('CPU Temp' in s for s in formatter._get_critical_issues_list(specs))
    assert not any('CPU Temp' in s for s in formatter._format_critical_issues(specs))


def test_abort_has_no_invented_load():
    summary=make_summary(ramp=(100,),load=(),aborted=True)
    assert 'Unavailable' in cpu_temperature_rows(summary)[0][1]
    assert '100°C' in cpu_temperature_issues(summary)[0]


def test_hot_boundary_and_confirmation():
    assert 'HIGH' in cpu_temperature_issues(make_summary(load=(90,)))[0]
    rows=cpu_temperature_rows(make_summary(evidence=True))
    assert 'throttling confirmed' in rows[0][1]


def test_actual_cpu_panel():
    from types import SimpleNamespace
    from panels import SystemInfoPanel
    rows=[]
    section=SimpleNamespace(clear_dynamic=lambda:None,set_row_value=lambda *a:None,
        add_info_row=lambda label,value,**kwargs:rows.append((label,value)))
    SystemInfoPanel._update_cpu(SimpleNamespace(_sec_cpu=section),
        {'CPUDetails':{'generation':'13th Gen'},'AdvancedHealth':{'cpu_load_temp':make_summary()}})
    assert any(label == 'Temp — Load' and value.startswith('75°C') for label,value in rows)
    assert ('Transient ramp peak','95°C') in rows


def test_clock_evidence_survives_renderers():
    from types import SimpleNamespace
    from panels import SystemInfoPanel
    rows=[]
    section=SimpleNamespace(clear_dynamic=lambda:None,set_row_value=lambda *a:None,
        add_info_row=lambda label,value,**kwargs:rows.append((label,value)))
    specs={'CPU':'Intel Core i7-1355U | Current: 2.61 GHz | WMI max: 1.70 GHz (10C/12T)',
           'CPUDetails':{'generation':'13th Gen'}}
    SystemInfoPanel._update_cpu(SimpleNamespace(_sec_cpu=section), specs)
    assert ('Current Clock','2.61 GHz') in rows
    assert ('WMI max Clock','1.70 GHz') in rows
    formatter=ReportFormatter()
    for render in (formatter._format_cpu_section,formatter._format_hardware_config):
        text=' '.join(render(specs))
        assert 'WMI max Clock:</strong> 1.70 GHz' in text
        assert 'Current Clock:</strong> 2.61 GHz' in text
        assert 'Boost' not in text
