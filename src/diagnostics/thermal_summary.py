"""Phase-aware CPU temperatures and shared presentation rules."""
from statistics import median


def summarize_cpu_temperature(*, ramp_samples, load_samples, thermal_limit_c,
                              aborted, independent_throttle_detected):
    ramp = round(max(ramp_samples), 1) if ramp_samples else None
    load = round(max(load_samples), 1) if load_samples else None
    peaks = [value for value in (ramp, load) if value is not None]
    overall = max(peaks) if peaks else None
    evidence = 'unavailable'
    if independent_throttle_detected is not None:
        evidence = 'confirmed' if independent_throttle_detected else 'none'
    elif ramp is not None and load is not None and ramp > load + 10:
        evidence = 'suspected'
    return dict(ramp_peak_temp_c=ramp, load_peak_temp_c=load,
                load_median_temp_c=round(median(load_samples), 1) if load_samples else None,
                overall_peak_temp_c=overall,
                peak_temp_c=overall,  # Legacy compatibility only; never the load value.
                thermal_limit_c=thermal_limit_c, aborted=aborted,
                throttling_evidence=evidence)


def should_show_ramp_peak(summary):
    ramp, load = summary.get('ramp_peak_temp_c'), summary.get('load_peak_temp_c')
    return ramp is not None and (ramp >= 90 or (load is not None and ramp >= load + 5))


def cpu_temperature_rows(summary):
    """Return (label, text, severity) rows shared by GUI and HTML reports."""
    rows = []
    if summary.get('status') == 'cancelled':
        rows.append(('Temp — Load', 'Cancelled by tech', 'warning'))
    elif summary.get('status') == 'ok':
        peak = summary.get('load_peak_temp_c')
        if peak is not None:
            label, severity = ('Hot', 'error') if peak >= 90 else ('Warm', 'warning') if peak >= 75 else ('Normal', 'success')
            suffix = ''
            evidence = summary.get('throttling_evidence')
            if evidence == 'suspected':
                suffix += ' — possible throttling'
            elif evidence == 'confirmed':
                suffix += ' — throttling confirmed'
            if summary.get('sensor'):
                suffix += f" — {summary['sensor']}"
            rows.append(('Temp — Load', f'{peak:.0f}°C ({label}){suffix}', severity))
        else:
            rows.append(('Temp — Load', 'Unavailable — no load samples', 'warning'))
        if summary.get('aborted') and summary.get('overall_peak_temp_c') is not None:
            rows.append(('Thermal limit', f"Hit at {summary['overall_peak_temp_c']:.0f}°C — test aborted", 'error'))
    if should_show_ramp_peak(summary):
        rows.append(('Transient ramp peak', f"{summary['ramp_peak_temp_c']:.0f}°C", 'warning'))
    return rows


def cpu_temperature_issues(summary):
    if summary.get('status') != 'ok':
        return []
    overall, load = summary.get('overall_peak_temp_c'), summary.get('load_peak_temp_c')
    if summary.get('aborted') and overall is not None:
        return [f'CPU Temp: CRITICAL — thermal limit hit at {overall:.0f}°C (urgent cooling service needed)']
    if load is not None and load >= 90:
        return [f'CPU Temp (Load): HIGH ({load:.0f}°C — cooling service recommended)']
    if summary.get('throttling_evidence') == 'confirmed':
        return ['CPU: throttling confirmed']
    return []
