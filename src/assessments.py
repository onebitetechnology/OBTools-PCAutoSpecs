"""
Unified Assessment System
Single source of truth for severity levels, health assessments, and recommendations.
All surfaces (GUI, logs, reports) derive from these functions.
"""

# ============================================================================
# SEVERITY LEVELS - Universal across all diagnostics
# ============================================================================

SEVERITY_OK = "OK"
SEVERITY_NOTICE = "NOTICE"
SEVERITY_WARN = "WARN"
SEVERITY_CAUTION = "CAUTION"
SEVERITY_CRITICAL = "CRITICAL"


# ============================================================================
# BATTERY HEALTH ASSESSMENT
# ============================================================================

def assess_battery_health(health_percent, cycle_count=None):
    """
    Unified battery health assessment.
    
    Args:
        health_percent: Battery health percentage (0-100)
        cycle_count: Optional battery cycle count
        
    Returns:
        dict with keys: severity, label, description, recommendation
    """
    if health_percent >= 90:
        return {
            'severity': SEVERITY_OK,
            'label': 'Excellent',
            'description': 'Battery in excellent condition',
            'recommendation': 'No action needed',
            'color_category': 'success'
        }
    elif health_percent >= 80:
        return {
            'severity': SEVERITY_OK,
            'label': 'Good',
            'description': 'Battery in good condition',
            'recommendation': 'Normal use, no concerns',
            'color_category': 'success'
        }
    elif health_percent >= 70:
        return {
            'severity': SEVERITY_NOTICE,
            'label': 'Fair',
            'description': 'Moderate wear detected',
            'recommendation': 'Monitor over next 6-12 months',
            'color_category': 'warning'
        }
    elif health_percent >= 50:
        return {
            'severity': SEVERITY_WARN,
            'label': 'Degraded',
            'description': 'Significant wear, reduced capacity',
            'recommendation': 'Plan replacement within 3-6 months',
            'color_category': 'warning'
        }
    else:
        return {
            'severity': SEVERITY_CRITICAL,
            'label': 'Poor',
            'description': 'Critically degraded battery',
            'recommendation': 'Replacement recommended immediately',
            'color_category': 'error'
        }


# ============================================================================
# SMART / STORAGE HEALTH ASSESSMENT
# ============================================================================

def assess_smart_status(smart_data, drive_model='Unknown Drive'):
    """
    Unified SMART health assessment.
    
    Args:
        smart_data: Dictionary with SMART attributes
        drive_model: Drive model name
        
    Returns:
        dict with keys: severity, label, description, recommendation, issues[]
    """
    if not smart_data:
        return {
            'severity': SEVERITY_NOTICE,
            'label': 'No Data',
            'description': 'SMART data unavailable — could not query drive',
            'recommendation': 'Verify drive health manually if concerned',
            'issues': [],
            'color_category': 'info'
        }
    
    status = smart_data.get('status', 'Unknown')
    health_percent = smart_data.get('health_percent')
    
    # Special cases first
    if status == 'N/A':
        return {
            'severity': SEVERITY_OK,
            'label': 'N/A',
            'description': 'SMART not applicable (USB device)',
            'recommendation': 'SMART data not reliable for USB devices',
            'issues': [],
            'color_category': 'info'
        }
    
    if status in ('Unknown', 'Error') or health_percent is None:
        # Check if the drive's own health assessment genuinely failed
        # (smartctl: "SMART overall-health self-assessment test result: FAILED")
        # vs. our query simply failing to complete (Error|0|Unknown|SMART_FAILED sentinel)
        full_output = smart_data.get('full_smart_output', '') or ''
        drive_reported_failure = 'self-assessment test result: FAILED' in full_output

        if drive_reported_failure:
            return {
                'severity': SEVERITY_WARN,
                'label': 'SMART Failed',
                'description': 'Drive self-assessment returned FAILED',
                'recommendation': 'Back up data immediately — drive may be failing',
                'issues': ['Drive reported SMART failure in self-assessment test'],
                'color_category': 'warning'
            }
        return {
            'severity': SEVERITY_NOTICE,
            'label': 'SMART unavailable',
            'description': 'Drive detected but SMART health data could not be read',
            'recommendation': 'Verify drive health manually if concerned (e.g. CrystalDiskInfo)',
            'issues': [],
            'color_category': 'info'
        }
    
    # Health-based assessment
    issues = []
    
    # Check for critical SMART attributes
    if smart_data.get('reallocated_sectors', 0) > 0:
        issues.append(f"{smart_data['reallocated_sectors']} reallocated sectors")
    if smart_data.get('pending_sectors', 0) > 0:
        issues.append(f"{smart_data['pending_sectors']} pending sectors")
    if smart_data.get('uncorrectable_errors', 0) > 0:
        issues.append(f"{smart_data['uncorrectable_errors']} uncorrectable errors")
    
    # High runtime
    power_on_hours = smart_data.get('power_on_hours', 0)
    if power_on_hours > 40000:  # ~4.5 years continuous
        issues.append(f"High runtime: {power_on_hours}h ({power_on_hours//8760} years)")
    
    # Temperature
    temp = smart_data.get('temperature')
    if temp and temp > 55:
        issues.append(f"Elevated temperature: {temp}°C")
    
    # Final assessment
    if health_percent >= 85:
        if issues:
            return {
                'severity': SEVERITY_NOTICE,
                'label': 'Good',
                'description': f'{health_percent}% health, minor concerns',
                'recommendation': 'Monitor SMART status monthly',
                'issues': issues,
                'color_category': 'success'
            }
        return {
            'severity': SEVERITY_OK,
            'label': 'Healthy',
            'description': f'{health_percent}% health',
            'recommendation': 'No action needed',
            'issues': [],
            'color_category': 'success'
        }
    elif health_percent >= 70:
        return {
            'severity': SEVERITY_WARN,
            'label': 'Caution',
            'description': f'{health_percent}% health, wear detected',
            'recommendation': 'Backup data, monitor closely, plan replacement',
            'issues': issues,
            'color_category': 'warning'
        }
    else:
        return {
            'severity': SEVERITY_CRITICAL,
            'label': 'Critical',
            'description': f'{health_percent}% health, high risk of failure',
            'recommendation': 'BACKUP DATA NOW - Replace drive immediately',
            'issues': issues,
            'color_category': 'error'
        }


# ============================================================================
# DRIVER STATUS ASSESSMENT
# ============================================================================

def assess_driver_status(driver_date_str=None, has_device_errors=False, driver_version=None, is_oem=False):
    """
    Unified driver status assessment - workstation-aware.
    
    Recognizes that OEM/workstation drivers (Quadro, Precision, ZBook, ThinkPad P)
    prioritize stability over recency. Old ≠ bad for certified workstation drivers.
    
    Args:
        driver_date_str: Driver date string (e.g. "07/05/2020")
        has_device_errors: Whether Device Manager shows errors (Boolean from WMI ConfigManagerErrorCode)
        driver_version: Driver version string (optional, for display)
        is_oem: Whether driver is OEM-signed (Acer, Dell, HP, Lenovo variants)
        
    Returns:
        dict with keys: severity, label, description, age_note
    """
    # Priority 1: Device Manager errors - always critical
    if has_device_errors:
        return {
            'severity': SEVERITY_CRITICAL,
            'label': 'Device Errors',
            'description': 'Device Manager reports errors - driver malfunction',
            'age_note': None,
            'color_category': 'error'
        }
    
    # Priority 2: No errors = working driver
    # Parse age for informational display only (not a verdict)
    age_note = None
    age_years = None
    
    if driver_date_str:
        try:
            from datetime import datetime
            driver_date = datetime.strptime(driver_date_str, "%m/%d/%Y")
            age_years = (datetime.now() - driver_date).days / 365.25
            
            # Format age note (neutral, informational)
            if age_years >= 1:
                age_note = f"Released {driver_date_str} (~{int(age_years)} year{'s' if age_years >= 2 else ''} ago)"
            else:
                age_note = f"Released {driver_date_str} (recent)"
        except:
            pass
    
    # Determine label based on driver provenance
    if is_oem:
        # OEM-certified drivers: stability prioritized, age irrelevant
        return {
            'severity': SEVERITY_OK,
            'label': 'Installed - OEM Certified',
            'description': 'OEM-signed driver, stable, no device errors',
            'age_note': age_note,
            'color_category': 'success'
        }
    else:
        # Standard assessment: working = good, age is context only
        return {
            'severity': SEVERITY_OK,
            'label': 'Installed - Working',
            'description': 'Driver stable, no device errors detected',
            'age_note': age_note,
            'color_category': 'success'
        }


# ============================================================================
# SYSTEM UPTIME ASSESSMENT
# ============================================================================

def assess_uptime(uptime_days):
    """
    Assess system uptime health.
    
    Args:
        uptime_days: System uptime in days
        
    Returns:
        dict with keys: severity, label, description, recommendation
    """
    if uptime_days < 7:
        return {
            'severity': SEVERITY_OK,
            'label': 'Normal',
            'description': f'{uptime_days} days uptime',
            'recommendation': None
        }
    elif uptime_days < 30:
        return {
            'severity': SEVERITY_NOTICE,
            'label': 'Long',
            'description': f'{uptime_days} days uptime',
            'recommendation': 'Consider restart for Windows updates'
        }
    else:
        return {
            'severity': SEVERITY_WARN,
            'label': 'Extended',
            'description': f'{uptime_days} days uptime',
            'recommendation': 'Restart recommended for updates and stability'
        }


# ============================================================================
# MEMORY USAGE ASSESSMENT
# ============================================================================

def assess_memory_usage(percent_used):
    """
    Assess memory usage level.
    
    Args:
        percent_used: Memory usage percentage
        
    Returns:
        dict with keys: severity, label, color_category
    """
    if percent_used < 60:
        return {
            'severity': SEVERITY_OK,
            'label': 'Normal',
            'color_category': 'success'
        }
    elif percent_used < 80:
        return {
            'severity': SEVERITY_NOTICE,
            'label': 'Moderate',
            'color_category': 'warning'
        }
    elif percent_used < 95:
        return {
            'severity': SEVERITY_WARN,
            'label': 'High',
            'color_category': 'warning'
        }
    else:
        return {
            'severity': SEVERITY_CRITICAL,
            'label': 'Critical',
            'color_category': 'error'
        }

