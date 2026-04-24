"""
Enhanced System Specifications Collector
Gathers comprehensive hardware and software information from the system
Fixed issues: storage duplication, serial number detection, enhanced GPU info, better error handling
"""

import platform
import sys
import logging
import subprocess
import os
import re
import json
from datetime import datetime, timedelta

try:
    import psutil
except ImportError:
    print("Error: psutil is required. Install it with: pip install psutil")
    sys.exit(1)

# Windows-specific imports
if platform.system() == "Windows":
    try:
        import winreg
    except ImportError:
        winreg = None
    
    # COM/WMI access via pywin32 (direct WMI access from Python)
    try:
        import win32com.client
        import pythoncom
        COM_AVAILABLE = True
    except ImportError:
        logging.error("pywin32 not available. This tool requires pywin32 for Windows hardware detection.")
        logging.error("Install with: pip install pywin32")
        COM_AVAILABLE = False
        win32com = None
        pythoncom = None
else:
    winreg = None
    COM_AVAILABLE = False
    win32com = None
    pythoncom = None

# Resolve full path to powershell.exe — Git Bash and some environments don't have it on PATH
_POWERSHELL_EXE = os.path.join(
    os.environ.get('SYSTEMROOT', r'C:\Windows'),
    'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'
)
if not os.path.isfile(_POWERSHELL_EXE):
    _POWERSHELL_EXE = 'powershell.exe'  # fallback to PATH


def _resolve_bundled_smartctl_path():
    """Find smartctl.exe in source or bundled layouts."""
    source_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = []

    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass = os.path.abspath(sys._MEIPASS)
        candidates.extend([
            os.path.join(meipass, 'smartctl.exe'),
            os.path.join(meipass, 'src', 'smartctl.exe'),
        ])

    candidates.extend([
        os.path.join(source_dir, 'smartctl.exe'),
        os.path.join(os.path.dirname(source_dir), 'src', 'smartctl.exe'),
    ])

    seen = set()
    for candidate in candidates:
        normalized = os.path.normpath(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.isfile(normalized):
            return normalized
    return candidates[0] if candidates else 'smartctl.exe'


def _run_smartctl(args, timeout=45):
    """Run smartctl with a hidden console window on Windows."""
    smartctl_path = _resolve_bundled_smartctl_path()
    startupinfo = None
    creationflags = 0
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = subprocess.CREATE_NO_WINDOW

    return subprocess.run(
        [smartctl_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )


def _build_smartctl_device_types(drive_info):
    """Return likely smartctl device types for a drive."""
    if not drive_info:
        return []

    bus_type = str(drive_info.get('bus_type') or '').upper()
    interface = str(drive_info.get('interface') or '').upper()
    friendly_type = str(drive_info.get('friendly_type') or '').upper()

    if bus_type == 'USB' or interface == 'USB':
        return []
    if bus_type == 'RAID' or interface == 'RAID':
        return []
    if 'NVME' in bus_type or 'NVME' in friendly_type:
        return ['nvme', 'sat']
    return ['sat', 'ata']


def _get_computer_system_identity(com_wmi=None):
    """Return basic manufacturer/model identity for OEM-specific checks."""
    manufacturer = ""
    model = ""

    if platform.system() != "Windows":
        return {"manufacturer": manufacturer, "model": model}

    try:
        if com_wmi:
            items = _query_com_wmi(com_wmi, "Win32_ComputerSystem")
            if items and items.Count > 0:
                computer_system = items.ItemIndex(0)
                manufacturer = (computer_system.Properties_("Manufacturer").Value or "").strip()
                model = (computer_system.Properties_("Model").Value or "").strip()
                if manufacturer or model:
                    return {"manufacturer": manufacturer, "model": model}
    except Exception as e:
        logging.debug(f"Failed to get computer identity via COM/WMI: {e}")

    try:
        computer_system = win32com.client.GetObject("winmgmts:root\\cimv2").ExecQuery("SELECT Manufacturer, Model FROM Win32_ComputerSystem")
        for item in computer_system:
            manufacturer = (getattr(item, "Manufacturer", "") or "").strip()
            model = (getattr(item, "Model", "") or "").strip()
            break
    except Exception as e:
        logging.debug(f"Failed to get computer identity via WMI fallback: {e}")

    return {"manufacturer": manufacturer, "model": model}


def _normalize_oem_vendor(manufacturer, model=""):
    """Collapse raw manufacturer strings into a small canonical OEM set."""
    raw = f"{manufacturer or ''} {model or ''}".upper()
    if "LENOVO" in raw:
        return "Lenovo"
    if "DELL" in raw or "ALIENWARE" in raw:
        return "Dell"
    if "HEWLETT-PACKARD" in raw or re.search(r'(^|\s)HP(\s|$)', raw):
        return "HP"
    if "ASUS" in raw or "ASUSTEK" in raw:
        return "ASUS"
    if "ACER" in raw:
        return "Acer"
    if "MSI" in raw or "MICRO-STAR" in raw:
        return "MSI"
    if "MICROSOFT" in raw or "SURFACE" in raw:
        return "Microsoft"
    return ""


_OEM_UPDATE_TOOL_CATALOG = {
    "Lenovo": [
        {
            "name": "Lenovo Vantage",
            "matches": ["lenovo vantage", "commercial vantage"],
            "paths": [],
        },
        {
            "name": "Lenovo System Update",
            "matches": ["lenovo system update"],
            "paths": [
                os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Lenovo", "System Update", "tvsu.exe"),
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Lenovo", "System Update", "tvsu.exe"),
            ],
        },
    ],
    "Dell": [
        {
            "name": "Dell Command | Update",
            "matches": ["dell command | update", "dell command update"],
            "paths": [
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Dell", "CommandUpdate", "dcu-ui.exe"),
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Dell", "CommandUpdate", "dcu-cli.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Dell", "CommandUpdate", "dcu-ui.exe"),
            ],
        },
        {
            "name": "Dell SupportAssist",
            "matches": ["supportassist", "dell supportassist"],
            "paths": [
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Dell", "SupportAssistAgent", "bin", "SupportAssist.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "SupportAssistAgent", "bin", "SupportAssist.exe"),
            ],
        },
    ],
    "HP": [
        {
            "name": "HP Image Assistant",
            "matches": ["hp image assistant"],
            "paths": [
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "HP", "HP Image Assistant", "HPImageAssistant.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "HP", "HP Image Assistant", "HPImageAssistant.exe"),
            ],
        },
        {
            "name": "HP Support Assistant",
            "matches": ["hp support assistant"],
            "paths": [
                os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Hewlett-Packard", "HP Support Framework", "HPSupportAssistant.exe"),
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Hewlett-Packard", "HP Support Framework", "HPSupportAssistant.exe"),
            ],
        },
    ],
}


def _iter_installed_app_display_names():
    """Yield installed application display names from common uninstall registry keys."""
    if platform.system() != "Windows" or not winreg:
        return []

    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )

    names = []
    for hive, root_path in roots:
        try:
            with winreg.OpenKey(hive, root_path) as root_key:
                index = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root_key, index)
                        index += 1
                    except OSError:
                        break

                    try:
                        with winreg.OpenKey(root_key, subkey_name) as subkey:
                            display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            if display_name:
                                names.append(str(display_name).strip())
                    except OSError:
                        continue
        except OSError:
            continue
    return names


def _detect_manufacturer_update_tools(com_wmi=None):
    """Detect OEM-specific driver/BIOS update utilities for refurb workflows."""
    identity = _get_computer_system_identity(com_wmi)
    manufacturer = identity.get("manufacturer", "")
    model = identity.get("model", "")
    vendor = _normalize_oem_vendor(manufacturer, model)

    result = {
        "status": "unknown",
        "vendor": vendor or None,
        "manufacturer": manufacturer,
        "model": model,
        "summary": "Check unavailable",
        "recommended_tools": [],
        "found_tools": [],
        "found_install_names": [],
        "note": "",
    }

    if not vendor or vendor not in _OEM_UPDATE_TOOL_CATALOG:
        result["summary"] = "OEM update tool check not applicable"
        result["note"] = "This check currently focuses on Lenovo, Dell, and HP refurb workflows."
        return result

    catalog = _OEM_UPDATE_TOOL_CATALOG[vendor]
    result["recommended_tools"] = [entry["name"] for entry in catalog]

    installed_names = _iter_installed_app_display_names()
    installed_lower = [name.lower() for name in installed_names]

    found_tools = []
    found_names = []
    for entry in catalog:
        matched_name = None
        for display_name, display_name_lower in zip(installed_names, installed_lower):
            if any(match in display_name_lower for match in entry["matches"]):
                matched_name = display_name
                break
        if not matched_name:
            for path in entry.get("paths", []):
                if path and os.path.isfile(path):
                    matched_name = f"{entry['name']} (detected by executable)"
                    break
        if matched_name:
            found_tools.append(entry["name"])
            found_names.append(matched_name)

    if found_tools:
        result["status"] = "ok"
        result["found_tools"] = found_tools
        result["found_install_names"] = found_names
        result["summary"] = f"Installed — {', '.join(found_tools)}"
        result["note"] = (
            "Manufacturer tool detected. Pending driver/BIOS updates still need to be checked in the vendor utility."
        )
    else:
        result["status"] = "warning"
        result["summary"] = f"Not installed — recommended: {', '.join(result['recommended_tools'])}"
        result["note"] = (
            "Windows Update may miss OEM-specific BIOS, firmware, or driver updates on refurb units."
        )

    return result


def _get_manufacturer_update_tools_summary(com_wmi=None):
    """Compact tuple summary for the Advanced Diagnostics panel."""
    result = _detect_manufacturer_update_tools(com_wmi)
    return (result.get("summary", "Check unavailable"), result.get("status", "unknown"))


def _parse_drive_selftest_status(output):
    """Parse smartctl self-test output into a UI-friendly status dict."""
    text = (output or '').strip()
    lower = text.lower()

    if not text:
        return {
            'status': 'unavailable',
            'summary': 'Extended drive test status unavailable',
        }

    progress_match = re.search(r'(\d+)% of test remaining', text, re.IGNORECASE)
    if 'self-test routine in progress' in lower and progress_match:
        remaining = int(progress_match.group(1))
        completed = max(0, 100 - remaining)
        return {
            'status': 'in_progress',
            'summary': f'In progress — {completed}% complete',
            'percent_complete': completed,
            'percent_remaining': remaining,
        }

    if 'completed without error' in lower:
        return {
            'status': 'passed',
            'summary': 'Passed — completed without error',
        }

    if 'aborted by host' in lower or 'interrupted' in lower:
        return {
            'status': 'cancelled',
            'summary': 'Cancelled or interrupted',
        }

    failed_match = re.search(r'completed:\s*([^\n\r]+)', text, re.IGNORECASE)
    if failed_match:
        reason = failed_match.group(1).strip().rstrip('.')
        return {
            'status': 'failed',
            'summary': f'Failed — {reason}',
        }

    if 'no self-tests have been logged' in lower or 'no self-test has ever been run' in lower:
        return {
            'status': 'not_run',
            'summary': 'Not started',
        }

    return {
        'status': 'unknown',
        'summary': 'Status detected but not fully parsed',
        'raw_output': text,
    }


def get_drive_extended_test_status(drive_info):
    """Return the current extended SMART self-test state for a drive."""
    if platform.system() != "Windows":
        return {'status': 'unavailable', 'summary': 'Only supported on Windows'}

    if not drive_info:
        return {'status': 'unavailable', 'summary': 'Drive information unavailable'}

    drive_type = str(drive_info.get('friendly_type') or '').upper()
    if drive_type != 'HDD':
        return {'status': 'unsupported', 'summary': 'Extended test currently supported for HDDs only'}

    disk_index = drive_info.get('disk_index')
    if disk_index is None:
        return {'status': 'unavailable', 'summary': 'Drive index unavailable'}

    device_types = _build_smartctl_device_types(drive_info)
    if not device_types:
        return {'status': 'unsupported', 'summary': 'SMART self-test not supported for this controller'}

    drive_path = f"/dev/pd{disk_index}"
    last_error = None
    for device_type in device_types:
        try:
            result = _run_smartctl(
                ['-c', '-l', 'selftest', '-d', device_type, drive_path],
                timeout=45,
            )
        except Exception as e:
            last_error = str(e)
            continue

        combined = "\n".join(
            part for part in (result.stdout or '', result.stderr or '') if part
        ).strip()
        if result.returncode in (0, 4) and combined:
            parsed = _parse_drive_selftest_status(combined)
            parsed['device_type'] = device_type
            return parsed
        last_error = combined or f"smartctl exit code {result.returncode}"

    return {
        'status': 'unavailable',
        'summary': 'Unable to read SMART self-test status',
        'reason': last_error or 'smartctl unavailable',
    }


def start_drive_extended_test(drive_info):
    """Start a non-destructive SMART long self-test on an HDD."""
    if platform.system() != "Windows":
        return {'status': 'unavailable', 'summary': 'Only supported on Windows'}

    if not drive_info:
        return {'status': 'unavailable', 'summary': 'Drive information unavailable'}

    drive_type = str(drive_info.get('friendly_type') or '').upper()
    if drive_type != 'HDD':
        return {'status': 'unsupported', 'summary': 'Extended test currently supported for HDDs only'}

    disk_index = drive_info.get('disk_index')
    if disk_index is None:
        return {'status': 'unavailable', 'summary': 'Drive index unavailable'}

    current = get_drive_extended_test_status(drive_info)
    if current.get('status') == 'in_progress':
        return current

    device_types = _build_smartctl_device_types(drive_info)
    if not device_types:
        return {'status': 'unsupported', 'summary': 'SMART self-test not supported for this controller'}

    drive_path = f"/dev/pd{disk_index}"
    last_error = None
    for device_type in device_types:
        try:
            result = _run_smartctl(
                ['-t', 'long', '-d', device_type, drive_path],
                timeout=45,
            )
        except Exception as e:
            last_error = str(e)
            continue

        combined = "\n".join(
            part for part in (result.stdout or '', result.stderr or '') if part
        ).strip()
        if result.returncode in (0, 4):
            minutes_match = re.search(r'Please wait (\d+) minutes', combined, re.IGNORECASE)
            minutes = int(minutes_match.group(1)) if minutes_match else None
            summary = 'Extended HDD test started'
            if minutes:
                summary += f' — estimated {minutes} minutes'
            return {
                'status': 'in_progress',
                'summary': summary,
                'estimated_minutes': minutes,
                'device_type': device_type,
            }
        last_error = combined or f"smartctl exit code {result.returncode}"

    return {
        'status': 'unavailable',
        'summary': 'Could not start extended drive test',
        'reason': last_error or 'smartctl unavailable',
    }


def _normalize_ram_slot_label(device_locator, bank_label, slot_index, used_labels):
    """Prefer the WMI slot label, but guarantee a unique readable slot name."""
    def _clean(value):
        if value is None:
            return ""
        text = str(value).strip()
        return "" if not text or text.lower() == "unknown" else text

    device_locator = _clean(device_locator)
    bank_label = _clean(bank_label)
    fallback = f"DIMM{slot_index + 1}"

    for candidate in (device_locator, bank_label, fallback):
        if candidate and candidate not in used_labels:
            used_labels.add(candidate)
            return candidate

    if device_locator and bank_label:
        combined = f"{device_locator} ({bank_label})"
        if combined not in used_labels:
            used_labels.add(combined)
            return combined

    suffix = 2
    while True:
        candidate = f"{fallback}-{suffix}"
        if candidate not in used_labels:
            used_labels.add(candidate)
            return candidate
        suffix += 1


def _get_event_log_summary():
    """Get Windows Event Log summary (7 days) - Critical and Error events only"""
    try:
        ps_script = '''
        $StartDate = (Get-Date).AddDays(-7)
        $Critical = (Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=1; StartTime=$StartDate} -ErrorAction SilentlyContinue | Measure-Object).Count
        $Errors = (Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=2; StartTime=$StartDate} -ErrorAction SilentlyContinue | Measure-Object).Count
        Write-Output "$Critical|$Errors"
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split('|')
            critical = int(parts[0])
            errors = int(parts[1])
            
            total = critical + errors
            if critical > 0:
                return f"{critical} critical, {errors} error{'s' if errors != 1 else ''}", "critical"
            elif errors > 0:
                return f"{errors} error{'s' if errors != 1 else ''}", "warning"
            else:
                return "Clean (no errors)", "ok"
        
        return "Check unavailable", "unknown"
    except Exception as e:
        logging.debug(f"Failed to get event log: {e}")
        return "Check unavailable", "unknown"


def _get_windows_update_status():
    """Get Windows Update status"""
    try:
        ps_script = '''
        try {
            $UpdateSession = New-Object -ComObject Microsoft.Update.Session
            $UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
            $SearchResult = $UpdateSearcher.Search("IsInstalled=0")
            $PendingCount = $SearchResult.Updates.Count
            
            # Check last successful search time
            $LastSearch = $UpdateSearcher.GetTotalHistoryCount()
            
            Write-Output "$PendingCount"
        } catch {
            Write-Output "ERROR"
        }
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            if output == "ERROR":
                return "Unable to check", "unknown"

            pending = int(output)
            if pending == 0:
                return "Up to date", "ok"
            elif pending < 5:
                s = '' if pending == 1 else 's'
                return f"{pending} update{s} pending", "warning"
            else:
                return f"{pending} updates pending", "critical"

        return "Unable to check", "unknown"
    except Exception as e:
        logging.debug(f"Failed to get Windows Update status: {e}")
        return "Unable to check", "unknown"


def _get_defender_status():
    """Get Windows Defender status"""
    try:
        ps_script = '''
        try {
            $DefenderStatus = Get-MpComputerStatus -ErrorAction Stop
            $RtpEnabled = $DefenderStatus.RealTimeProtectionEnabled
            $DefsOutdated = $DefenderStatus.AntivirusSignatureAge -gt 7
            $DefAge = $DefenderStatus.AntivirusSignatureAge
            
            if ($RtpEnabled -and -not $DefsOutdated) {
                Write-Output "ENABLED|$DefAge"
            } elseif ($RtpEnabled -and $DefsOutdated) {
                Write-Output "OUTDATED|$DefAge"
            } else {
                Write-Output "DISABLED|$DefAge"
            }
        } catch {
            Write-Output "ERROR"
        }
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            if output == "ERROR":
                return "Check unavailable", "unknown"
            
            parts = output.split('|')
            status = parts[0]
            age = int(parts[1]) if len(parts) > 1 else 0
            
            if status == "ENABLED":
                age_text = "up to date" if age == 0 else (
                    "updated yesterday" if age == 1 else f"{age} days old")
                return f"Enabled (definitions {age_text})", "ok"
            elif status == "OUTDATED":
                return f"Enabled (definitions {age} days old)", "warning"
            elif status == "DISABLED":
                return "Disabled or not installed", "critical"
        
        return "Check unavailable", "unknown"
    except Exception as e:
        logging.debug(f"Failed to get Defender status: {e}")
        return "Check unavailable", "unknown"


def _get_startup_items():
    """Get count of startup items"""
    try:
        ps_script = '''
        $StartupItems = Get-CimInstance Win32_StartupCommand -ErrorAction SilentlyContinue | Measure-Object
        Write-Output $StartupItems.Count
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            count = int(result.stdout.strip())
            
            if count < 10:
                return f"{count} programs", "ok"
            elif count < 20:
                return f"{count} programs", "warning"
            else:
                return f"{count} programs (high)", "warning"
        
        return "Check unavailable", "unknown"
    except Exception as e:
        logging.debug(f"Failed to get startup items: {e}")
        return "Check unavailable", "unknown"


def _get_device_manager_issues():
    """Check for problematic devices in Device Manager"""
    try:
        ps_script = '''
        $Problematic = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue | Where-Object {
            $_.ConfigManagerErrorCode -ne 0 -and $_.ConfigManagerErrorCode -ne $null
        } | Measure-Object
        Write-Output $Problematic.Count
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            count = int(result.stdout.strip())
            
            if count == 0:
                return "No issues detected", "ok"
            else:
                s = '' if count == 1 else 's'
                level = "warning" if count < 3 else "critical"
                return f"{count} device{s} with issues", level
        
        return "Check unavailable", "unknown"
    except Exception as e:
        logging.debug(f"Failed to check Device Manager: {e}")
        return "Check unavailable", "unknown"


def _get_power_plan():
    """Get active power plan"""
    try:
        ps_script = '''
        $ActivePlan = powercfg /getactivescheme
        if ($ActivePlan -match "Power Scheme GUID: [a-f0-9-]+ +\\((.+)\\)") {
            Write-Output $matches[1]
        } else {
            Write-Output "Unknown"
        }
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            plan = result.stdout.strip()
            
            # Color code based on plan type
            if "High performance" in plan or "Ultimate" in plan:
                return plan, "ok"
            elif "Balanced" in plan:
                return plan, "ok"
            elif "Power saver" in plan or "Battery" in plan:
                return plan, "warning"
            else:
                return plan, "ok"
        
        return "Check unavailable", "unknown"
    except Exception as e:
        logging.debug(f"Failed to get power plan: {e}")
        return "Check unavailable", "unknown"


def _get_boot_time():
    """Get last boot time and duration"""
    try:
        ps_script = '''
        try {
            $LastBoot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
            $BootDuration = (Get-Date) - $LastBoot
            $Days = $BootDuration.Days
            $Hours = $BootDuration.Hours
            $Minutes = $BootDuration.Minutes
            
            Write-Output "$Days|$Hours|$Minutes"
        } catch {
            Write-Output "ERROR"
        }
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            if output == "ERROR":
                return "Check unavailable", "unknown"
            
            parts = output.split('|')
            days = int(parts[0])
            hours = int(parts[1])
            minutes = int(parts[2])
            
            # Format uptime
            if days > 0:
                uptime_str = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                uptime_str = f"{hours}h {minutes}m"
            else:
                uptime_str = f"{minutes}m"

            # Color code based on uptime (long uptime = needs restart)
            if days > 14:
                return f"{uptime_str} (restart recommended)", "warning"
            else:
                return uptime_str, "ok"
        
        return "Check unavailable", "unknown"
    except Exception as e:
        logging.debug(f"Failed to get boot time: {e}")
        return "Check unavailable", "unknown"


def get_system_specs(log_callback=None, progress_callback=None, spec_callback=None, skip_categories=None):
    """
    Collect comprehensive system specifications
    Returns a dictionary with system information

    Args:
        log_callback: Optional callback function to log progress messages to GUI
        progress_callback: Optional callback to update progress phase label
        spec_callback: Optional callback to emit partial specs as each section completes
    """
    specs = {}

    def log_message(message):
        """Log message to both callback (GUI) and regular logging"""
        logging.info(message)  # Always log to console/file
        if log_callback:
            log_callback(message)  # Also send to GUI if callback provided

    system = platform.system()
    if system == "Windows":
        specs = _get_windows_specs(
            log_callback, progress_callback, spec_callback, skip_categories=skip_categories)
    elif system == "Darwin":
        specs = _get_macos_specs(progress_callback)
    else:
        specs = _get_linux_specs(progress_callback, log_callback)

    return specs


def _get_windows_specs(log_callback=None, progress_callback=None, spec_callback=None, skip_categories=None):
    """Get Windows-specific system specifications using COM/WMI (pure Python)"""
    import re

    specs = {}
    skip = skip_categories or set()

    def log_message(message):
        """Log message to both callback (GUI) and regular logging"""
        logging.info(message)  # Always log to console/file
        if log_callback:
            log_callback(message)  # Also send to GUI if callback provided

    def _emit_specs():
        """Emit partial specs to GUI for live panel updates."""
        if spec_callback:
            spec_callback(specs)

    # Initialize COM/WMI connection (direct WMI access from Python - no subprocess overhead)
    com_wmi = None
    
    if not COM_AVAILABLE:
        logging.error("pywin32 not available - cannot collect Windows hardware information")
        logging.error("Install with: pip install pywin32")
        return _get_fallback_specs()
    
    try:
        pythoncom.CoInitialize()
        com_wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        com_wmi = com_wmi.ConnectServer(".", "root\\cimv2")
        logging.debug("COM/WMI connection established successfully")
    except Exception as e:
        logging.error(f"Failed to initialize COM/WMI: {e}")
        logging.error("Cannot collect system information without WMI access")
        return _get_fallback_specs()

    # Operating System - Enhanced (with validation)
    log_message("Detecting OS...")
    specs['OS'] = _get_os_info(com_wmi)
    if specs['OS']:
        log_message(f" {specs['OS']}\n")

    # Windows Details - Enhanced (validated)
    specs['WindowsDetails'] = _get_windows_details(com_wmi)
    _emit_specs()

    # System Identification - Type, Model, Serial (populates System Overview early)
    log_message("Identifying system...")
    specs['SystemType'] = _get_system_type(com_wmi)
    specs['SystemHealth'] = _get_system_health()
    specs['LaptopModel'] = _get_laptop_model(com_wmi)
    specs['DesktopType'] = _get_desktop_type(com_wmi, specs.get('SystemType', ''))
    specs['SerialNumber'] = _get_serial_number(com_wmi)
    specs['HPSpecific'] = _get_hp_specific_info()

    sys_id = specs.get('LaptopModel', '')
    if sys_id and sys_id != 'Not Available':
        log_message(f" {specs['SystemType']} - {sys_id}\n")
    elif specs.get('DesktopType') == 'Custom Build':
        log_message(f" {specs['SystemType']} - Custom Build\n")
    else:
        log_message(f" {specs['SystemType']}\n")
    _emit_specs()

    cpu_name = ''
    # CPU - Basic inventory is always collected; skip only affects deeper diagnostics.
    log_message("Detecting CPU...")
    specs['CPU'] = _get_cpu_info(com_wmi)
    if specs['CPU']:
        # Extract just the CPU name without clocks/cores for cleaner log
        import re
        cpu_name = re.match(r'^(.*?)\s*(?:\||$)', specs['CPU']).group(1) if specs['CPU'] else specs['CPU']
        log_message(f" {cpu_name}")
        if specs.get('CPUDetails') and specs['CPUDetails'].get('generation'):
            log_message(f" ({specs['CPUDetails']['generation']})")
        log_message("\n")

    # CPU Enhanced Details - Generation, architecture, socket, TDP, upgrade path (silent - already logged above)
    specs['CPUDetails'] = _get_cpu_enhanced_details(specs.get('CPU', ''))
    _emit_specs()

    # RAM - Basic inventory is always collected; skip only affects deeper diagnostics.
    log_message("Detecting RAM...")
    specs['RAM'] = _get_ram_info(com_wmi)
    specs['RAMDetails'] = _get_ram_details(com_wmi)
    if specs['RAM']:
        # Extract total RAM size for log
        import re
        ram_match = re.match(r'^([\d\.]+ GB)', specs['RAM'])
        if ram_match:
            log_message(f" {ram_match.group(1)}")
            if specs['RAMDetails']:
                log_message(f" ({len(specs['RAMDetails'])} modules)")
            log_message("\n")

    # RAM Slot Count - For empty slot detection and upsell opportunities
    ram_slot_count = _get_ram_slot_count(com_wmi)

    # RAM Compatibility Analysis - Check for mismatches/issues
    if specs.get('RAMDetails'):
        specs['RAMCompatibilityWarnings'] = _analyze_ram_compatibility(
            specs['RAMDetails'],
            cpu_name=specs.get('CPU', ''),
            system_type='',  # Will add system type detection later
            total_slots=ram_slot_count
        )
    else:
        specs['RAMCompatibilityWarnings'] = []
    _emit_specs()

    # GPU - Basic inventory is always collected; skip only affects thermal/load diagnostics.
    log_message("Detecting GPU...")
    specs['GPU'] = _get_gpu_info(com_wmi)
    if specs['GPU'] and specs['GPU'] != 'Unknown':
        # Extract GPU name without VRAM/driver for log
        import re
        gpu_name = re.match(r'^(.*?)\s*(?:\(|$)', specs['GPU']).group(1) if specs['GPU'] else specs['GPU']
        log_message(f" {gpu_name}\n")

    # Build GPUDetails dict from the GPU string for report formatter
    specs['GPUDetails'] = _parse_gpu_details(specs.get('GPU', ''))

    # GPU Detailed Metrics - Temperature, clocks, power, utilization (NVIDIA/AMD)
    if 'gpu' in skip:
        specs['GPUMetrics'] = {}
    else:
        gpu_name = specs.get('GPU', '')
        if gpu_name and gpu_name != 'Unknown':
            gpu_metrics = _get_gpu_detailed_metrics(gpu_name)
            if gpu_metrics:
                specs['GPUMetrics'] = gpu_metrics
    _emit_specs()

    # Motherboard / BIOS
    log_message("Detecting Motherboard...")
    specs['Motherboard'] = _get_motherboard_info(com_wmi)
    if specs['Motherboard']:
        log_message(f" {specs['Motherboard']}\n")

    # Chipset - Extract from motherboard (shows platform generation)
    chipset = _extract_chipset_from_motherboard(specs.get('Motherboard', ''))
    if chipset:
        specs['Chipset'] = chipset
        # Get chipset-level specs (JEDEC speeds, memory type, channels)
        chipset_specs = _get_chipset_specs(chipset)
        if chipset_specs:
            specs['ChipsetSpecs'] = chipset_specs
    else:
        specs['Chipset'] = None
        specs['ChipsetSpecs'] = None

    # Motherboard RAM specs - From verified motherboard database (actual max RAM)
    mobo_specs = _get_motherboard_specs(specs.get('Motherboard', ''))
    if mobo_specs:
        specs['MotherboardSpecs'] = mobo_specs
    else:
        # Unknown board - show fallback message in GUI
        specs['MotherboardSpecs'] = None
    _emit_specs()

    # Battery - Enhanced with health (validated)
    specs['Battery'] = _get_battery_status(com_wmi)

    # Storage - Basic inventory is always collected; skip only affects SMART/bench tests.
    log_message("Detecting Storage...")
    specs['Storage'] = _get_storage_info(com_wmi)
    if specs['Storage']:
        drive_count = specs['Storage'].count('\n') + 1 if specs['Storage'] else 0
        log_message(f" {drive_count} drive(s)\n")
    _emit_specs()

    # Network Adapters - Basic inventory is always collected; skip only affects WiFi diagnostics.
    log_message("Detecting Network...")
    specs['Network'] = _get_network_info(com_wmi)
    specs['_ethernet_connected'] = _has_connected_ethernet(com_wmi)
    if specs['Network']:
        adapter_count = specs['Network'].count('\n') + 1 if specs['Network'] else 0
        log_message(f" {adapter_count} adapter(s)\n")
    _emit_specs()

    # Display Information - Basic inventory is always collected; skip only affects panel/webcam diagnostics.
    log_message("Detecting Displays...")
    specs['Display'] = _get_display_info(com_wmi)
    if specs['Display'] and specs['Display'] != 'Display information unavailable':
        display_lines = [l for l in specs['Display'].split('\n') if l.strip()]
        log_message(f" {len(display_lines)} monitor(s)\n")
    _emit_specs()

    # BIOS Information - Enhanced (validated) - returns (first_line, remaining_lines)
    log_message("Detecting BIOS...")
    bios_first, bios_details = _get_bios_info(com_wmi)
    specs['BIOS'] = bios_first
    specs['BIOSDetails'] = bios_details
    if bios_first:
        log_message(f" {bios_first}\n")
    _emit_specs()

    # Driver Information - Enhanced
    specs['Drivers'] = _get_driver_info(com_wmi)

    # Enhanced Storage with SMART Status (structured for GUI, validated)
    if 'storage' in skip:
        specs['StorageHealth'] = []
    else:
        log_message("Analyzing SMART data...")
        specs['StorageHealth'] = _get_storage_health_structured(com_wmi)
    if specs['StorageHealth']:
        # Categorize drives: healthy, unhealthy, failed SMART, N/A (USB)
        # FIX: score is nested in interpretation dict, not top level
        healthy_drives = [h for h in specs['StorageHealth'] 
                          if h.get('interpretation', {}).get('score') is not None 
                          and h.get('interpretation', {}).get('score') >= 85]
        unhealthy_drives = [h for h in specs['StorageHealth'] 
                            if h.get('interpretation', {}).get('score') is not None 
                            and h.get('interpretation', {}).get('score') < 85]
        no_data_drives = [h for h in specs['StorageHealth'] 
                          if h.get('interpretation', {}).get('score') is None 
                          and h.get('status') in ['Error', 'Unknown']]
        na_drives = [h for h in specs['StorageHealth'] if h.get('status') == 'N/A']
        
        # Count issues: unhealthy (low health) + no data (can't assess)
        total_issues = len(unhealthy_drives) + len(no_data_drives)
        
        if len(no_data_drives) == len(specs['StorageHealth']) and len(no_data_drives) > 0:
            # All drives failed SMART - likely admin rights issue
            log_message(" ⚠️ SMART data unavailable - Run as Administrator for full diagnostics\n")
            logging.warning(f"SMART health check: All {len(no_data_drives)} drives failed SMART query [TAG:SMART ALL_FAILED count={len(no_data_drives)}]")
        elif total_issues > 0:
            # Mixed results - some good, some bad/unknown
            issue_breakdown = []
            if unhealthy_drives:
                issue_breakdown.append(f"{len(unhealthy_drives)} unhealthy")
            if no_data_drives:
                issue_breakdown.append(f"{len(no_data_drives)} unknown")
            
            log_message(f" ⚠️ Storage health: {len(healthy_drives)} OK, {', '.join(issue_breakdown)}\n")
            
            # Detailed logging for diagnostics - only emit alerts, not duplicate summaries
            for drive in unhealthy_drives:
                health_score = drive.get('interpretation', {}).get('score', 0)
                logging.warning(f"Storage alert: {drive.get('model', 'Unknown')} - Health {health_score}% (degraded) [TAG:STORAGE UNHEALTHY model=\"{drive.get('model', 'Unknown')}\" health={health_score}]")
            for drive in no_data_drives:
                logging.warning(f"Storage alert: {drive.get('model', 'Unknown')} - SMART data unavailable (verify manually) [TAG:STORAGE NO_DATA model=\"{drive.get('model', 'Unknown')}\"]")
        else:
            log_message(f" All drives healthy\n")
            logging.info(f"SMART health check: {len(healthy_drives)} drive(s) healthy, {len(na_drives)} USB/external skipped [TAG:SMART ALL_HEALTHY count={len(healthy_drives)}]")
    
    _emit_specs()

    # Phase 2: Enhanced Diagnostics (validated)
    # Enhanced Battery Information (Laptops)
    if 'battery' in skip:
        specs['BatteryDetails'] = None
    else:
        if specs.get('SystemType') == 'Laptop':
            log_message("Analyzing battery...")
        specs['BatteryDetails'] = _get_battery_details(com_wmi)

    # Screen Size Detection (Laptops)
    if 'display' in skip:
        specs['ScreenSize'] = None
        specs['PanelDetails'] = None
    else:
        specs['ScreenSize'] = _get_screen_size(com_wmi)

        # LCD Panel Details (Laptops) - Enhanced with manufacturer, model, year
        if specs['SystemType'] == 'Laptop':
            specs['PanelDetails'] = _get_panel_details()
            battery_details = specs.get('BatteryDetails') or {}
            if battery_details.get('health_percent'):
                log_message(f" {battery_details['health_percent']}% health\n")
            else:
                log_message("\n")
        else:
            specs['PanelDetails'] = None
    _emit_specs()

    # Recent Critical Errors
    specs['RecentErrors'] = [] if 'event_logs' in skip else _get_recent_critical_errors()

    # Advanced Diagnostics
    log_message("Running diagnostics...")
    def _diag_or_skipped(category_key, getter):
        if category_key in skip:
            return ('Test skipped', 'skipped')
        return getter()

    specs['AdvancedDiagnostics'] = {
        'EventLog': _diag_or_skipped('event_logs', _get_event_log_summary),
        'WindowsUpdate': _diag_or_skipped('windows_update', _get_windows_update_status),
        'ManufacturerUpdates': _diag_or_skipped('manufacturer_updates', lambda: _get_manufacturer_update_tools_summary(com_wmi)),
        'Defender': _diag_or_skipped('defender', _get_defender_status),
        'StartupItems': _diag_or_skipped('startup_items', _get_startup_items),
        'DeviceManager': _diag_or_skipped('device_manager', _get_device_manager_issues),
        'KeyboardTest': ('Test skipped', 'skipped') if 'keyboard' in skip else ('Pending technician keyboard test', 'warning'),
        'PowerPlan': _diag_or_skipped('power_boot', _get_power_plan),
        'BootTime': _diag_or_skipped('power_boot', _get_boot_time)
    }

    specs['ManufacturerUpdateTools'] = (
        {'status': 'skipped', 'summary': 'Test skipped'}
        if 'manufacturer_updates' in skip
        else _detect_manufacturer_update_tools(com_wmi)
    )

    # Promote to top-level keys for report formatter
    _adv = specs['AdvancedDiagnostics']

    # BootTime — formatter handles tuples directly
    specs['BootTime'] = _adv.get('BootTime')

    # PowerPlan — formatter uses as plain string
    _pp = _adv.get('PowerPlan', ('Unknown', 'unknown'))
    specs['PowerPlan'] = _pp[0] if isinstance(_pp, (list, tuple)) else _pp
    specs['ActivePowerPlan'] = specs['PowerPlan']

    # StartupItems — formatter uses as plain string
    _si = _adv.get('StartupItems', ('Unknown', 'unknown'))
    specs['StartupItems'] = _si[0] if isinstance(_si, (list, tuple)) else _si

    # DeviceManager — summary for panels, detailed errors bridged from
    # AdvancedHealth later (in the worker) with proper device dicts
    _dm = _adv.get('DeviceManager', ('No issues', 'ok'))
    _dm_text = _dm[0] if isinstance(_dm, (list, tuple)) else (_dm or '')
    specs['DeviceManagerIssues'] = _dm_text
    specs['DeviceManagerErrors'] = []  # populated from AdvancedHealth if available

    dm_issues = int(re.search(r'(\d+)', _dm_text).group(1)) if re.search(r'(\d+)', _dm_text) else 0
    if dm_issues > 0:
        log_message(f" {dm_issues} device issue(s)\n")
    else:
        log_message(" Complete\n")
    _emit_specs()

    # Release COM objects before uninitializing
    if COM_AVAILABLE and com_wmi:
        try:
            import gc
            del com_wmi
            gc.collect()
            pythoncom.CoUninitialize()
        except Exception:
            pass
    
    return specs


def _query_com_wmi(com_wmi, class_name, properties=None):
    """Helper function to query COM/WMI"""
    if not com_wmi:
        return None
    try:
        query = f"SELECT * FROM {class_name}"
        items = com_wmi.ExecQuery(query)
        if items.Count > 0:
            return items
    except Exception as e:
        logging.debug(f"COM/WMI query failed for {class_name}: {e}")
    return None


def _get_os_info(com_wmi):
    """Get enhanced OS information using COM/WMI"""
    try:
        # Start with platform info as fallback
        os_info = platform.platform()
        
        # Try to get detailed info via COM/WMI
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_OperatingSystem")
                if items:
                    os_data = items.ItemIndex(0)
                    edition = os_data.Properties_("Caption").Value or ""
                    build = os_data.Properties_("BuildNumber").Value or ""
                    if edition and build:
                        return f"{edition} (Build {build})"
            except Exception as e:
                logging.debug(f"Could not get OS details via COM/WMI: {e}")
        
        # Return platform info if WMI failed
        return os_info if os_info else "Unknown"
        
    except Exception as e:
        logging.warning(f"Failed to get OS info: {e}")
        return platform.platform() or "Unknown"


def _get_base_clock_from_registry(cpu_name=None):
    """Try to get CPU base clock from Windows registry, with validation"""
    if platform.system() != "Windows":
        return None
    
    # Known base clocks for CPUs (in MHz) - used for validation
    KNOWN_BASE_CLOCKS = {
        # Intel 10th Gen Ice Lake Mobile
        "1035G1": 1000, "1035G4": 1100, "1035G7": 1200,
        "1065G7": 1300, "1068NG7": 1300,
        # Intel 10th Gen Comet Lake
        "10900K": 3700, "10700K": 3800, "10600K": 4100,
        # Intel 11th Gen
        "11900K": 3500, "11700K": 3600, "11600K": 3700,
    }
    
    try:
        import winreg
        key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            try:
                reg_clock_mhz, _ = winreg.QueryValueEx(key, "~MHz")
                reg_clock_mhz = int(reg_clock_mhz)
                
                # Validate: If CPU name provided, check if registry value matches known base clock
                # Registry often returns current speed, not base speed
                if cpu_name:
                    cpu_upper = cpu_name.upper()
                    for model, known_base in KNOWN_BASE_CLOCKS.items():
                        if model.upper() in cpu_upper:
                            # For low-frequency CPUs (<2GHz), use tighter tolerance (5% or 50MHz, whichever is larger)
                            # For high-frequency CPUs, use 200MHz tolerance
                            if known_base < 2000:
                                tolerance = max(50, int(known_base * 0.05))  # 5% or 50MHz minimum
                            else:
                                tolerance = 200
                            
                            # If registry value is close to known base, use it
                            if abs(reg_clock_mhz - known_base) <= tolerance:
                                return reg_clock_mhz
                            # Otherwise, registry is likely showing current speed, use known base
                            else:
                                logging.debug(f"Registry clock ({reg_clock_mhz}MHz) doesn't match known base ({known_base}MHz) for {model} (tolerance: {tolerance}MHz), using known base")
                                return known_base
                
                # If no CPU match, return registry value (might be current speed, but better than nothing)
                return reg_clock_mhz
            except FileNotFoundError:
                return None
    except Exception:
        return None
    
    # Fallback: Try to get from CPU name database
    if cpu_name:
        cpu_upper = cpu_name.upper()
        for model, known_base in KNOWN_BASE_CLOCKS.items():
            if model.upper() in cpu_upper:
                return known_base
    
    return None


def _get_cpu_enhanced_details(cpu_name):
    """
    Get comprehensive CPU details for PC AutoSpec diagnostics.
    
    Returns dict with:
    - generation: "9th Gen" or "Zen 4"
    - architecture: "Coffee Lake Refresh" or "Raphael"
    - year: Release year (int)
    - age_years: Years since release
    - socket: "LGA1151" or "AM5"
    - max_ram_speed: Max supported RAM speed (e.g., "DDR4-2666")
    - tdp: TDP in watts (int)
    - windows_compatibility: "Windows 11 compatible" or "Windows 10 only"
    - upgrade_path: List of upgrade suggestions
    """
    if not cpu_name:
        return None
    
    cpu_upper = cpu_name.upper()
    
    # Comprehensive CPU database
    # Format: (gen, arch, year, socket, max_ram, tdp, windows_compat, [upgrade_suggestions])
    # Windows 11 officially supported: Intel 8th Gen+, AMD Ryzen 2000 (Zen+)+
    CPU_DATABASE = {
        # Intel 9th Gen (Coffee Lake Refresh) - 2018-2019 | Windows 11 Compatible
        "9900K": ("9th Gen", "Coffee Lake Refresh", 2018, "LGA1151", "DDR4-2666", 95, "Windows 11 compatible", ["i9-9900KS"]),
        "9900KF": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 95, "Windows 11 compatible", ["i9-9900K", "i9-9900KS"]),
        "9900KS": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 127, "Windows 11 compatible", []),
        "9700K": ("9th Gen", "Coffee Lake Refresh", 2018, "LGA1151", "DDR4-2666", 95, "Windows 11 compatible", ["i9-9900K", "i9-9900KS"]),
        "9700KF": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 95, "Windows 11 compatible", ["i9-9900K", "i9-9900KS"]),
        "9600K": ("9th Gen", "Coffee Lake Refresh", 2018, "LGA1151", "DDR4-2666", 95, "Windows 11 compatible", ["i7-9700K", "i9-9900K"]),
        "9600KF": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 95, "Windows 11 compatible", ["i7-9700K", "i9-9900K"]),
        
        # Intel 10th Gen (Comet Lake) - 2020
        "10900K": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 125, ["i9-10900KF"]),
        "10900KF": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 125, []),
        "10850K": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 125, ["i9-10900K"]),
        "10700K": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 125, ["i9-10850K", "i9-10900K"]),
        "10700KF": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 125, ["i9-10850K", "i9-10900K"]),
        "10600K": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 125, ["i7-10700K", "i9-10900K"]),
        "10600KF": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 125, ["i7-10700K", "i9-10900K"]),
        
        # Intel 11th Gen (Rocket Lake) - 2021
        "11900K": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 125, ["i9-11900KF"]),
        "11900KF": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 125, []),
        "11700K": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 125, ["i9-11900K"]),
        "11700KF": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 125, ["i9-11900K"]),
        "11600K": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 125, ["i7-11700K", "i9-11900K"]),
        "11600KF": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 125, ["i7-11700K", "i9-11900K"]),
        
        # Intel 12th Gen (Alder Lake) - 2021-2022
        "12900K": ("12th Gen", "Alder Lake", 2021, "LGA1700", "DDR5-4800 / DDR4-3200", 125, ["i9-12900KS"]),
        "12900KF": ("12th Gen", "Alder Lake", 2021, "LGA1700", "DDR5-4800 / DDR4-3200", 125, ["i9-12900K", "i9-12900KS"]),
        "12900KS": ("12th Gen", "Alder Lake", 2022, "LGA1700", "DDR5-4800 / DDR4-3200", 150, ["i9-13900K", "i9-14900K"]),
        "12700K": ("12th Gen", "Alder Lake", 2021, "LGA1700", "DDR5-4800 / DDR4-3200", 125, ["i9-12900K", "i9-13900K"]),
        "12700KF": ("12th Gen", "Alder Lake", 2021, "LGA1700", "DDR5-4800 / DDR4-3200", 125, ["i9-12900K", "i9-13900K"]),
        "12600K": ("12th Gen", "Alder Lake", 2021, "LGA1700", "DDR5-4800 / DDR4-3200", 125, ["i7-12700K", "i9-13900K"]),
        "12600KF": ("12th Gen", "Alder Lake", 2021, "LGA1700", "DDR5-4800 / DDR4-3200", 125, ["i7-12700K", "i9-13900K"]),
        
        # Intel 13th Gen (Raptor Lake) - 2022-2023
        "13900K": ("13th Gen", "Raptor Lake", 2022, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-13900KS"]),
        "13900KF": ("13th Gen", "Raptor Lake", 2022, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-13900K", "i9-13900KS"]),
        "13900KS": ("13th Gen", "Raptor Lake", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 150, ["i9-14900K"]),
        "13700K": ("13th Gen", "Raptor Lake", 2022, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-13900K", "i9-14900K"]),
        "13700KF": ("13th Gen", "Raptor Lake", 2022, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-13900K", "i9-14900K"]),
        "13600K": ("13th Gen", "Raptor Lake", 2022, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i7-13700K", "i9-14900K"]),
        "13600KF": ("13th Gen", "Raptor Lake", 2022, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i7-13700K", "i9-14900K"]),
        
        # Intel 14th Gen (Raptor Lake Refresh) - 2023-2024
        "14900K": ("14th Gen", "Raptor Lake Refresh", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-14900KS"]),
        "14900KF": ("14th Gen", "Raptor Lake Refresh", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-14900K", "i9-14900KS"]),
        "14900KS": ("14th Gen", "Raptor Lake Refresh", 2024, "LGA1700", "DDR5-5600 / DDR4-3200", 150, []),  # Top of LGA1700
        "14700K": ("14th Gen", "Raptor Lake Refresh", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-14900K"]),
        "14700KF": ("14th Gen", "Raptor Lake Refresh", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i9-14900K"]),
        "14600K": ("14th Gen", "Raptor Lake Refresh", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i7-14700K", "i9-14900K"]),
        "14600KF": ("14th Gen", "Raptor Lake Refresh", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 125, ["i7-14700K", "i9-14900K"]),
        
        # AMD Ryzen 5000 (Zen 3) - 2020-2021
        "5950X": ("Zen 3", "Vermeer", 2020, "AM4", "DDR4-3200", 105, []),  # Top of AM4
        "5900X": ("Zen 3", "Vermeer", 2020, "AM4", "DDR4-3200", 105, ["Ryzen 9 5950X"]),
        "5800X": ("Zen 3", "Vermeer", 2020, "AM4", "DDR4-3200", 105, ["Ryzen 9 5900X", "Ryzen 9 5950X"]),
        "5800X3D": ("Zen 3", "Vermeer 3D V-Cache", 2022, "AM4", "DDR4-3200", 105, ["Ryzen 9 5950X"]),
        "5700X": ("Zen 3", "Vermeer", 2022, "AM4", "DDR4-3200", 65, ["Ryzen 7 5800X", "Ryzen 9 5900X"]),
        "5600X": ("Zen 3", "Vermeer", 2020, "AM4", "DDR4-3200", 65, ["Ryzen 7 5800X3D", "Ryzen 9 5900X"]),
        "5600": ("Zen 3", "Vermeer", 2022, "AM4", "DDR4-3200", 65, ["Ryzen 5 5600X", "Ryzen 7 5800X"]),
        
        # AMD Ryzen 7000 (Zen 4) - 2022-2023
        "7950X": ("Zen 4", "Raphael", 2022, "AM5", "DDR5-5200", 170, ["Ryzen 9 7950X3D"]),
        "7950X3D": ("Zen 4", "Raphael 3D V-Cache", 2023, "AM5", "DDR5-5200", 120, []),  # Top gaming CPU
        "7900X": ("Zen 4", "Raphael", 2022, "AM5", "DDR5-5200", 170, ["Ryzen 9 7950X"]),
        "7900X3D": ("Zen 4", "Raphael 3D V-Cache", 2023, "AM5", "DDR5-5200", 120, ["Ryzen 9 7950X3D"]),
        "7800X3D": ("Zen 4", "Raphael 3D V-Cache", 2023, "AM5", "DDR5-5200", 120, ["Ryzen 9 7900X3D"]),
        "7700X": ("Zen 4", "Raphael", 2022, "AM5", "DDR5-5200", 105, ["Ryzen 7 7800X3D", "Ryzen 9 7900X"]),
        "7700": ("Zen 4", "Raphael", 2023, "AM5", "DDR5-5200", 65, ["Ryzen 7 7700X", "Ryzen 7 7800X3D"]),
        "7600X": ("Zen 4", "Raphael", 2022, "AM5", "DDR5-5200", 105, ["Ryzen 7 7700X", "Ryzen 7 7800X3D"]),
        "7600": ("Zen 4", "Raphael", 2023, "AM5", "DDR5-5200", 65, ["Ryzen 5 7600X", "Ryzen 7 7700X"]),
        
        # AMD Ryzen 9000 (Zen 5) - 2024
        "9950X": ("Zen 5", "Granite Ridge", 2024, "AM5", "DDR5-5600", 170, []),  # Latest top
        "9900X": ("Zen 5", "Granite Ridge", 2024, "AM5", "DDR5-5600", 120, ["Ryzen 9 9950X"]),
        "9700X": ("Zen 5", "Granite Ridge", 2024, "AM5", "DDR5-5600", 65, ["Ryzen 9 9900X", "Ryzen 9 9950X"]),
        "9600X": ("Zen 5", "Granite Ridge", 2024, "AM5", "DDR5-5600", 65, ["Ryzen 7 9700X", "Ryzen 9 9900X"]),
        
        # ===== EXPANDED DATABASE: Non-K, i3, Older Gen, Mobile =====
        
        # Intel 6th Gen (Skylake) - 2015-2016
        "6700K": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 91, "Windows 10 only", ["i7-7700K", "i7-8700K"]),
        "6700": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 65, "Windows 10 only", ["i7-6700K", "i7-7700"]),
        "6600K": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 91, "Windows 10 only", ["i7-6700K"]),
        "6600": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 65, "Windows 10 only", ["i5-6600K", "i7-6700"]),
        "6500": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 65, "Windows 10 only", ["i5-6600", "i7-6700"]),
        "6400": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 65, "Windows 10 only", ["i5-6500", "i7-6700"]),
        "6300": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 51, "Windows 10 only", ["i5-6400"]),
        "6100": ("6th Gen", "Skylake", 2015, "LGA1151", "DDR4-2133", 51, "Windows 10 only", ["i3-6300", "i5-6400"]),
        
        # Intel 7th Gen (Kaby Lake) - 2017
        "7700K": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 91, "Windows 10 only", ["i7-8700K", "i7-9700K"]),
        "7700": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 65, "Windows 10 only", ["i7-7700K", "i7-8700"]),
        "7600K": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 91, "Windows 10 only", ["i7-7700K"]),
        "7600": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 65, "Windows 10 only", ["i5-7600K", "i7-7700"]),
        "7500": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 65, "Windows 10 only", ["i5-7600", "i7-7700"]),
        "7400": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 65, "Windows 10 only", ["i5-7500", "i7-7700"]),
        "7350K": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 60, "Windows 10 only", ["i5-7400"]),
        "7300": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 51, "Windows 10 only", ["i3-7350K", "i5-7400"]),
        "7100": ("7th Gen", "Kaby Lake", 2017, "LGA1151", "DDR4-2400", 51, "Windows 10 only", ["i3-7300", "i5-7400"]),
        
        # Intel 8th Gen (Coffee Lake) - 2017-2018
        "8700K": ("8th Gen", "Coffee Lake", 2017, "LGA1151", "DDR4-2666", 95, ["i7-9700K", "i9-9900K"]),
        "8700": ("8th Gen", "Coffee Lake", 2017, "LGA1151", "DDR4-2666", 65, ["i7-8700K", "i7-9700"]),
        "8600K": ("8th Gen", "Coffee Lake", 2017, "LGA1151", "DDR4-2666", 95, ["i7-8700K"]),
        "8600": ("8th Gen", "Coffee Lake", 2018, "LGA1151", "DDR4-2666", 65, ["i5-8600K", "i7-8700"]),
        "8500": ("8th Gen", "Coffee Lake", 2018, "LGA1151", "DDR4-2666", 65, ["i5-8600", "i7-8700"]),
        "8400": ("8th Gen", "Coffee Lake", 2017, "LGA1151", "DDR4-2666", 65, ["i5-8500", "i7-8700"]),
        "8350K": ("8th Gen", "Coffee Lake", 2017, "LGA1151", "DDR4-2666", 91, ["i5-8400"]),
        "8300": ("8th Gen", "Coffee Lake", 2018, "LGA1151", "DDR4-2666", 62, ["i3-8350K", "i5-8400"]),
        "8100": ("8th Gen", "Coffee Lake", 2017, "LGA1151", "DDR4-2666", 65, ["i3-8300", "i5-8400"]),
        
        # Intel 9th Gen Non-K (Coffee Lake Refresh) - 2019
        "9900": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 65, ["i9-9900K"]),
        "9700": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 65, ["i7-9700K", "i9-9900"]),
        "9600": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 65, ["i5-9600K", "i7-9700"]),
        "9500": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 65, ["i5-9600", "i7-9700"]),
        "9400": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 65, ["i5-9500", "i7-9700"]),
        "9350KF": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 91, ["i5-9400"]),
        "9300": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 62, ["i3-9350KF", "i5-9400"]),
        "9100": ("9th Gen", "Coffee Lake Refresh", 2019, "LGA1151", "DDR4-2666", 65, ["i3-9300", "i5-9400"]),
        
        # Intel 10th Gen Non-K (Comet Lake) - 2020
        "10900": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 65, ["i9-10900K"]),
        "10850": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 65, ["i9-10900"]),
        "10700": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 65, ["i7-10700K", "i9-10900"]),
        "10600": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 65, ["i5-10600K", "i7-10700"]),
        "10500": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2933", 65, ["i5-10600", "i7-10700"]),
        "10400": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2666", 65, ["i5-10500", "i7-10700"]),
        "10320": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2666", 65, ["i3-10325", "i5-10400"]),
        "10300": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2666", 65, ["i3-10320", "i5-10400"]),
        "10100": ("10th Gen", "Comet Lake", 2020, "LGA1200", "DDR4-2666", 65, ["i3-10300", "i5-10400"]),
        
        # Intel 11th Gen Non-K (Rocket Lake) - 2021
        "11900": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 65, ["i9-11900K"]),
        "11700": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 65, ["i7-11700K", "i9-11900"]),
        "11600": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 65, ["i5-11600K", "i7-11700"]),
        "11500": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 65, ["i5-11600", "i7-11700"]),
        "11400": ("11th Gen", "Rocket Lake", 2021, "LGA1200", "DDR4-3200", 65, ["i5-11500", "i7-11700"]),
        
        # Intel 12th Gen Non-K (Alder Lake) - 2022
        "12900": ("12th Gen", "Alder Lake", 2022, "LGA1700", "DDR5-4800 / DDR4-3200", 65, ["i9-12900K"]),
        "12700": ("12th Gen", "Alder Lake", 2022, "LGA1700", "DDR5-4800 / DDR4-3200", 65, ["i7-12700K", "i9-12900"]),
        "12600": ("12th Gen", "Alder Lake", 2022, "LGA1700", "DDR5-4800 / DDR4-3200", 65, ["i5-12600K", "i7-12700"]),
        "12500": ("12th Gen", "Alder Lake", 2022, "LGA1700", "DDR5-4800 / DDR4-3200", 65, ["i5-12600", "i7-12700"]),
        "12400": ("12th Gen", "Alder Lake", 2022, "LGA1700", "DDR5-4800 / DDR4-3200", 65, ["i5-12500", "i7-12700"]),
        "12100": ("12th Gen", "Alder Lake", 2022, "LGA1700", "DDR5-4800 / DDR4-3200", 60, ["i3-12300", "i5-12400"]),
        
        # Intel 13th Gen Non-K (Raptor Lake) - 2023
        "13900": ("13th Gen", "Raptor Lake", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i9-13900K"]),
        "13700": ("13th Gen", "Raptor Lake", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i7-13700K", "i9-13900"]),
        "13600": ("13th Gen", "Raptor Lake", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i5-13600K", "i7-13700"]),
        "13500": ("13th Gen", "Raptor Lake", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i5-13600", "i7-13700"]),
        "13400": ("13th Gen", "Raptor Lake", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i5-13500", "i7-13700"]),
        "13100": ("13th Gen", "Raptor Lake", 2023, "LGA1700", "DDR5-5600 / DDR4-3200", 60, ["i3-13400", "i5-13400"]),
        
        # Intel 14th Gen Non-K (Raptor Lake Refresh) - 2024
        "14900": ("14th Gen", "Raptor Lake Refresh", 2024, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i9-14900K"]),
        "14700": ("14th Gen", "Raptor Lake Refresh", 2024, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i7-14700K", "i9-14900"]),
        "14600": ("14th Gen", "Raptor Lake Refresh", 2024, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i5-14600K", "i7-14700"]),
        "14500": ("14th Gen", "Raptor Lake Refresh", 2024, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i5-14600", "i7-14700"]),
        "14400": ("14th Gen", "Raptor Lake Refresh", 2024, "LGA1700", "DDR5-5600 / DDR4-3200", 65, ["i5-14500", "i7-14700"]),
        
        # Intel Mobile CPUs (H-series, High Performance) - Common in gaming laptops
        "11800H": ("11th Gen", "Tiger Lake-H", 2021, "BGA (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "10750H": ("10th Gen", "Comet Lake-H", 2020, "BGA (Mobile)", "DDR4-2933", 45, ["Upgrade laptop"]),
        "9750H": ("9th Gen", "Coffee Lake-H", 2019, "BGA (Mobile)", "DDR4-2666", 45, ["Upgrade laptop"]),
        "8750H": ("8th Gen", "Coffee Lake-H", 2018, "BGA (Mobile)", "DDR4-2666", 45, ["Upgrade laptop"]),
        "12700H": ("12th Gen", "Alder Lake-H", 2022, "BGA (Mobile)", "DDR5-4800", 45, ["Upgrade laptop"]),
        "11600H": ("11th Gen", "Tiger Lake-H", 2021, "BGA (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "13700H": ("13th Gen", "Raptor Lake-H", 2023, "BGA (Mobile)", "DDR5-5600", 45, ["Upgrade laptop"]),
        
        # Intel Mobile CPUs (U-series, Low Power) - Common in business/ultrabooks
        "1135G7": ("11th Gen", "Tiger Lake-U", 2020, "BGA (Mobile)", "DDR4-3200", 15, ["Upgrade laptop"]),
        "1035G7": ("10th Gen", "Ice Lake-U", 2019, "BGA (Mobile)", "DDR4-3200", 15, ["Upgrade laptop"]),
        "8265U": ("8th Gen", "Whiskey Lake-U", 2018, "BGA (Mobile)", "DDR4-2400", 15, ["Upgrade laptop"]),
        "8250U": ("8th Gen", "Kaby Lake-U", 2017, "BGA (Mobile)", "DDR4-2400", 15, ["Upgrade laptop"]),
        "7200U": ("7th Gen", "Kaby Lake-U", 2016, "BGA (Mobile)", "DDR4-2133", 15, "Windows 10 only", ["Upgrade laptop"]),
        "6200U": ("6th Gen", "Skylake-U", 2015, "BGA (Mobile)", "DDR4-2133", 15, "Windows 10 only", ["Upgrade laptop"]),
        
        # AMD Ryzen 3000 (Zen 2) - 2019-2020
        "3950X": ("Zen 2", "Matisse", 2019, "AM4", "DDR4-3200", 105, ["Ryzen 9 5950X"]),
        "3900X": ("Zen 2", "Matisse", 2019, "AM4", "DDR4-3200", 105, ["Ryzen 9 3950X", "Ryzen 9 5900X"]),
        "3800X": ("Zen 2", "Matisse", 2019, "AM4", "DDR4-3200", 105, ["Ryzen 9 3900X", "Ryzen 7 5800X"]),
        "3700X": ("Zen 2", "Matisse", 2019, "AM4", "DDR4-3200", 65, ["Ryzen 7 3800X", "Ryzen 7 5800X"]),
        "3600X": ("Zen 2", "Matisse", 2019, "AM4", "DDR4-3200", 95, ["Ryzen 7 3700X", "Ryzen 5 5600X"]),
        "3600": ("Zen 2", "Matisse", 2019, "AM4", "DDR4-3200", 65, ["Ryzen 5 3600X", "Ryzen 5 5600X"]),
        "3500X": ("Zen 2", "Matisse", 2019, "AM4", "DDR4-3200", 65, ["Ryzen 5 3600"]),
        "3300X": ("Zen 2", "Matisse", 2020, "AM4", "DDR4-3200", 65, ["Ryzen 5 3600"]),
        "3100": ("Zen 2", "Matisse", 2020, "AM4", "DDR4-3200", 65, ["Ryzen 3 3300X", "Ryzen 5 3600"]),
        
        # AMD Ryzen 4000 Mobile (Zen 2) - 2020 - Common in laptops
        "4900H": ("Zen 2", "Renoir", 2020, "FP6 (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "4800H": ("Zen 2", "Renoir", 2020, "FP6 (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "4600H": ("Zen 2", "Renoir", 2020, "FP6 (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "4800U": ("Zen 2", "Renoir", 2020, "FP6 (Mobile)", "DDR4-3200", 15, ["Upgrade laptop"]),
        "4700U": ("Zen 2", "Renoir", 2020, "FP6 (Mobile)", "DDR4-3200", 15, ["Upgrade laptop"]),
        
        # AMD Ryzen 5000 Non-X (Zen 3) - 2021-2022
        "5900": ("Zen 3", "Vermeer", 2022, "AM4", "DDR4-3200", 65, ["Ryzen 9 5900X", "Ryzen 9 5950X"]),
        "5800": ("Zen 3", "Vermeer", 2022, "AM4", "DDR4-3200", 65, ["Ryzen 7 5800X", "Ryzen 9 5900X"]),
        "5700": ("Zen 3", "Vermeer", 2022, "AM4", "DDR4-3200", 65, ["Ryzen 7 5800X"]),
        "5500": ("Zen 3", "Cezanne", 2022, "AM4", "DDR4-3200", 65, ["Ryzen 5 5600", "Ryzen 7 5700X"]),
        "5300": ("Zen 3", "Cezanne", 2022, "AM4", "DDR4-3200", 65, ["Ryzen 5 5500"]),
        
        # AMD Ryzen 5000 Mobile (Zen 3) - 2021-2022
        "5900HX": ("Zen 3", "Cezanne", 2021, "FP6 (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "5800H": ("Zen 3", "Cezanne", 2021, "FP6 (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "5600H": ("Zen 3", "Cezanne", 2021, "FP6 (Mobile)", "DDR4-3200", 45, ["Upgrade laptop"]),
        "5800U": ("Zen 3", "Cezanne", 2021, "FP6 (Mobile)", "DDR4-3200", 15, ["Upgrade laptop"]),
        "5700U": ("Zen 3", "Cezanne", 2021, "FP6 (Mobile)", "DDR4-3200", 15, ["Upgrade laptop"]),
        
        # AMD Ryzen 7000 Non-X (Zen 4) - 2023
        "7900": ("Zen 4", "Raphael", 2023, "AM5", "DDR5-5200", 65, ["Ryzen 9 7900X", "Ryzen 9 7950X"]),
        "7800": ("Zen 4", "Raphael", 2023, "AM5", "DDR5-5200", 65, ["Ryzen 7 7800X3D", "Ryzen 9 7900X"]),
        "7500": ("Zen 4", "Raphael", 2023, "AM5", "DDR5-5200", 65, ["Ryzen 5 7600", "Ryzen 7 7700"]),
        
        # AMD Ryzen 7000 Mobile (Zen 4) - 2023
        "7940HS": ("Zen 4", "Phoenix", 2023, "FP8 (Mobile)", "DDR5-5200", 35, ["Upgrade laptop"]),
        "7840HS": ("Zen 4", "Phoenix", 2023, "FP8 (Mobile)", "DDR5-5200", 35, ["Upgrade laptop"]),
        "7735HS": ("Zen 3+", "Rembrandt", 2023, "FP7 (Mobile)", "DDR5-4800", 35, ["Upgrade laptop"]),
    }
    
    # Try to match CPU model using word boundaries for precision
    import re
    for model, cpu_data in CPU_DATABASE.items():
        # Use word boundaries to avoid substring matches (e.g., "6400" matching in "G6400")
        if re.search(r'\b' + re.escape(model.upper()) + r'\b', cpu_upper):
            # Handle both 7-value (without windows_compat) and 8-value (with windows_compat) tuples
            if len(cpu_data) == 8:
                gen, arch, year, socket, ram, tdp, windows_compat, upgrades = cpu_data
            elif len(cpu_data) == 7:
                gen, arch, year, socket, ram, tdp, upgrades = cpu_data
                windows_compat = "Windows 11 compatible"  # Default value for entries without explicit compatibility
            else:
                continue  # Skip malformed entries
            
            # Calculate age
            from datetime import datetime
            current_year = datetime.now().year
            age_years = current_year - year
            
            return {
                'generation': gen,
                'architecture': arch,
                'year': year,
                'age_years': age_years,
                'socket': socket,
                'max_ram_speed': ram,
                'tdp': tdp,
                'windows_compatibility': windows_compat,
                'upgrade_path': upgrades
            }

    # Fallback for unknown CPUs - basic Windows 11 compatibility detection
    logging.debug(f"CPU '{cpu_name}' not found in database, applying fallback logic")

    # Check AMD processors based on official Microsoft Windows 11 compatibility list
    # Source: https://learn.microsoft.com/en-us/windows-hardware/design/minimum/supported/windows-11-supported-amd-processors
    # Updated as of Windows 11 version checks
    if 'RYZEN' in cpu_upper or 'ATHLON' in cpu_upper or 'EPYC' in cpu_upper:
        # Extract model information for accurate compatibility checking
        import re

        # Windows 11 supported AMD processors (based on official Microsoft list):
        # - Ryzen 3000 series and newer (Zen 2+)
        # - Athlon 3000 series and newer
        # - EPYC 7000 series and newer
        # - Ryzen Embedded R2000 series and newer
        # - Ryzen PRO 3000 series and newer

        # First, try to extract 4-digit model number (most reliable)
        model_match = re.search(r'(?:RYZEN|ATHLON|EPYC)\s+\d+\s+(\d{4})', cpu_name, re.IGNORECASE)
        if model_match:
            model_number = int(model_match.group(1))
            model_series = model_number // 1000  # 5600 -> 5, 3200 -> 3, 2200 -> 2

            logging.debug(f"AMD model series detected: {model_series}000 (from '{cpu_name}')")

            # Windows 11 compatibility rules based on official Microsoft list
            if 'EPYC' in cpu_upper:
                # EPYC 7000 series and newer are Windows 11 compatible
                if model_series >= 7:  # 7000 series and newer
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"AMD EPYC {model_series}000 series - {windows_compat}")
                else:
                    windows_compat = "Windows 10 only"
                    logging.debug(f"AMD EPYC {model_series}000 series - {windows_compat}")
            elif 'ATHLON' in cpu_upper:
                # Athlon 3000 series and newer are Windows 11 compatible
                if model_series >= 3:  # 3000 series and newer
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"AMD Athlon {model_series}000 series - {windows_compat}")
                else:
                    windows_compat = "Windows 10 only"
                    logging.debug(f"AMD Athlon {model_series}000 series - {windows_compat}")
            else:  # Ryzen
                # Ryzen 3000 series and newer are Windows 11 compatible
                if model_series >= 3:  # 3000 series and newer (Zen 2+)
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"AMD Ryzen {model_series}000 series (Zen 2+) - {windows_compat}")
                else:
                    windows_compat = "Windows 10 only"
                    logging.debug(f"AMD Ryzen {model_series}000 series (Zen/Zen+) - {windows_compat}")
        else:
            # Fallback for CPUs without 4-digit model numbers
            # Check for specific known series or embedded/mobile indicators
            if 'EMBEDDED' in cpu_upper or 'PRO' in cpu_upper or 'R2' in cpu_upper:
                # Ryzen Embedded, Ryzen PRO, Ryzen R2000 series are generally Windows 11 compatible
                windows_compat = "Windows 11 compatible"
                logging.debug(f"AMD Embedded/PRO/R-series detected - {windows_compat}")
            elif 'Z1' in cpu_upper:
                # Ryzen Z1 is Windows 11 compatible
                windows_compat = "Windows 11 compatible"
                logging.debug(f"AMD Ryzen Z1 detected - {windows_compat}")
            else:
                # For unknown AMD CPUs, default to Windows 11 compatible (conservative approach)
                # Most modern AMD CPUs released after 2020 are Windows 11 compatible
                windows_compat = "Windows 11 compatible"
                logging.debug(f"Unknown AMD CPU '{cpu_name}' - defaulting to {windows_compat}")

        return {
            'generation': 'Unknown',
            'architecture': 'Unknown',
            'year': None,
            'age_years': None,
            'socket': 'AM4',  # Most modern AMD CPUs use AM4/AM5
            'max_ram_speed': 'Unknown',
            'tdp': None,
            'windows_compatibility': windows_compat,
            'upgrade_path': []
        }

    # Check Intel processors based on official Microsoft Windows 11 compatibility list
    # Source: https://learn.microsoft.com/en-us/windows-hardware/design/minimum/supported/windows-11-supported-intel-processors
    elif 'INTEL' in cpu_upper:
        import re

        # Windows 11 supported Intel processors (based on official Microsoft list):
        # - Core processors: 8th generation and newer (i3, i5, i7, i9, m)
        # - Celeron: 3000 series and newer
        # - Pentium Gold: 4000 series and newer
        # - Pentium Silver: J5000, N6000 series and newer
        # - Pentium: 6800 series and newer
        # - Xeon Scalable: All modern series (1st, 2nd, 3rd generation)
        # - Xeon D/E/W: Modern series (D-1700+, E-2100+, W-1200+, etc.)
        # - Core X-series: 7000X, 9000X, 10000X series

        # First, check for Xeon processors (generally Windows 11 compatible if modern)
        if 'XEON' in cpu_upper:
            # Check for modern Xeon series - be more inclusive
            if (any(series in cpu_upper for series in ['SCALABLE', 'D-17', 'D-18', 'D-27', 'D-28',
                                                      'E-21', 'E-22', 'E-23', 'E-24',
                                                      'W-10', 'W-11', 'W-12', 'W-13',
                                                      'W-21', 'W-22', 'W-31', 'W-33']) or
                'GOLD' in cpu_upper or 'SILVER' in cpu_upper or 'BRONZE' in cpu_upper or
                'PLATINUM' in cpu_upper):
                windows_compat = "Windows 11 compatible"
                logging.debug(f"Intel Xeon (modern series) detected - {windows_compat}")
            else:
                # Older Xeon series may not be Windows 11 compatible
                windows_compat = "Windows 10 only"
                logging.debug(f"Intel Xeon (older series) detected - {windows_compat}")

        # Check for Core processors (8th gen+)
        elif 'CORE' in cpu_upper:
            # Extract generation number - be more specific about the patterns
            # Match patterns like "i7-7700" and extract "7" as the generation
            gen_match = re.search(r'I(\d)-\d+|(\d+)(?:TH|ST|ND|RD)\s+GEN|(\d+)TH\s+GEN', cpu_name, re.IGNORECASE)
            if gen_match:
                # Debug which group matched
                logging.debug(f"Core gen_match groups: {gen_match.groups()}")
                gen_num = int(gen_match.group(1) or gen_match.group(2) or gen_match.group(3))
                if gen_num >= 8:  # 8th generation and newer
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"Intel Core {gen_num}th gen detected - {windows_compat}")
                else:
                    windows_compat = "Windows 10 only"
                    logging.debug(f"Intel Core {gen_num}th gen detected - {windows_compat}")
            elif any(series in cpu_upper for series in ['10000X', '9000X', '7000X']):
                # Core X-series are Windows 11 compatible
                windows_compat = "Windows 11 compatible"
                logging.debug(f"Intel Core X-series detected - {windows_compat}")
            else:
                # Unknown Core series - check if it might be 8th gen or newer by looking for patterns
                # Most modern Core processors without explicit gen numbers are likely compatible
                if any(indicator in cpu_upper for indicator in ['I3-', 'I5-', 'I7-', 'I9-', 'M3-', 'M5-', 'M7-', 'M9-']):
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"Intel Core (modern series) detected - {windows_compat}")
                else:
                    windows_compat = "Unknown"
                    logging.debug(f"Intel Core (unknown series) - {windows_compat}")

        # Check for Celeron processors (3000 series+)
        elif 'CELERON' in cpu_upper:
            # Extract series number - handle various formats like N6210, G5900, etc.
            series_match = re.search(r'CELERON.*?(\d+)', cpu_name, re.IGNORECASE)
            if series_match:
                series_num = int(series_match.group(1))
                if series_num >= 3000:  # 3000 series and newer
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"Intel Celeron {series_num} series detected - {windows_compat}")
                else:
                    windows_compat = "Windows 10 only"
                    logging.debug(f"Intel Celeron {series_num} series detected - {windows_compat}")
            else:
                # Unknown Celeron series - check for specific supported series patterns
                if any(supported in cpu_upper for supported in ['G4000', 'G5000', 'G6000', 'J4000', 'N4000', 'N5000',
                                                               'N6000', '3000', '4000', '5000', '6000', '7000']):
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"Intel Celeron (supported series) detected - {windows_compat}")
                else:
                    # Check for N-series specifically
                    alt_match = re.search(r'CELERON.*?(?:CPU\s+)?N(\d+)', cpu_name, re.IGNORECASE)
                    if alt_match:
                        series_num = int(alt_match.group(1))
                        if series_num >= 4000:  # N4000 and newer
                            windows_compat = "Windows 11 compatible"
                            logging.debug(f"Intel Celeron N{series_num} series detected - {windows_compat}")
                        else:
                            windows_compat = "Unknown"
                            logging.debug(f"Intel Celeron N{series_num} series - {windows_compat}")
                    else:
                        windows_compat = "Unknown"
                        logging.debug(f"Intel Celeron (unknown series) - {windows_compat}")

        # Check for Pentium processors
        elif 'PENTIUM' in cpu_upper:
            if 'SILVER' in cpu_upper:
                # Pentium Silver J5000, N6000 series and newer
                if any(series in cpu_upper for series in ['J5000', 'N6000', 'J500', 'N600']):
                    windows_compat = "Windows 11 compatible"
                    logging.debug(f"Intel Pentium Silver (supported series) detected - {windows_compat}")
                else:
                    # Check for specific model numbers like N6210
                    model_match = re.search(r'SILVER\s+(?:J|N)(\d+)', cpu_name, re.IGNORECASE)
                    if model_match:
                        model_num = int(model_match.group(1))
                        if model_num >= 5000:  # J5000, N5000 and newer
                            windows_compat = "Windows 11 compatible"
                            logging.debug(f"Intel Pentium Silver {model_match.group(0)} detected - {windows_compat}")
                        else:
                            windows_compat = "Unknown"
                            logging.debug(f"Intel Pentium Silver {model_match.group(0)} - {windows_compat}")
                    else:
                        windows_compat = "Unknown"
                        logging.debug(f"Intel Pentium Silver (unknown series) - {windows_compat}")
            elif 'GOLD' in cpu_upper:
                # Pentium Gold 4000 series and newer
                series_match = re.search(r'GOLD\s+(?:G)?(\d+)', cpu_name, re.IGNORECASE)
                if series_match:
                    series_num = int(series_match.group(1))
                    if series_num >= 4000:  # 4000 series and newer
                        windows_compat = "Windows 11 compatible"
                        logging.debug(f"Intel Pentium Gold {series_num} series detected - {windows_compat}")
                    else:
                        windows_compat = "Windows 10 only"
                        logging.debug(f"Intel Pentium Gold {series_num} series detected - {windows_compat}")
                else:
                    # Check for specific supported series
                    if any(supported in cpu_upper for supported in ['4000U', '4000Y', '5000', '6000', '6000Y',
                                                                   '7000', '8000', 'G5000', 'G7000']):
                        windows_compat = "Windows 11 compatible"
                        logging.debug(f"Intel Pentium Gold (supported series) detected - {windows_compat}")
                    else:
                        windows_compat = "Unknown"
                        logging.debug(f"Intel Pentium Gold (unknown series) - {windows_compat}")
            else:
                # Regular Pentium 6800 series and newer
                series_match = re.search(r'PENTIUM\s+(\d+)', cpu_name, re.IGNORECASE)
                if series_match:
                    series_num = int(series_match.group(1))
                    if series_num >= 6800:  # 6800 series and newer
                        windows_compat = "Windows 11 compatible"
                        logging.debug(f"Intel Pentium {series_num} series detected - {windows_compat}")
                    else:
                        windows_compat = "Windows 10 only"
                        logging.debug(f"Intel Pentium {series_num} series detected - {windows_compat}")
                else:
                    windows_compat = "Unknown"
                    logging.debug(f"Intel Pentium (unknown series) - {windows_compat}")

        # Check for Intel branded processors (300 series, U300 series)
        elif 'INTEL' in cpu_upper and ('PROCESSOR' in cpu_upper or 'U300' in cpu_upper or '300' in cpu_upper):
            if 'U300' in cpu_upper or '300' in cpu_upper:
                windows_compat = "Windows 11 compatible"
                logging.debug(f"Intel Processor U300/300 series detected - {windows_compat}")
            else:
                windows_compat = "Unknown"
                logging.debug(f"Intel Processor (unknown series) - {windows_compat}")

        # Unknown Intel processor
        else:
            windows_compat = "Unknown"
            logging.debug(f"Unknown Intel processor '{cpu_name}' - {windows_compat}")

        # Extract generation for return statement if available
        generation = 'Unknown'
        if 'gen_match' in locals() and gen_match:
            gen_num = int(gen_match.group(1) or gen_match.group(2) or gen_match.group(3))
            generation = f"{gen_num}th Gen"

        return {
            'generation': generation,
            'architecture': 'Unknown',
            'year': None,
            'age_years': None,
            'socket': 'Unknown',
            'max_ram_speed': 'Unknown',
            'tdp': None,
            'windows_compatibility': windows_compat,
            'upgrade_path': []
        }

    # For completely unknown CPUs, return minimal info
    logging.debug(f"CPU '{cpu_name}' completely unknown, returning minimal info")
    return {
        'generation': 'Unknown',
        'architecture': 'Unknown',
        'year': None,
        'age_years': None,
        'socket': 'Unknown',
        'max_ram_speed': 'Unknown',
        'tdp': None,
        'windows_compatibility': 'Unknown',
        'upgrade_path': []
    }


def _parse_cpu_boost_speed(cpu_name):
    """
    Parse CPU name and lookup known boost speeds for Intel/AMD CPUs.
    
    How it works:
    1. Extracts CPU model (e.g., 9700K, 9600X, 14900K)
    2. Looks up in database of known boost speeds
    3. Falls back to pattern matching in CPU name string
    
    Returns: boost speed in MHz or None
    """
    if not cpu_name:
        return None
    
    # Database of known CPU boost speeds (in MHz)
    # Format: "model_identifier": boost_mhz
    KNOWN_BOOST_SPEEDS = {
        # Intel 9th Gen (Coffee Lake Refresh)
        "9900K": 5000, "9900KF": 5000, "9900KS": 5000,
        "9700K": 4900, "9700KF": 4900,
        "9600K": 4600, "9600KF": 4600,
        
        # Intel 10th Gen (Comet Lake)
        "10900K": 5300, "10900KF": 5300,
        "10700K": 5100, "10700KF": 5100,
        "10600K": 4800, "10600KF": 4800,
        
        # Intel 10th Gen (Ice Lake - Mobile)
        "1035G1": 3600, "1035G4": 3700, "1035G7": 3700,
        "1065G7": 3900, "1068NG7": 3900,
        "10210U": 4200, "10310U": 4400, "10510U": 4900, "10710U": 4700,
        
        # Intel 11th Gen (Rocket Lake)
        "11900K": 5300, "11900KF": 5300,
        "11700K": 5000, "11700KF": 5000,
        "11600K": 4900, "11600KF": 4900,
        
        # Intel 12th Gen (Alder Lake)
        "12900K": 5200, "12900KF": 5200, "12900KS": 5500,
        "12700K": 5000, "12700KF": 5000,
        "12600K": 4900, "12600KF": 4900,
        
        # Intel 13th Gen (Raptor Lake)
        "13900K": 5800, "13900KF": 5800, "13900KS": 6000,
        "13700K": 5400, "13700KF": 5400,
        "13600K": 5100, "13600KF": 5100,
        
        # Intel 14th Gen (Raptor Lake Refresh)
        "14900K": 6000, "14900KF": 6000, "14900KS": 6200,
        "14700K": 5600, "14700KF": 5600,
        "14600K": 5300, "14600KF": 5300,
        
        # AMD Ryzen 5000 (Zen 3)
        "5950X": 4900, "5900X": 4800,
        "5800X": 4700, "5800X3D": 4500,
        "5600X": 4600, "5600": 4400,
        
        # AMD Ryzen 7000 (Zen 4)
        "7950X": 5700, "7950X3D": 5700,
        "7900X": 5400, "7900X3D": 5600,
        "7800X3D": 5000, "7700X": 5400,
        "7600X": 5300, "7600": 5100,
        
        # AMD Ryzen 9000 (Zen 5)
        "9950X": 5700, "9900X": 5600,
        "9700X": 5500, "9600X": 5400,
    }
    
    # Try to find model in database
    cpu_upper = cpu_name.upper()
    for model, boost_mhz in KNOWN_BOOST_SPEEDS.items():
        if model.upper() in cpu_upper:
            logging.debug(f"Found boost speed for {model}: {boost_mhz}MHz")
            return boost_mhz
    
    # Fallback: Try to extract GHz from name (some CPUs show it)
    # Example: "AMD Ryzen 5 9600X 3.9 GHz Boost 5.4 GHz"
    import re
    boost_patterns = [
        r'boost[:\s]+(\d+\.?\d*)\s*GHz',
        r'turbo[:\s]+(\d+\.?\d*)\s*GHz',
        r'max[:\s]+(\d+\.?\d*)\s*GHz',
    ]
    
    for pattern in boost_patterns:
        match = re.search(pattern, cpu_name, re.IGNORECASE)
        if match:
            boost_ghz = float(match.group(1))
            boost_mhz = int(boost_ghz * 1000)
            logging.debug(f"Parsed boost from name: {boost_mhz}MHz")
            return boost_mhz
    
    return None

def _get_cpu_info(com_wmi):
    """Get enhanced CPU information with cores, threads, and accurate clock speeds using COM/WMI"""
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_Processor")
                if items and items.Count > 0:
                    cpu = items.ItemIndex(0)
                    name = cpu.Properties_("Name").Value
                    if name:
                        name = name.strip()
                    cores = cpu.Properties_("NumberOfCores").Value or 0
                    threads = cpu.Properties_("NumberOfLogicalProcessors").Value or 0
                    
                    # Get clock speeds - MaxClockSpeed is often the turbo/boost speed
                    try:
                        max_clock_mhz = int(cpu.Properties_("MaxClockSpeed").Value) if cpu.Properties_("MaxClockSpeed").Value else None
                        current_clock_mhz = int(cpu.Properties_("CurrentClockSpeed").Value) if cpu.Properties_("CurrentClockSpeed").Value else None
                    except (ValueError, TypeError, AttributeError):
                        max_clock_mhz = None
                        current_clock_mhz = None
                    
                    # Base clock from registry (most accurate for base)
                    base_clock_mhz = _get_base_clock_from_registry(name)
                    
                    # Try to get boost speed from CPU database or name parsing
                    boost_clock_mhz = _parse_cpu_boost_speed(name)
                    
                    # Build clock info string with clear labeling
                    clock_info = ""
                    
                    # Base clock (from registry)
                    if base_clock_mhz:
                        base_ghz = base_clock_mhz / 1000.0
                        clock_info = f" | Base: {base_ghz:.2f} GHz"
                    
                    # Boost/Turbo clock (prioritize parsed database, fallback to WMI MaxClockSpeed)
                    if boost_clock_mhz:
                        # Use parsed boost speed from database
                        boost_ghz = boost_clock_mhz / 1000.0
                        clock_info += f" | Boost: {boost_ghz:.2f} GHz"
                    elif max_clock_mhz and max_clock_mhz != base_clock_mhz:
                        # Fallback to WMI MaxClockSpeed if parser didn't find it
                        boost_ghz = max_clock_mhz / 1000.0
                        clock_info += f" | Turbo: {boost_ghz:.2f} GHz"
                    
                    # Current clock (actual running speed) - only show if significantly different
                    if current_clock_mhz:
                        current_ghz = current_clock_mhz / 1000.0
                        # Only show if different from base/boost to reduce clutter
                        if current_clock_mhz != base_clock_mhz and current_clock_mhz != boost_clock_mhz and current_clock_mhz != max_clock_mhz:
                            clock_info += f" | Current: {current_ghz:.2f} GHz"
                    
                    return f"{name}{clock_info} ({cores}C/{threads}T)"
            except Exception as e:
                logging.debug(f"Failed to get CPU info via COM/WMI: {e}")
        
        # Final fallback
        processor = platform.processor()
        if processor:
            return processor
        return "Unknown"
    except Exception as e:
        logging.warning(f"Failed to get CPU info: {e}")
        return "Unknown"


def _translate_ram_manufacturer(manufacturer_code):
    """
    Translate JEDEC manufacturer codes to human-readable brand names.
    
    WMI often returns hex codes (e.g., "8A76") or partial strings (e.g., "80CE").
    This function maps them to actual brand names like "Corsair", "G.Skill", etc.
    """
    if not manufacturer_code:
        return "Unknown"
    
    # Clean the input
    mfr = str(manufacturer_code).strip().upper()
    
    # JEDEC Standard Manufacturer Codes
    # Format: hex code or common WMI string → Brand name
    JEDEC_CODES = {
        # Common RAM brands
        "859B": "Crucial",
        "80AD": "SK Hynix",
        "80CE": "Samsung",
        "802C": "Micron",
        "8551": "Qimonda",
        "869E": "AMD",
        "8A76": "Corsair",  # Your RAM!
        "04CD": "G.Skill",
        "0198": "Kingston",
        "029E": "Corsair",
        "04CB": "A-DATA",
        "0420": "Nanya",
        "00CE": "Samsung",
        "00AD": "SK Hynix",
        "002C": "Micron",
        
        # Additional common codes
        "017A": "Apacer",
        "04C3": "PNY",
        "04EF": "Transcend",
        "0502": "Aeneon",
        "0530": "OCZ",
        "059B": "Crucial",
        "04CD00000000": "G.Skill",
        "029E00000000": "Corsair",
        "019800000000": "Kingston",
        
        # OEM/Generic
        "9801": "Kingston (OEM)",
        "0B03": "Unknown OEM",
        "0D00": "Unknown OEM",
    }
    
    # Check for exact match first
    if mfr in JEDEC_CODES:
        return JEDEC_CODES[mfr]
    
    # Check if manufacturer string already contains a brand name
    brand_keywords = {
        "CORSAIR": "Corsair",
        "G.SKILL": "G.Skill",
        "GSKILL": "G.Skill",
        "KINGSTON": "Kingston",
        "CRUCIAL": "Crucial",
        "SAMSUNG": "Samsung",
        "HYNIX": "SK Hynix",
        "MICRON": "Micron",
        "ADATA": "A-DATA",
        "PNY": "PNY",
        "PATRIOT": "Patriot",
        "TEAMGROUP": "Team Group",
        "MUSHKIN": "Mushkin",
    }
    
    for keyword, brand in brand_keywords.items():
        if keyword in mfr:
            return brand
    
    # If still unrecognized, return the original code with indicator
    if len(mfr) <= 8 and all(c in '0123456789ABCDEF' for c in mfr):
        return f"{mfr} (Unknown)"  # Looks like a hex code we don't recognize
    
    return mfr  # Return as-is if it's not a hex code


def _get_ram_info(com_wmi):
    """
    Get comprehensive RAM information with per-DIMM details in one combined string.
    
    Format: "Total | Type @ Speed (X modules) - Usage% used, Available GB available
             DIMM A: XGB @ Speed - Manufacturer Part#
             DIMM B: XGB @ Speed - Manufacturer Part#"
    """
    try:
        # Get total RAM and usage
        ram_total_gb = round(psutil.virtual_memory().total / (1024**3), 2)
        ram_used_gb = round(psutil.virtual_memory().used / (1024**3), 2)
        ram_available_gb = round(psutil.virtual_memory().available / (1024**3), 2)
        ram_percent = psutil.virtual_memory().percent
        
        ram_summary = f"{ram_total_gb} GB"
        ram_module_lines = []
        
        # Get detailed RAM module info via COM/WMI
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_PhysicalMemory")
                if items and items.Count > 0:
                    # Get type/speed from first module for summary
                    first_module = items.ItemIndex(0)
                    ram_type_code = first_module.Properties_("SMBIOSMemoryType").Value
                    ram_speed = first_module.Properties_("Speed").Value or 0
                    
                    ram_type_map = {
                        20: "DDR",
                        21: "DDR2",
                        24: "DDR3",
                        26: "DDR4",
                        34: "DDR5"
                    }
                    ram_type = ram_type_map.get(ram_type_code, "")
                    module_count = items.Count
                    used_slot_labels = set()
                    
                    # Build summary line
                    if ram_type and ram_speed:
                        ram_summary = f"{ram_total_gb} GB {ram_type} @ {ram_speed}MHz ({module_count} module{'s' if module_count > 1 else ''})"
                    elif ram_type:
                        ram_summary = f"{ram_total_gb} GB {ram_type} ({module_count} module{'s' if module_count > 1 else ''})"
                    
                    # Build per-DIMM details
                    for i in range(items.Count):
                        module = items.ItemIndex(i)
                        try:
                            slot = _normalize_ram_slot_label(
                                module.Properties_("DeviceLocator").Value,
                                module.Properties_("BankLabel").Value,
                                i,
                                used_slot_labels,
                            )
                            capacity_bytes = module.Properties_("Capacity").Value
                            capacity_gb = round(int(capacity_bytes) / (1024**3), 0) if capacity_bytes else 0
                            speed = module.Properties_("ConfiguredClockSpeed").Value or module.Properties_("Speed").Value or 0
                            manufacturer_raw = module.Properties_("Manufacturer").Value or ""
                            manufacturer_raw = manufacturer_raw.strip() if manufacturer_raw else ""
                            manufacturer = _translate_ram_manufacturer(manufacturer_raw)
                            part_number = module.Properties_("PartNumber").Value or ""
                            part_number = part_number.strip() if part_number else ""
                            
                            # Format with indentation to visually align with first line in GUI
                            # Need more spaces to reach where data starts (approximately 70 characters based on "RAM:" label position)
                            dimm_line = f"{slot}: {int(capacity_gb)}GB"
                            if speed:
                                dimm_line += f" @ {speed}MHz"
                            if manufacturer and manufacturer != "Unknown":
                                dimm_line += f" - {manufacturer}"
                                if part_number and part_number != manufacturer and part_number != manufacturer_raw:
                                    dimm_line += f" {part_number}"
                            
                            ram_module_lines.append(dimm_line)
                        except Exception as e:
                            logging.debug(f"Could not parse DIMM {i}: {e}")
                            continue
                    
            except Exception as e:
                logging.debug(f"Could not get RAM module details: {e}")
        
        # Add usage info to summary
        ram_summary += f" - {ram_percent:.1f}% used, {ram_available_gb:.1f} GB available"
        
        # Return summary only in main field - per-DIMM details will be shown separately
        return ram_summary
            
    except Exception as e:
        logging.warning(f"Failed to get RAM info: {e}")
        ram_total_gb = round(psutil.virtual_memory().total / (1024**3), 2)
        return f"{ram_total_gb} GB"


def _analyze_ram_compatibility(ram_details, cpu_name="", system_type="", total_slots=None):
    """
    Analyze RAM configuration and report FACTUAL observations only.
    No recommendations - just report what the hardware shows.
    
    Args:
        ram_details: List of RAM module dicts
        cpu_name: CPU name string for context
        system_type: System type (not used yet)
        total_slots: Total RAM slots on motherboard
    
    Returns: List of observation dictionaries with 'level' and 'message'
    """
    observations = []
    
    if not ram_details or len(ram_details) == 0:
        return observations
    
    # Extract properties
    speeds = [m['configured_speed'] for m in ram_details if m['configured_speed']]
    rated_speeds = [m['speed'] for m in ram_details if m['speed']]
    manufacturers = [m['manufacturer'] for m in ram_details]
    sizes = [m['size_gb'] for m in ram_details]
    types = [m['type'] for m in ram_details]
    voltages = [m['voltage'] for m in ram_details if m['voltage']]
    form_factors = [m.get('form_factor', 'Unknown') for m in ram_details]
    
    module_count = len(ram_details)
    current_total_gb = sum(sizes)
    
    # FACT 1: Memory channel configuration
    if module_count == 1:
        observations.append({
            'level': "INFO",
            'label': "Configuration:",
            'value': f"Single-channel ({module_count} module, {current_total_gb}GB total)"
        })
    elif module_count == 2:
        observations.append({
            'level': "INFO",
            'label': "Configuration:",
            'value': f"Dual-channel ({module_count} modules, {current_total_gb}GB total)"
        })
    elif module_count == 4:
        observations.append({
            'level': "INFO",
            'label': "Configuration:",
            'value': f"Quad-channel ({module_count} modules, {current_total_gb}GB total)"
        })
    elif module_count == 3:
        observations.append({
            'level': "INFO",
            'label': "Configuration:",
            'value': f"Asymmetric ({module_count} modules, {current_total_gb}GB total)"
        })
    
    # FACT 2: Speed differential (if RAM not running at SPD rated speed)
    if speeds and rated_speeds:
        configured_speed = speeds[0]
        rated_speed = rated_speeds[0]
        if configured_speed and rated_speed and (rated_speed - configured_speed) > 100:
            observations.append({
                'level': "INFO",
                'label': "Speed:",
                'value': f"{configured_speed}MHz configured ({rated_speed}MHz rated)"
            })
    
    # FACT 3: Empty slots (if we know total)
    if total_slots and module_count < total_slots:
        empty_slots = total_slots - module_count
        observations.append({
            'level': "INFO",
            'label': "Slot Usage:",
            'value': f"{module_count} of {total_slots} occupied ({empty_slots} empty)"
        })
    
    # FACT 4: Form factor and removability
    unique_form_factors = set(form_factors)
    if "Soldered" in unique_form_factors:
        observations.append({
            'level': "INFO",
            'label': "Form Factor:",
            'value': "Soldered (not user-replaceable)"
        })
    elif "SODIMM" in unique_form_factors:
        observations.append({
            'level': "INFO",
            'label': "Form Factor:",
            'value': "SODIMM (user-replaceable)"
        })
    elif "DIMM" in unique_form_factors:
        observations.append({
            'level': "INFO",
            'label': "Form Factor:",
            'value': "DIMM (user-replaceable)"
        })
    
    # CRITICAL ISSUE 1: Mismatched RAM types (DDR4 + DDR5 = won't work)
    unique_types = set(types)
    if len(unique_types) > 1:
        observations.append({
            'level': "CRITICAL",
            'label': "Issue:",
            'value': f"Incompatible RAM types ({', '.join(sorted(unique_types))}) - system unstable"
        })
    
    # CRITICAL ISSUE 2: Mismatched speeds
    unique_speeds = set(speeds)
    if len(unique_speeds) > 1:
        observations.append({
            'level': "CRITICAL",
            'label': "Issue:",
            'value': f"Mismatched speeds ({', '.join(f'{s}MHz' for s in sorted(unique_speeds))}) - may cause instability"
        })
    
    # WARNING 1: Mismatched manufacturers
    unique_manufacturers = set(m for m in manufacturers if m and m != "Unknown")
    if len(unique_manufacturers) > 1:
        observations.append({
            'level': "WARNING",
            'label': "Notice:",
            'value': f"Mixed brands ({', '.join(sorted(unique_manufacturers))})"
        })
    
    # WARNING 2: Mismatched sizes
    unique_sizes = set(sizes)
    if len(unique_sizes) > 1:
        observations.append({
            'level': "WARNING",
            'label': "Notice:",
            'value': f"Mixed capacities ({', '.join(f'{s}GB' for s in sorted(unique_sizes))})"
        })
    
    # WARNING 3: Voltage mismatches
    if len(voltages) > 1:
        unique_voltages = set(voltages)
        if len(unique_voltages) > 1:
            observations.append({
                'level': "WARNING",
                'label': "Notice:",
                'value': f"Mixed voltages ({', '.join(f'{v:.2f}V' for v in sorted(unique_voltages))})"
            })
    
    return observations


def _get_ram_slot_count(com_wmi):
    """Get total RAM slot count from motherboard (for empty slot detection)"""
    try:
        if not com_wmi:
            return None
        
        # Query PhysicalMemoryArray for slot capacity
        items = _query_com_wmi(com_wmi, "Win32_PhysicalMemoryArray")
        if items and items.Count > 0:
            array = items.ItemIndex(0)
            mem_devices = array.Properties_("MemoryDevices").Value
            if mem_devices:
                return int(mem_devices)
    except Exception as e:
        logging.debug(f"Could not get RAM slot count: {e}")
    
    return None


def _get_ram_details(com_wmi):
    """Get detailed per-DIMM RAM information - facts only, no assumptions"""
    try:
        if not com_wmi:
            return None
        
        items = _query_com_wmi(com_wmi, "Win32_PhysicalMemory")
        if not items or items.Count == 0:
            return None
        
        modules = []
        ram_type_map = {
            20: "DDR",
            21: "DDR2",
            24: "DDR3",
            26: "DDR4",
            34: "DDR5"
        }
        used_slot_labels = set()
        
        # Form factor map (key for determining if RAM is removable)
        form_factor_map = {
            8: "DIMM",          # Desktop - always removable
            12: "SODIMM",       # Laptop - usually removable
            13: "FB-DIMM",      # Server
            14: "Soldered",     # Soldered - NOT removable
            0: "Unknown"
        }
        
        for i in range(items.Count):
            module = items.ItemIndex(i)
            
            try:
                # Slot location
                device_locator = module.Properties_("DeviceLocator").Value or "Unknown"
                bank_label = module.Properties_("BankLabel").Value or ""
                slot_label = _normalize_ram_slot_label(
                    device_locator,
                    bank_label,
                    i,
                    used_slot_labels,
                )
                
                # Size
                capacity_bytes = module.Properties_("Capacity").Value
                capacity_gb = round(int(capacity_bytes) / (1024**3), 0) if capacity_bytes else 0
                
                # Type
                ram_type_code = module.Properties_("SMBIOSMemoryType").Value
                ram_type = ram_type_map.get(ram_type_code, f"Type {ram_type_code}")
                
                # Form Factor (critical for upgrade feasibility)
                form_factor_code = module.Properties_("FormFactor").Value
                form_factor = form_factor_map.get(form_factor_code, "Unknown")
                
                # Speed
                speed = module.Properties_("Speed").Value or 0
                configured_speed = module.Properties_("ConfiguredClockSpeed").Value or speed
                
                # Manufacturer and Part Number
                manufacturer_raw = module.Properties_("Manufacturer").Value or "Unknown"
                manufacturer_raw = manufacturer_raw.strip() if manufacturer_raw else "Unknown"
                manufacturer = _translate_ram_manufacturer(manufacturer_raw)
                part_number = module.Properties_("PartNumber").Value or "Unknown"
                part_number = part_number.strip() if part_number else "Unknown"
                
                # Voltage
                try:
                    voltage_mv = module.Properties_("ConfiguredVoltage").Value
                    voltage_v = voltage_mv / 1000.0 if voltage_mv else None
                except:
                    voltage_v = None
                
                # Build module info
                module_info = {
                    'slot': slot_label,
                    'bank': bank_label,
                    'size_gb': int(capacity_gb),
                    'type': ram_type,
                    'form_factor': form_factor,
                    'speed': speed,
                    'configured_speed': configured_speed,
                    'manufacturer': manufacturer,
                    'part_number': part_number,
                    'voltage': voltage_v
                }
                
                modules.append(module_info)
                
            except Exception as e:
                logging.debug(f"Failed to get details for RAM module {i}: {e}")
                continue
        
        return modules if modules else None
        
    except Exception as e:
        logging.warning(f"Failed to get detailed RAM info: {e}")
        return None


def _get_gpu_info(com_wmi):
    """Get enhanced GPU information with VRAM and driver details using COM/WMI"""
    gpu_list = []
    
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_VideoController")
                if items and items.Count > 0:
                    for i in range(items.Count):
                        gpu = items.ItemIndex(i)
                        name = gpu.Properties_("Name").Value
                        if not name:
                            continue
                        
                        # Enhance Intel GPU names to be more specific
                        gpu_name_clean = name.strip()
                        if "Intel" in gpu_name_clean:
                            # Extract specific Intel GPU model
                            intel_gpu_patterns = [
                                (r'Intel.*UHD\s+Graphics\s+(\d+)', r'Intel UHD Graphics \1'),
                                (r'Intel.*Iris.*Xe\s+Graphics', 'Intel Iris Xe Graphics'),
                                (r'Intel.*Iris.*Pro\s+Graphics\s+(\d+)', r'Intel Iris Pro Graphics \1'),
                                (r'Intel.*Iris\s+Graphics\s+(\d+)', r'Intel Iris Graphics \1'),
                                (r'Intel.*HD\s+Graphics\s+(\d+)', r'Intel HD Graphics \1'),
                                (r'Intel.*HD\s+(\d+)', r'Intel HD Graphics \1'),
                                (r'Intel.*Graphics\s+(\d+)', r'Intel Graphics \1'),
                                (r'Intel.*Iris', 'Intel Iris Graphics'),
                                (r'Intel.*UHD', 'Intel UHD Graphics'),
                                (r'Intel.*HD', 'Intel HD Graphics'),
                            ]
                            
                            for pattern, replacement in intel_gpu_patterns:
                                match = re.search(pattern, gpu_name_clean, re.IGNORECASE)
                                if match:
                                    if '\\1' in replacement:
                                        gpu_name_clean = match.expand(replacement)
                                    else:
                                        gpu_name_clean = replacement
                                    break
                            
                            # If still just "Intel", try to get more info from PNPDeviceID or Description
                            if gpu_name_clean.lower() == "intel" or gpu_name_clean == "Intel":
                                try:
                                    pnp_id = gpu.Properties_("PNPDeviceID").Value
                                    description = gpu.Properties_("Description").Value or ""
                                    
                                    # Try to extract from PNP ID (format: PCI\\VEN_8086&DEV_xxxx)
                                    if pnp_id and "VEN_8086" in pnp_id:
                                        dev_match = re.search(r'DEV_([0-9A-F]{4})', pnp_id)
                                        if dev_match:
                                            # Look up common Intel GPU device IDs
                                            dev_id = dev_match.group(1).upper()
                                            intel_gpu_ids = {
                                                # UHD Graphics 600/605/610/615/620/630 (7th/8th gen)
                                                "3184": "Intel UHD Graphics 600",
                                                "3185": "Intel UHD Graphics 605",
                                                "5902": "Intel UHD Graphics 610",
                                                "5906": "Intel UHD Graphics 615",
                                                "5917": "Intel UHD Graphics 620",
                                                "5912": "Intel UHD Graphics 630",
                                                # UHD Graphics (Ice Lake - 10th gen mobile)
                                                "8A56": "Intel UHD Graphics",  # Ice Lake U
                                                "8A57": "Intel UHD Graphics",  # Ice Lake U
                                                "8A58": "Intel UHD Graphics",  # Ice Lake U
                                                "8A59": "Intel UHD Graphics",  # Ice Lake U
                                                "8A5A": "Intel UHD Graphics",  # Ice Lake U
                                                "8A5B": "Intel UHD Graphics",  # Ice Lake U
                                                "8A5C": "Intel UHD Graphics",  # Ice Lake U
                                                "8A5D": "Intel UHD Graphics",  # Ice Lake U
                                                "8A71": "Intel Iris Plus Graphics",  # Ice Lake G7
                                                "8A72": "Intel Iris Plus Graphics",  # Ice Lake G7
                                                "8A73": "Intel Iris Plus Graphics",  # Ice Lake G7
                                                # UHD Graphics (Tiger Lake - 11th gen)
                                                "9A49": "Intel UHD Graphics",  # Tiger Lake U
                                                "9A59": "Intel UHD Graphics",  # Tiger Lake U
                                                "9A60": "Intel UHD Graphics",  # Tiger Lake U
                                                "9A68": "Intel UHD Graphics",  # Tiger Lake U
                                                "9A70": "Intel UHD Graphics",  # Tiger Lake U
                                                # Iris Xe (Tiger Lake)
                                                "9A40": "Intel Iris Xe Graphics",
                                                "9A49": "Intel Iris Xe Graphics",  # Also UHD
                                                "9A4C": "Intel Iris Xe Graphics",
                                                "9A78": "Intel Iris Xe Graphics",
                                                # HD Graphics 4000/4400/4600/5000/5200/5300 (3rd/4th gen)
                                                "0166": "Intel HD Graphics 4000",
                                                "0A16": "Intel HD Graphics 4400",
                                                "0A26": "Intel HD Graphics 4600",
                                                "0A2E": "Intel HD Graphics 5000",
                                                "0D26": "Intel HD Graphics 5200",
                                                "0D22": "Intel HD Graphics 5300",
                                                # HD Graphics 510/515/520/530 (6th gen)
                                                "1916": "Intel HD Graphics 510",
                                                "191E": "Intel HD Graphics 515",
                                                "1926": "Intel HD Graphics 520",
                                                "1927": "Intel HD Graphics 530",
                                            }
                                            if dev_id in intel_gpu_ids:
                                                gpu_name_clean = intel_gpu_ids[dev_id]
                                    
                                    # Fallback to Description if available
                                    if (gpu_name_clean.lower() == "intel" or gpu_name_clean == "Intel") and description:
                                        desc_lower = description.lower()
                                        if "uhd" in desc_lower:
                                            # Try to extract model number
                                            uhd_match = re.search(r'uhd\s+graphics\s+(\d+)', desc_lower)
                                            if uhd_match:
                                                gpu_name_clean = f"Intel UHD Graphics {uhd_match.group(1)}"
                                            else:
                                                gpu_name_clean = "Intel UHD Graphics"
                                        elif "iris" in desc_lower:
                                            if "xe" in desc_lower:
                                                gpu_name_clean = "Intel Iris Xe Graphics"
                                            elif "plus" in desc_lower:
                                                gpu_name_clean = "Intel Iris Plus Graphics"
                                            elif "pro" in desc_lower:
                                                gpu_name_clean = "Intel Iris Pro Graphics"
                                            else:
                                                gpu_name_clean = "Intel Iris Graphics"
                                        elif "hd graphics" in desc_lower or ("hd" in desc_lower and "graphics" in desc_lower):
                                            # Try to extract model number
                                            hd_match = re.search(r'hd\s+graphics\s+(\d+)', desc_lower)
                                            if hd_match:
                                                gpu_name_clean = f"Intel HD Graphics {hd_match.group(1)}"
                                            else:
                                                gpu_name_clean = "Intel HD Graphics"
                                    
                                    # Last resort: If still just "Intel", use generic name based on CPU generation
                                    if (gpu_name_clean.lower() == "intel" or gpu_name_clean == "Intel"):
                                        # Try to infer from CPU if available (Ice Lake = UHD, Tiger Lake = Iris Xe/UHD)
                                        gpu_name_clean = "Intel UHD Graphics"  # Default fallback
                                        
                                except Exception as e:
                                    logging.debug(f"Error enhancing Intel GPU name: {e}")
                                    # If all else fails, use generic name
                                    if gpu_name_clean.lower() == "intel" or gpu_name_clean == "Intel":
                                        gpu_name_clean = "Intel UHD Graphics"
                        
                        gpu_info = gpu_name_clean
                        
                        # Get VRAM - WMI AdapterRAM overflows on modern GPUs (>4GB)
                        # Try multiple methods for accurate VRAM detection
                        vram_gb = None
                        vram = gpu.Properties_("AdapterRAM").Value
                        
                        if vram:
                            vram_bytes = int(vram)
                            # Check if AdapterRAM is valid (not negative/overflowed)
                            if vram_bytes > 0 and vram_bytes < 4294967296:  # Less than 4GB = likely accurate
                                vram_gb = round(vram_bytes / (1024**3), 1)
                            else:
                                # WMI overflow detected - try alternative methods
                                logging.debug(f"WMI AdapterRAM overflow detected ({vram_bytes}), trying alternatives")
                                vram_gb = _get_gpu_vram_fallback(name.strip())
                        else:
                            # WMI returned null - try alternative methods
                            vram_gb = _get_gpu_vram_fallback(name.strip())
                        
                        if vram_gb and vram_gb > 0:
                            gpu_info += f" ({vram_gb} GB VRAM)"
                        
                        # Get driver version
                        driver_version = gpu.Properties_("DriverVersion").Value
                        if driver_version:
                            gpu_info += f" - Driver: {driver_version}"
                        
                        # Get driver date
                        driver_date = gpu.Properties_("DriverDate").Value
                        if driver_date:
                            try:
                                # WMI dates are in format: YYYYMMDDHHMMSS.ffffff-###
                                date_str = str(driver_date).split('.')[0]
                                if len(date_str) >= 8:
                                    year = date_str[:4]
                                    month = date_str[4:6]
                                    day = date_str[6:8]
                                    gpu_info += f" ({month}/{day}/{year})"
                            except Exception:
                                pass
                        
                        gpu_list.append(gpu_info)
            except Exception as e:
                logging.debug(f"Failed to get GPU info via COM/WMI: {e}")
        
        if gpu_list:
            return ", ".join(gpu_list)
        return "Unknown"
    except Exception as e:
        logging.warning(f"Failed to get GPU info: {e}")
    return "Unknown"


def _parse_gpu_details(gpu_string):
    """Parse the GPU info string into a structured GPUDetails dict.

    The GPU string looks like:
        "NVIDIA Quadro T1000 (4.0 GB VRAM) - Driver: 27.21.14.5167 (07/05/2020)"
    Returns dict with: driver_version, driver_date, driver_age_years, vram
    """
    details = {}
    if not gpu_string or gpu_string == 'Unknown':
        return details

    # Driver version
    ver_m = re.search(r'Driver:\s*([\d.]+)', gpu_string)
    if ver_m:
        details['driver_version'] = ver_m.group(1)

    # Driver date — MM/DD/YYYY format
    date_m = re.search(r'\((\d{2}/\d{2}/\d{4})\)', gpu_string)
    if date_m:
        details['driver_date'] = date_m.group(1)
        try:
            from datetime import datetime
            d = datetime.strptime(date_m.group(1), '%m/%d/%Y')
            age_days = (datetime.now() - d).days
            details['driver_age_years'] = round(age_days / 365.25, 1)
        except Exception:
            pass

    # VRAM
    vram_m = re.search(r'([\d.]+)\s*GB\s*VRAM', gpu_string)
    if vram_m:
        details['vram'] = f"{vram_m.group(1)} GB"

    return details


def _get_gpu_detailed_metrics(gpu_name):
    """
    Get detailed GPU metrics (temperature, clocks, power, utilization).
    Works for NVIDIA (via nvidia-smi) and AMD (via PowerShell/WMI).

    Returns dict with: temperature, core_clock, memory_clock, power_draw, utilization, vram_gb
    """
    metrics = {}
    
    if platform.system() != "Windows":
        return metrics
    
    # NVIDIA: Use nvidia-smi (most comprehensive)
    if "NVIDIA" in gpu_name.upper():
        try:
            result = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=temperature.gpu,clocks.current.graphics,clocks.current.memory,power.draw,utilization.gpu,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            if result.returncode == 0 and result.stdout.strip():
                # Parse: temp, core_clock, mem_clock, power, util, vram
                parts = result.stdout.strip().split(',')
                if len(parts) >= 6:
                    try:
                        metrics['temperature'] = int(parts[0].strip())
                        metrics['temperature_sensor'] = 'GPU Core (nvidia-smi)'
                        metrics['core_clock'] = int(parts[1].strip())
                        metrics['memory_clock'] = int(parts[2].strip())
                        metrics['power_draw'] = float(parts[3].strip())
                        metrics['utilization'] = int(parts[4].strip())
                        metrics['vram_gb'] = round(float(parts[5].strip()) / 1024, 1)
                        # Log at DEBUG level - monitoring thread will sample this every 3s
                        logging.debug(f"NVIDIA GPU metrics: {metrics}")
                        return metrics
                    except (ValueError, IndexError) as e:
                        logging.debug(f"Failed to parse nvidia-smi output: {e}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logging.debug(f"nvidia-smi not available: {e}")
    
    # AMD: Use PowerShell with WMI and Performance Counters
    elif "AMD" in gpu_name.upper() or "RADEON" in gpu_name.upper():
        try:
            # AMD GPU metrics via PowerShell
            ps_script = '''
            $gpu = Get-CimInstance -ClassName Win32_VideoController | Where-Object {$_.Name -like "*AMD*" -or $_.Name -like "*Radeon*"} | Select-Object -First 1
            
            # Try to get temperature from WMI (may not always be available)
            $temp = $null
            try {
                $thermal = Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue
                if ($thermal) {
                    $temp = [math]::Round(($thermal.CurrentTemperature - 2732) / 10, 0)
                }
            } catch {}
            
            # Get current resolution and refresh rate (indirect perf indicator)
            $refreshRate = $gpu.CurrentRefreshRate
            $resolution = "$($gpu.CurrentHorizontalResolution)x$($gpu.CurrentVerticalResolution)"
            
            # Output as JSON
            @{
                temperature = $temp
                refresh_rate = $refreshRate
                resolution = $resolution
            } | ConvertTo-Json -Compress
            '''
            
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            
            if result.returncode == 0 and result.stdout.strip():
                amd_data = json.loads(result.stdout.strip())
                if amd_data.get('temperature'):
                    metrics['temperature'] = amd_data['temperature']
                    metrics['temperature_sensor'] = 'ACPI Thermal Zone'
                logging.info(f"AMD GPU metrics: {metrics}")
                return metrics
        except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError, Exception) as e:
            logging.debug(f"AMD GPU metrics query failed: {e}")
    
    return metrics


def _get_gpu_vram_fallback(gpu_name):
    """Fallback method to get accurate GPU VRAM using nvidia-smi or Registry"""
    if platform.system() != "Windows":
        return None
    
    # Method 1: Try detailed metrics first (includes VRAM)
    metrics = _get_gpu_detailed_metrics(gpu_name)
    if metrics.get('vram_gb'):
        return metrics['vram_gb']
    
    # Method 2: Try nvidia-smi for VRAM only (fallback for NVIDIA)
    if "NVIDIA" in gpu_name.upper():
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            if result.returncode == 0 and result.stdout.strip():
                vram_mb = float(result.stdout.strip())
                vram_gb = round(vram_mb / 1024, 1)
                logging.info(f"GPU VRAM detected via nvidia-smi: {vram_gb} GB")
                return vram_gb
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            logging.debug("nvidia-smi not available, trying registry method")
    
    # Method 3: Try Registry query (works for all GPUs)
    try:
        ps_script = '''
        $gpu = Get-ItemProperty -Path "HKLM:\\SYSTEM\\ControlSet001\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*" -ErrorAction SilentlyContinue | 
            Where-Object {$_.DriverDesc -like "*''' + gpu_name + '''*"} | 
            Select-Object -First 1 -ExpandProperty "HardwareInformation.qwMemorySize" -ErrorAction SilentlyContinue
        
        if ($gpu) {
            [math]::Round($gpu / 1GB, 1)
        }
        '''
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            vram_gb = float(result.stdout.strip())
            if vram_gb > 0:
                logging.info(f"GPU VRAM detected via Registry: {vram_gb} GB")
                return vram_gb
    except (subprocess.TimeoutExpired, ValueError, Exception) as e:
        logging.debug(f"Registry VRAM query failed: {e}")
    
    logging.warning(f"Could not determine accurate VRAM for {gpu_name}")
    return None


# PowerShell GPU function removed - using COM/WMI only


def _extract_chipset_from_motherboard(mobo_model):
    """
    Extract chipset from motherboard model string.
    
    Examples:
    - "Gigabyte Z370M D3H-CF" → "Z370"
    - "ASUS ROG STRIX B450-F GAMING" → "B450"
    - "MSI MPG X570 GAMING EDGE WIFI" → "X570"
    """
    if not mobo_model:
        return None
    
    mobo_upper = mobo_model.upper()
    
    # Intel chipsets (from newest to oldest to match longer patterns first)
    intel_chipsets = [
        # 14th Gen (Raptor Lake Refresh) - 2023-2024
        "Z790", "H770", "B760",
        # 13th/12th Gen (Alder/Raptor Lake) - 2021-2023
        "Z690", "H670", "B660", "H610",
        # 11th Gen (Rocket Lake) - 2021
        "Z590", "H570", "B560",
        # 10th Gen (Comet Lake) - 2020
        "Z490", "H470", "B460", "H410",
        # 9th/8th Gen (Coffee Lake) - 2017-2019
        "Z390", "Z370", "H370", "B365", "B360", "H310",
        # 7th/6th Gen (Kaby/Skylake) - 2015-2017
        "Z270", "H270", "B250", "Z170", "H170", "B150", "H110",
        # 5th/4th Gen (Broadwell/Haswell) - 2013-2015
        "Z97", "H97", "Z87", "H87", "B85", "H81",
        # 3rd Gen (Ivy Bridge) - 2012-2013
        "Z77", "Z75", "H77", "B75",
        # 2nd Gen (Sandy Bridge) - 2011-2012
        "Z68", "P67", "H67", "H61",
    ]
    
    # AMD chipsets (from newest to oldest)
    amd_chipsets = [
        # AM5 (Ryzen 7000+) - 2022+
        "X870E", "X870", "B850", "X670E", "X670", "B650E", "B650", "A620",
        # AM4 (Ryzen 1000-5000) - 2017-2022
        "X570", "B550", "A520", "X470", "B450", "X370", "B350", "A320",
        # AM3+ (FX series) - 2011-2013
        "990FX", "990X", "970", "880G",
    ]
    
    # Check for chipsets (order matters - check longer patterns first)
    for chipset in intel_chipsets + amd_chipsets:
        if chipset in mobo_upper:
            return chipset
    
    return None


def _get_motherboard_specs(mobo_model):
    """
    Get verified RAM specifications for a specific motherboard model.
    
    Returns dict with:
    - max_ram: Total max RAM (e.g., "64GB")
    - max_per_dimm: Max per slot (e.g., "16GB")
    - dimm_slots: Number of slots (e.g., 4)
    - supported_speeds: Supported speeds (e.g., "DDR4-2666 (JEDEC), DDR4-4000+ (OC)")
    - memory_type: DDR generation (e.g., "DDR4")
    - form_factor: Board size (e.g., "ATX", "Micro-ATX")
    - chipset: Chipset (e.g., "Z370")
    
    Database contains VERIFIED specs only - no hallucinated data.
    Grow organically by adding boards as you encounter them.
    """
    if not mobo_model:
        return None
    
    mobo_upper = mobo_model.upper()
    
    # MOTHERBOARD DATABASE - VERIFIED SPECS ONLY
    # Format: model_identifier: (max_ram, max_per_dimm, slots, speeds, mem_type, form_factor, chipset)
    # Source: Manufacturer specification sheets
    # 
    # HOW TO ADD NEW BOARDS:
    # 1. Look up official specs from manufacturer website
    # 2. Verify max RAM, per-DIMM capacity, and slot count
    # 3. Add entry with exact model string from WMI
    # 4. Update this comment with total count
    #
    # Current count: STARTER SET (add as encountered)
    
    MOTHERBOARD_DATABASE = {
        # ===== HOW TO ADD BOARDS =====
        # When you encounter a new board:
        # 1. Look up official specs from manufacturer website
        # 2. Add entry using the SHORTEST unique model identifier from WMI
        # 3. Update count below
        #
        # Current count: 1 board (expand as encountered)
        # Last updated: 2025-01-13
        
        # ===== GIGABYTE BOARDS =====
        # Verified from official Gigabyte specs
        "Z370M D3H": {
            "max_ram": "64GB",
            "max_per_dimm": "16GB",
            "dimm_slots": 4,
            "supported_speeds": "DDR4-2666 (base) | up to DDR4-4000+ (XMP/OC)",
            "memory_type": "DDR4",
            "form_factor": "Micro-ATX",
            "chipset": "Z370",
            "verified": "2025-01-13",
            "source": "Verified from Gigabyte specs"
        },
        
        # ===== ASUS BOARDS =====
        # Add ASUS boards here as encountered
        
        # ===== MSI BOARDS =====
        # Add MSI boards here as encountered
        
        # ===== ASROCK BOARDS =====
        # Add ASRock boards here as encountered
        
        # ===== OEM BOARDS =====
        # Dell, HP, Lenovo, etc. - Add as encountered
    }
    
    # Try to match motherboard model
    for model_key, specs in MOTHERBOARD_DATABASE.items():
        if model_key.upper() in mobo_upper:
            return specs
    
    # Unknown board - return None so we can show appropriate fallback
    return None


def _get_chipset_specs(chipset):
    """
    DEPRECATED - Chipsets don't define max RAM capacity, motherboards do.
    
    This function remains for backwards compatibility but should not be used
    for max RAM calculations. Use _get_motherboard_specs() instead.
    
    Returns dict with:
    - max_capacity: Total max RAM (e.g., "128GB")
    - max_per_dimm: Max per slot (e.g., "32GB")
    - channels: Memory channels (e.g., "Dual-channel")
    - supported_speeds: List of supported speeds
    - memory_type: DDR generation (e.g., "DDR4")
    """
    if not chipset:
        return None
    
    # Comprehensive chipset database
    # Format: (max_capacity, max_per_dimm, channels, speeds, memory_type)
    CHIPSET_DATABASE = {
        # ===== INTEL CHIPSETS =====
        
        # 14th Gen (Raptor Lake Refresh) - 2023-2024
        "Z790": ("192GB", "48GB", "Dual-channel", "DDR5-5600 / DDR4-3200 + OC", "DDR5/DDR4"),
        "H770": ("192GB", "48GB", "Dual-channel", "DDR5-5600 / DDR4-3200 + OC", "DDR5/DDR4"),
        "B760": ("192GB", "48GB", "Dual-channel", "DDR5-5600 / DDR4-3200 + OC", "DDR5/DDR4"),
        
        # 13th/12th Gen (Alder/Raptor Lake) - 2021-2023
        "Z690": ("128GB", "32GB", "Dual-channel", "DDR5-4800 / DDR4-3200 + OC", "DDR5/DDR4"),
        "H670": ("128GB", "32GB", "Dual-channel", "DDR5-4800 / DDR4-3200 + OC", "DDR5/DDR4"),
        "B660": ("128GB", "32GB", "Dual-channel", "DDR5-4800 / DDR4-3200 + OC", "DDR5/DDR4"),
        "H610": ("64GB", "16GB", "Dual-channel", "DDR5-4800 / DDR4-3200", "DDR5/DDR4"),
        
        # 11th Gen (Rocket Lake) - 2021
        "Z590": ("128GB", "32GB", "Dual-channel", "DDR4-3200 + OC up to 5333+", "DDR4"),
        "H570": ("128GB", "32GB", "Dual-channel", "DDR4-3200 + OC", "DDR4"),
        "B560": ("128GB", "32GB", "Dual-channel", "DDR4-3200 + OC", "DDR4"),
        
        # 10th Gen (Comet Lake) - 2020
        "Z490": ("128GB", "32GB", "Dual-channel", "DDR4-2933 + OC up to 4800+", "DDR4"),
        "H470": ("128GB", "32GB", "Dual-channel", "DDR4-2933", "DDR4"),
        "B460": ("128GB", "32GB", "Dual-channel", "DDR4-2933", "DDR4"),
        "H410": ("64GB", "16GB", "Dual-channel", "DDR4-2666", "DDR4"),
        
        # 9th/8th Gen (Coffee Lake) - 2017-2019
        "Z390": ("128GB", "32GB", "Dual-channel", "DDR4-2666 + OC up to 4400+", "DDR4"),
        "Z370": ("64GB", "32GB", "Dual-channel", "DDR4-2666 + OC up to 4000+", "DDR4"),
        "H370": ("64GB", "16GB", "Dual-channel", "DDR4-2666", "DDR4"),
        "B365": ("64GB", "16GB", "Dual-channel", "DDR4-2666", "DDR4"),
        "B360": ("64GB", "16GB", "Dual-channel", "DDR4-2666", "DDR4"),
        "H310": ("32GB", "16GB", "Dual-channel", "DDR4-2666", "DDR4"),
        
        # 7th/6th Gen (Kaby/Skylake) - 2015-2017
        "Z270": ("64GB", "16GB", "Dual-channel", "DDR4-2400 + OC up to 3866+", "DDR4"),
        "H270": ("64GB", "16GB", "Dual-channel", "DDR4-2400", "DDR4"),
        "B250": ("64GB", "16GB", "Dual-channel", "DDR4-2400", "DDR4"),
        "Z170": ("64GB", "16GB", "Dual-channel", "DDR4-2133 + OC up to 3866+", "DDR4"),
        "H170": ("64GB", "16GB", "Dual-channel", "DDR4-2133", "DDR4"),
        "B150": ("64GB", "16GB", "Dual-channel", "DDR4-2133", "DDR4"),
        "H110": ("32GB", "16GB", "Dual-channel", "DDR4-2133", "DDR4"),
        
        # 5th/4th Gen (Broadwell/Haswell) - 2013-2015
        "Z97": ("32GB", "8GB", "Dual-channel", "DDR3-1600 + OC up to 3200+", "DDR3"),
        "H97": ("32GB", "8GB", "Dual-channel", "DDR3-1600", "DDR3"),
        "Z87": ("32GB", "8GB", "Dual-channel", "DDR3-1600 + OC up to 3000+", "DDR3"),
        "H87": ("32GB", "8GB", "Dual-channel", "DDR3-1600", "DDR3"),
        "B85": ("32GB", "8GB", "Dual-channel", "DDR3-1600", "DDR3"),
        "H81": ("16GB", "8GB", "Dual-channel", "DDR3-1600", "DDR3"),
        
        # 3rd Gen (Ivy Bridge) - 2012-2013
        "Z77": ("32GB", "8GB", "Dual-channel", "DDR3-1600 + OC", "DDR3"),
        "Z75": ("32GB", "8GB", "Dual-channel", "DDR3-1600", "DDR3"),
        "H77": ("32GB", "8GB", "Dual-channel", "DDR3-1600", "DDR3"),
        "B75": ("16GB", "8GB", "Dual-channel", "DDR3-1600", "DDR3"),
        
        # 2nd Gen (Sandy Bridge) - 2011-2012
        "Z68": ("32GB", "8GB", "Dual-channel", "DDR3-1333 + OC", "DDR3"),
        "P67": ("32GB", "8GB", "Dual-channel", "DDR3-1333 + OC", "DDR3"),
        "H67": ("32GB", "8GB", "Dual-channel", "DDR3-1333", "DDR3"),
        "H61": ("16GB", "8GB", "Dual-channel", "DDR3-1333", "DDR3"),
        
        # ===== AMD CHIPSETS =====
        
        # AM5 (Ryzen 7000+) - 2022+
        "X870E": ("256GB", "64GB", "Dual-channel", "DDR5-5600 + EXPO up to 8000+", "DDR5"),
        "X870": ("256GB", "64GB", "Dual-channel", "DDR5-5600 + EXPO up to 8000+", "DDR5"),
        "B850": ("256GB", "64GB", "Dual-channel", "DDR5-5600 + EXPO", "DDR5"),
        "X670E": ("256GB", "64GB", "Dual-channel", "DDR5-5200 + EXPO up to 6400+", "DDR5"),
        "X670": ("256GB", "64GB", "Dual-channel", "DDR5-5200 + EXPO up to 6400+", "DDR5"),
        "B650E": ("256GB", "64GB", "Dual-channel", "DDR5-5200 + EXPO", "DDR5"),
        "B650": ("256GB", "64GB", "Dual-channel", "DDR5-5200 + EXPO", "DDR5"),
        "A620": ("128GB", "32GB", "Dual-channel", "DDR5-5200", "DDR5"),
        
        # AM4 (Ryzen 1000-5000) - 2017-2022
        "X570": ("128GB", "32GB", "Dual-channel", "DDR4-3200 + OC up to 5100+", "DDR4"),
        "B550": ("128GB", "32GB", "Dual-channel", "DDR4-3200 + OC up to 5100+", "DDR4"),
        "A520": ("64GB", "32GB", "Dual-channel", "DDR4-3200", "DDR4"),
        "X470": ("128GB", "32GB", "Dual-channel", "DDR4-2933 + OC up to 3600+", "DDR4"),
        "B450": ("128GB", "32GB", "Dual-channel", "DDR4-2933 + OC up to 3600+", "DDR4"),
        "X370": ("128GB", "32GB", "Dual-channel", "DDR4-2666 + OC up to 3200+", "DDR4"),
        "B350": ("64GB", "32GB", "Dual-channel", "DDR4-2666 + OC up to 3200+", "DDR4"),
        "A320": ("64GB", "32GB", "Dual-channel", "DDR4-2666", "DDR4"),
        
        # AM3+ (FX series) - 2011-2013
        "990FX": ("64GB", "8GB", "Dual-channel", "DDR3-1866 + OC", "DDR3"),
        "990X": ("32GB", "8GB", "Dual-channel", "DDR3-1866", "DDR3"),
        "970": ("32GB", "8GB", "Dual-channel", "DDR3-1866 + OC", "DDR3"),
        "880G": ("16GB", "4GB", "Dual-channel", "DDR3-1333", "DDR3"),
    }
    
    if chipset in CHIPSET_DATABASE:
        max_cap, max_dimm, channels, speeds, mem_type = CHIPSET_DATABASE[chipset]
        return {
            'max_capacity': max_cap,
            'max_per_dimm': max_dimm,
            'channels': channels,
            'supported_speeds': speeds,
            'memory_type': mem_type
        }
    
    return None


def _get_motherboard_info(com_wmi):
    """Get enhanced motherboard information with serial and version using COM/WMI"""
    mobo_info = []
    
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_BaseBoard")
                if items and items.Count > 0:
                    motherboard = items.ItemIndex(0)
                    product = motherboard.Properties_("Product").Value or ""
                    manufacturer = motherboard.Properties_("Manufacturer").Value or ""
                    version = motherboard.Properties_("Version").Value or ""
                    serial = motherboard.Properties_("SerialNumber").Value or ""
                    
                    # Build motherboard string with all available info
                    if product and manufacturer:
                        mobo_name = f"{manufacturer} {product}".strip()
                        # Validate it's not a placeholder
                        if mobo_name and mobo_name not in ["", " ", "Unknown", "To be filled by O.E.M."]:
                            mobo_info.append(mobo_name)
                    elif product and product.strip() and product.strip() not in ["Unknown", "To be filled by O.E.M."]:
                        mobo_info.append(product.strip())
                    elif manufacturer and manufacturer.strip() and manufacturer.strip() not in ["Unknown", "To be filled by O.E.M."]:
                        mobo_info.append(manufacturer.strip())
                    
                    # Add version if available
                    if version and str(version).strip() and str(version).strip().upper() not in ["", "NONE", "N/A", "DEFAULT STRING", "TO BE FILLED BY O.E.M."]:
                        mobo_info.append(f"Version: {version.strip()}")
                    
                    # Add serial if available
                    if serial and str(serial).strip():
                        serial_str = str(serial).strip().upper()
                        if serial_str not in ["", "NONE", "N/A", "DEFAULT STRING", "TO BE FILLED BY O.E.M.", "SYSTEM SERIAL NUMBER", "NOT AVAILABLE"]:
                            mobo_info.append(f"Serial: {str(serial).strip()}")
                    
                    if mobo_info:
                        return ", ".join(mobo_info)
                        
            except Exception as e:
                logging.debug(f"Failed to get motherboard info via COM/WMI: {e}")
        
        return "Unknown"
    except Exception as e:
        logging.warning(f"Failed to get motherboard info: {e}")
    return "Unknown"


# PowerShell motherboard function removed - using COM/WMI only


def _get_serial_number(com_wmi):
    """Get system serial number using multiple methods with explicit source identification (COM/WMI)"""
    serial_sources = []  # List of (source, serial_number) tuples
    placeholders = ["TO BE FILLED BY O.E.M.", "DEFAULT STRING", "SYSTEM SERIAL NUMBER", "N/A", "NONE"]
    
    try:
        if com_wmi:
            # Method 1: Win32_BIOS (System/BIOS Serial)
            try:
                items = _query_com_wmi(com_wmi, "Win32_BIOS")
                if items and items.Count > 0:
                    bios = items.ItemIndex(0)
                    serial = bios.Properties_("SerialNumber").Value
                    if serial and serial.strip() and serial.strip().upper() not in placeholders:
                        serial_sources.append(("BIOS", serial.strip()))
            except Exception as e:
                logging.debug(f"Method 1 (Win32_BIOS) failed: {e}")
            
            # Method 2: Win32_SystemEnclosure (Chassis/System Enclosure Serial)
            try:
                items = _query_com_wmi(com_wmi, "Win32_SystemEnclosure")
                if items and items.Count > 0:
                    enclosure = items.ItemIndex(0)
                    serial = enclosure.Properties_("SerialNumber").Value
                    if serial and serial.strip() and serial.strip().upper() not in placeholders:
                        serial_sources.append(("System Enclosure", serial.strip()))
            except Exception as e:
                logging.debug(f"Method 2 (Win32_SystemEnclosure) failed: {e}")
            
            # Method 3: Win32_BaseBoard (Motherboard Serial)
            try:
                items = _query_com_wmi(com_wmi, "Win32_BaseBoard")
                if items and items.Count > 0:
                    baseboard = items.ItemIndex(0)
                    serial = baseboard.Properties_("SerialNumber").Value
                    if serial and serial.strip() and serial.strip().upper() not in placeholders:
                        serial_sources.append(("Motherboard", serial.strip()))
            except Exception as e:
                logging.debug(f"Method 3 (Win32_BaseBoard) failed: {e}")
        
        # Return formatted serial with source
        if serial_sources:
            # Remove duplicates, prefer BIOS > System Enclosure > Motherboard
            priority_order = ["BIOS", "System Enclosure", "Motherboard"]
            seen_serials = set()
            
            # First pass: get highest priority unique serial
            for priority in priority_order:
                for source, serial in serial_sources:
                    if source == priority and serial not in seen_serials:
                        seen_serials.add(serial)
                        return f"{serial} ({source})"
            
            # Fallback: return first unique serial with source
            for source, serial in serial_sources:
                if serial not in seen_serials:
                    seen_serials.add(serial)
                    return f"{serial} ({source})"
        
        return "Not Available"
    except Exception as e:
        logging.warning(f"Failed to get serial number: {e}")
        return "Not Available"


# PowerShell serial number function removed - using COM/WMI only


def _get_battery_status(com_wmi):
    """Get enhanced battery status with health information using COM/WMI"""
    try:
        battery_info = []
        
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_Battery")
                if items and items.Count > 0:
                    for i in range(items.Count):
                        battery = items.ItemIndex(i)
                        status = "Installed"
                        
                        charge_remaining = battery.Properties_("EstimatedChargeRemaining").Value
                        if charge_remaining is not None:
                            status += f" ({charge_remaining}%)"
                        
                        battery_status = battery.Properties_("BatteryStatus").Value
                        if battery_status:
                            # BatteryStatus: 1=Other, 2=Unknown, 3=Fully Charged, 4=Low, 5=Critical, 6=Charging, 7=Not Charging
                            status_map = {
                                3: "Fully Charged",
                                4: "Low",
                                5: "Critical",
                                6: "Charging",
                                7: "Not Charging"
                            }
                            status_text = status_map.get(battery_status, "")
                            if status_text:
                                status += f" - {status_text}"
                        battery_info.append(status)
            except Exception as e:
                logging.debug(f"Failed to get battery via COM/WMI: {e}")
        
        # Fallback to psutil
        if not battery_info:
            if psutil:
                try:
                    battery = psutil.sensors_battery()
                    if battery:
                        battery_info.append(f"Installed ({battery.percent}%)")
                    else:
                        battery_info.append("Not Installed")
                except Exception:
                    battery_info.append("Not Installed")
            else:
                battery_info.append("Not Installed")
        
        return ", ".join(battery_info) if battery_info else "Not Installed"
    except Exception as e:
        logging.warning(f"Failed to get battery status: {e}")
        return "Not Installed"


def _get_storage_info(com_wmi):
    """Get storage information using psutil and COM/WMI - FIXED: No more duplication"""
    storage_details = []
    
    try:
        # Get logical drives (partitions) with usage info - PRIMARY SOURCE
        partitions = psutil.disk_partitions()
        drive_info_map = {}
        
        for partition in partitions:
            try:
                # Skip CD-ROM and network drives
                if 'cdrom' in partition.opts or 'fuse' in partition.opts:
                    continue
                
                usage = psutil.disk_usage(partition.mountpoint)
                total_gb = round(usage.total / (1024**3), 2)
                free_gb = round(usage.free / (1024**3), 2)
                used_gb = round(usage.used / (1024**3), 2)
                used_percent = round((usage.used / usage.total) * 100, 1)
                
                drive_letter = partition.mountpoint[0] if partition.mountpoint else "?"
                file_system = partition.fstype or "Unknown"
                
                # Format size
                if total_gb >= 1000:
                    total_str = f"{total_gb / 1000:.1f}TB"
                else:
                    total_str = f"{total_gb}GB"
                
                drive_info_map[drive_letter] = {
                    'letter': drive_letter,
                    'total': total_str,
                    'total_gb': total_gb,
                    'free_gb': free_gb,
                    'used_gb': used_gb,
                    'used_percent': used_percent,
                    'filesystem': file_system
                }
            except (PermissionError, OSError):
                # Skip drives we can't access
                continue
            except Exception as e:
                logging.debug(f"Error getting partition info for {partition.mountpoint}: {e}")
                continue
        
        # Get physical drive info from COM/WMI for additional context (model, type)
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_DiskDrive")
                if items and items.Count > 0:
                    for i in range(items.Count):
                        disk = items.ItemIndex(i)
                        size = disk.Properties_("Size").Value
                        if size and int(size) > 0:
                            size_gb = round(int(size) / (1024**3), 2)
                            disk_index = disk.Properties_("Index").Value
                            model = disk.Properties_("Model").Value or "Unknown"
                            media_type = disk.Properties_("MediaType").Value or "Unknown"
                            interface = disk.Properties_("InterfaceType").Value or "Unknown"
                            bus_type = _get_disk_bus_type(disk_index)
                            friendly_type = _classify_basic_drive_type(model, media_type, interface, bus_type)
                            
                            # Match physical drive to logical drives by size (approximate)
                            for drive_letter, drive_info in drive_info_map.items():
                                if abs(drive_info['total_gb'] - size_gb) < 10:  # Within 10GB tolerance
                                    if 'model' not in drive_info:
                                        drive_info['model'] = model
                                        drive_info['media_type'] = media_type
                                        drive_info['interface'] = interface
                                        drive_info['bus_type'] = bus_type
                                        drive_info['friendly_type'] = friendly_type
                                    break
            except Exception as e:
                logging.debug(f"Could not get physical drive info: {e}")
        
        # Format output - one line per logical drive
        for drive_letter in sorted(drive_info_map.keys()):
            info = drive_info_map[drive_letter]
            line = f"Drive {info['letter']}: {info['total']} total, {info['free_gb']:.1f} GB free ({info['used_percent']:.1f}% used)"
            
            # Add physical drive info if available
            if 'model' in info and info['model'] != "Unknown":
                line += f" - {info['model']}"
            if 'friendly_type' in info and info['friendly_type']:
                line += f" ({info['friendly_type']})"
            elif 'media_type' in info and info['media_type'] not in ["Unknown", ""]:
                line += f" ({info['media_type']})"
            
            storage_details.append(line)
        
        if storage_details:
            return "\n".join(storage_details)
        return "Storage information unavailable"
    except Exception as e:
        logging.warning(f"Failed to get storage info: {e}")
        return "Storage information unavailable"


def _get_disk_bus_type(disk_index):
    """Best-effort Windows bus type lookup for a physical disk index."""
    if platform.system() != "Windows" or disk_index is None:
        return None

    try:
        ps_script = f'''
        $bus = (Get-Disk -Number {disk_index} -ErrorAction SilentlyContinue).BusType
        if ($bus) {{ Write-Output $bus }}
        '''
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logging.debug(f"Failed to get bus type for disk {disk_index}: {e}")
    return None


def _classify_basic_drive_type(model, media_type, interface, bus_type):
    """Classify drive type for overview text, independent of SMART availability."""
    model_upper = (model or "").upper()
    media_upper = (media_type or "").upper()
    interface_upper = (interface or "").upper()
    bus_upper = (bus_type or "").upper()

    if bus_upper == "NVME":
        return "NVMe SSD"
    if bus_upper == "USB":
        return "USB"
    if bus_upper == "SATA" and ("SSD" in model_upper or "SSD" in media_upper or "SOLID STATE" in media_upper):
        return "SATA SSD"
    if bus_upper == "SATA" and ("HDD" in media_upper or "HARD DISK" in media_upper):
        return "HDD"

    if "NVME" in model_upper or "NVM" in model_upper or "NVME" in interface_upper:
        return "NVMe SSD"
    if "SSD" in model_upper or "SSD" in media_upper or "SOLID STATE" in media_upper:
        return "SATA SSD"
    if "HDD" in media_upper or "HARD DISK" in media_upper:
        return "HDD"
    return None


def _get_network_info(com_wmi):
    """Get network adapter information using COM/WMI"""
    adapters = []

    # Virtual adapter keywords to filter out
    VIRTUAL_KEYWORDS = [
        'vmware', 'virtualbox', 'vbox', 'hyper-v',
        'wan miniport', 'bluetooth device (personal area network)',
        'bluetooth pan', 'tap-windows', 'virtual', 'loopback'
    ]

    def is_physical_adapter(adapter_name):
        """Check if adapter is physical (not virtual)"""
        if not adapter_name:
            return False
        name_lower = adapter_name.lower()
        return not any(keyword in name_lower for keyword in VIRTUAL_KEYWORDS)

    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_NetworkAdapter")
                if items:
                    for i in range(items.Count):
                        nic = items.ItemIndex(i)
                        # Only physical adapters
                        physical = nic.Properties_("PhysicalAdapter").Value
                        if not physical:
                            continue

                        name = nic.Properties_("Name").Value
                        mac = nic.Properties_("MACAddress").Value

                        # Filter out virtual adapters by name
                        if not is_physical_adapter(name):
                            logging.debug(f"Filtered virtual adapter: {name}")
                            continue

                        if name and mac:
                            adapter_info = f"{name}"
                            if mac and mac != "":
                                adapter_info += f" - MAC: {mac}"

                            # Get speed
                            try:
                                speed = nic.Properties_("Speed").Value
                                if speed and 0 < speed < 500000000000:  # 500 Gbps realistic max
                                    speed_mbps = speed / 1000000
                                    if speed_mbps >= 1000:
                                        adapter_info += f" ({speed_mbps/1000:.0f} Gbps)"
                                    else:
                                        adapter_info += f" ({speed_mbps:.0f} Mbps)"
                            except (ValueError, TypeError, AttributeError):
                                pass

                            # Get driver version (v1.2 - Critical for Wi-Fi troubleshooting)
                            try:
                                pnp_device_id = nic.Properties_("PNPDeviceID").Value
                                if pnp_device_id:
                                    # Query Win32_PnPSignedDriver for driver info
                                    # Escape backslashes in DeviceID for WMI query
                                    escaped_id = pnp_device_id.replace("\\", "\\\\")
                                    driver_query = f"SELECT DriverVersion, DriverDate FROM Win32_PnPSignedDriver WHERE DeviceID = '{escaped_id}'"
                                    driver_items = com_wmi.ExecQuery(driver_query)

                                    if driver_items and driver_items.Count > 0:
                                        driver = driver_items.ItemIndex(0)
                                        driver_version = driver.Properties_("DriverVersion").Value
                                        driver_date = driver.Properties_("DriverDate").Value

                                        if driver_version:
                                            adapter_info += f" | Driver: {driver_version}"

                                        if driver_date:
                                            # Parse WMI date format (YYYYMMDDhhmmss.mmmmmm+UUU)
                                            try:
                                                date_str = str(driver_date)[:8]  # YY YYMMDD
                                                from datetime import datetime
                                                date_obj = datetime.strptime(date_str, "%Y%m%d")
                                                driver_date_formatted = date_obj.strftime("%m/%d/%Y")
                                                adapter_info += f" ({driver_date_formatted})"
                                            except:
                                                pass
                            except Exception as e:
                                logging.debug(f"Failed to get driver info for {name}: {e}")

                            adapters.append(adapter_info)
            except Exception as e:
                logging.debug(f"Failed to get network info via COM/WMI: {e}")
        
        if adapters:
            return "\n".join(adapters)
        return "No network adapters found"
    except Exception as e:
        logging.warning(f"Failed to get network info: {e}")
        return "Network information unavailable"


def _has_connected_ethernet(com_wmi):
    """Return True when a physical wired adapter is actively connected."""
    ETHERNET_KEYWORDS = (
        'ethernet', 'gigabit', 'gbe', '2.5gbe', '5gbe', '10gbe',
        'realtek pcie', 'intel(r) ethernet', 'local area connection'
    )
    WIFI_KEYWORDS = ('wireless', 'wi-fi', 'wifi', 'wlan', '802.11')
    VIRTUAL_KEYWORDS = (
        'vmware', 'virtualbox', 'vbox', 'hyper-v', 'wan miniport',
        'bluetooth device (personal area network)', 'bluetooth pan',
        'tap-windows', 'virtual', 'loopback'
    )

    def _looks_like_physical_ethernet(name):
        if not name:
            return False
        lowered = name.lower()
        if any(word in lowered for word in VIRTUAL_KEYWORDS):
            return False
        if any(word in lowered for word in WIFI_KEYWORDS):
            return False
        return any(word in lowered for word in ETHERNET_KEYWORDS)

    try:
        if not com_wmi:
            return False

        items = _query_com_wmi(com_wmi, "Win32_NetworkAdapter")
        if not items:
            return False

        for i in range(items.Count):
            nic = items.ItemIndex(i)
            try:
                physical = nic.Properties_("PhysicalAdapter").Value
                if not physical:
                    continue

                name = nic.Properties_("Name").Value or ""
                if not _looks_like_physical_ethernet(name):
                    continue

                net_status = nic.Properties_("NetConnectionStatus").Value
                if str(net_status) == '2':
                    return True
            except Exception:
                continue
    except Exception as e:
        logging.debug(f"Failed to determine Ethernet connectivity: {e}")

    return False


def _parse_edid_data(edid_bytes):
    """Parse EDID data to extract manufacturer, model, etc."""
    try:
        if len(edid_bytes) < 128:
            return None

        # EDID header check
        if edid_bytes[0] != 0x00 or edid_bytes[1] != 0xFF or edid_bytes[2] != 0xFF or edid_bytes[3] != 0xFF:
            return None

        # Manufacturer ID (bytes 8-9)
        manufacturer_id = (edid_bytes[9] << 8) | edid_bytes[8]
        manufacturer = _decode_manufacturer_id(manufacturer_id)

        # Product Code (bytes 10-11)
        product_code = (edid_bytes[11] << 8) | edid_bytes[10]

        # Get model name from descriptor blocks (bytes 54-125)
        model_name = None
        for i in range(4):  # 4 descriptor blocks
            offset = 54 + (i * 18)
            if offset + 18 <= len(edid_bytes):
                descriptor = edid_bytes[offset:offset+18]
                if descriptor[0] == 0x00 and descriptor[1] == 0x00 and descriptor[3] == 0xFC:
                    # Monitor name descriptor
                    model_name = bytes(descriptor[5:18]).decode('ascii', errors='ignore').rstrip('\x00 ')
                    break

        return {
            'manufacturer': manufacturer,
            'product_code': f"{product_code:04X}",
            'model': model_name or f"Product {product_code:04X}",
        }

    except Exception as e:
        logging.debug(f"EDID parsing failed: {e}")
        return None

def _decode_manufacturer_id(manufacturer_id):
    """Decode 3-letter manufacturer ID from EDID"""
    try:
        # Manufacturer ID is stored as 5 bits per character, offset by 64
        # The bits are arranged as: [char2:5][char1:5][char0:5][reserved:1]
        chars = []
        for i in range(3):
            # Extract 5 bits for each character, starting from LSB
            char_bits = (manufacturer_id >> (5 * i)) & 0x1F
            char_code = char_bits + 64
            if 65 <= char_code <= 90:  # A-Z
                chars.append(chr(char_code))
            else:
                chars.append('?')
        return ''.join(chars)
    except:
        return f"ID_{manufacturer_id:04X}"

def _get_display_info(com_wmi):
    """Get display/monitor information with laptop internal display priority

    For laptops: Prioritizes internal panel (LVDS/eDP) over external monitors
    For desktops: Shows external monitors only
    """
    displays = []
    internal_displays = []
    external_displays = []

    def _has_builtin_display_for_logic(com_wmi):
        """Check if system has a built-in display (for display logic decisions)"""
        try:
            # Check for video controllers with internal display indicators
            video_items = _query_com_wmi(com_wmi, "Win32_VideoController")
            if video_items:
                for i in range(video_items.Count):
                    controller = video_items.ItemIndex(i)
                    video_name = controller.Properties_("Name").Value

                    # Internal display manufacturers (panel makers for AIOs and laptops)
                    internal_indicators = ['innolux', 'lg display', 'au optronics', 'boe',
                                         'samsung display', 'sharp display', 'chimei']
                    if video_name and any(indicator in video_name.lower() for indicator in internal_indicators):
                        return True

            # Also check for integrated graphics controllers (common in AIOs)
            integrated_indicators = ['intel', 'amd', 'integrated', 'embedded']
            if video_items:
                for i in range(video_items.Count):
                    controller = video_items.ItemIndex(i)
                    video_name = controller.Properties_("Name").Value
                    if video_name and any(indicator in video_name.lower() for indicator in integrated_indicators):
                        # Additional check: if we have battery, it's likely a laptop, not AIO
                        battery_items = _query_com_wmi(com_wmi, "Win32_Battery")
                        has_battery = battery_items and battery_items.Count > 0
                        if not has_battery:
                            return True
            return False
        except Exception as e:
            logging.debug(f"Failed to check for built-in display: {e}")
            return False

    # Determine system type for display logic
    system_type = "Unknown"
    has_builtin_display = False

    try:
        if com_wmi:
            # Get system type classification
            system_type_result = _get_system_type(com_wmi)
            if system_type_result:
                system_type = system_type_result
                logging.debug(f"Display detection: System type is {system_type}")

            # Check for built-in display capability
            has_builtin_display = _has_builtin_display_for_logic(com_wmi)
            if has_builtin_display:
                logging.debug("Display detection: System has built-in display capability")
    except Exception as e:
        logging.debug(f"Failed to determine system type for display logic: {e}")

    try:
        if com_wmi:
            # For systems with built-in displays: Check for internal displays via VideoController
            if system_type in ["Laptop", "All-in-One"] or (system_type == "Unknown" and has_builtin_display):
                try:
                    # Query video controllers to get internal display info
                    video_items = _query_com_wmi(com_wmi, "Win32_VideoController")
                    if video_items:
                        for i in range(video_items.Count):
                            controller = video_items.ItemIndex(i)
                            video_name = controller.Properties_("Name").Value

                            # Get current resolution from video controller
                            current_h_res = controller.Properties_("CurrentHorizontalResolution").Value
                            current_v_res = controller.Properties_("CurrentVerticalResolution").Value

                            if video_name and current_h_res and current_v_res:
                                display_info = f"{video_name} - {current_h_res}x{current_v_res}"
                                # Check if this looks like an internal display
                                # Internal displays often have manufacturer names like "Chimei Innolux", "LG Display", "AU Optronics", "BOE"
                                internal_indicators = ['innolux', 'lg display', 'au optronics', 'boe', 'samsung display', 'sharp display']
                                if any(indicator in video_name.lower() for indicator in internal_indicators):
                                    internal_displays.append(display_info)
                                    logging.debug(f"Found internal display: {display_info}")
                except Exception as e:
                    logging.debug(f"Failed to get video controller info for laptop display: {e}")

            # Get external/desktop monitors using Win32_DesktopMonitor
            try:
                items = _query_com_wmi(com_wmi, "Win32_DesktopMonitor")
                if items:
                    for i in range(items.Count):
                        monitor = items.ItemIndex(i)
                        name = monitor.Properties_("Name").Value

                        # For desktops, don't skip "Generic PnP Monitor" - get more detailed info
                        if name:
                            display_info = name
                            width = monitor.Properties_("ScreenWidth").Value
                            height = monitor.Properties_("ScreenHeight").Value

                            # Try to get manufacturer and model info
                            try:
                                manufacturer = monitor.Properties_("MonitorManufacturer").Value
                                if manufacturer and manufacturer != "(Standard monitor types)":
                                    display_info = f"{manufacturer} {display_info}"
                            except:
                                pass

                            try:
                                model = monitor.Properties_("MonitorType").Value
                                if model and model != display_info:
                                    display_info = f"{display_info} ({model})"
                            except:
                                pass

                            if width and height:
                                display_info += f" - {width}x{height}"

                                # Try to get refresh rate from video controller
                                try:
                                    video_items = _query_com_wmi(com_wmi, "Win32_VideoController")
                                    if video_items and video_items.Count > 0:
                                        controller = video_items.ItemIndex(0)  # Primary controller
                                        refresh = controller.Properties_("CurrentRefreshRate").Value
                                        if refresh:
                                            display_info += f" @ {refresh}Hz"
                                except Exception as e:
                                    logging.debug(f"Failed to get refresh rate: {e}")

                            external_displays.append(display_info)
                            logging.debug(f"Found external display: {display_info}")
                        else:
                            logging.debug(f"Skipping monitor with no name")
            except Exception as e:
                logging.debug(f"Failed to get display info via COM/WMI monitors: {e}")

            # Enhanced info: Try Win32_PnPEntity to get manufacturer and model from device IDs and EDID
            try:
                pnp_items = _query_com_wmi(com_wmi, "Win32_PnPEntity WHERE PNPClass='Monitor'")
                if pnp_items:
                    monitor_details = []
                    for i in range(pnp_items.Count):
                        pnp = pnp_items.ItemIndex(i)
                        device_id = pnp.Properties_("DeviceID").Value
                        name = pnp.Properties_("Name").Value

                        if device_id:
                            # Parse manufacturer from device ID
                            manufacturer = None
                            if "SAM" in device_id.upper():
                                manufacturer = "Samsung"
                            elif "GSM" in device_id.upper() or "LGD" in device_id.upper():
                                manufacturer = "LG"
                            elif "ACR" in device_id.upper():
                                manufacturer = "Acer"
                            elif "AOC" in device_id.upper():
                                manufacturer = "AOC"
                            elif "DEL" in device_id.upper():
                                manufacturer = "Dell"
                            elif "HP" in device_id.upper():
                                manufacturer = "HP"

                            # Try to get EDID data for detailed model info
                            model_info = None
                            try:
                                # Access registry for EDID data
                                import winreg
                                edid_path = f"SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\{device_id}\\Device Parameters"
                                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, edid_path)
                                edid_data, _ = winreg.QueryValueEx(key, "EDID")
                                winreg.CloseKey(key)

                                if edid_data:
                                    parsed_edid = _parse_edid_data(edid_data)
                                    if parsed_edid and parsed_edid.get('model'):
                                        model_info = parsed_edid['model']
                                        # Override manufacturer if EDID has better info
                                        if parsed_edid.get('manufacturer') and parsed_edid['manufacturer'] not in ['???', 'ID_']:
                                            manufacturer = parsed_edid['manufacturer']
                            except Exception as e:
                                logging.debug(f"Failed to get EDID for {device_id}: {e}")

                            if manufacturer or model_info:
                                monitor_details.append({
                                    'manufacturer': manufacturer,
                                    'model': model_info or name,
                                    'device_id': device_id
                                })

                    # If we found detailed monitor info, enhance the display list
                    if monitor_details and external_displays:
                        # Clear existing displays and rebuild with detailed info
                        enhanced_displays = []
                        for detail in monitor_details:
                            model = detail['model'] or "Unknown Monitor"
                            # Clean up model names
                            if "Generic Monitor" in model:
                                model = model.replace("Generic Monitor", "").strip()
                                if model.startswith("(") and model.endswith(")"):
                                    model = model[1:-1]  # Remove parentheses

                            manufacturer = detail['manufacturer'] or ""
                            if manufacturer and not model.startswith(manufacturer):
                                display_name = f"{manufacturer} {model}"
                            else:
                                display_name = model

                            # Add resolution and refresh rate info
                            try:
                                video_items = _query_com_wmi(com_wmi, "Win32_VideoController")
                                if video_items and video_items.Count > 0:
                                    controller = video_items.ItemIndex(0)
                                    h_res = controller.Properties_("CurrentHorizontalResolution").Value
                                    v_res = controller.Properties_("CurrentVerticalResolution").Value
                                    refresh = controller.Properties_("CurrentRefreshRate").Value
                                    if h_res and v_res:
                                        display_name += f" - {h_res}x{v_res}"
                                        if refresh:
                                            display_name += f" @ {refresh}Hz"
                            except Exception as e:
                                logging.debug(f"Failed to add resolution info: {e}")

                            enhanced_displays.append(display_name)

                        if enhanced_displays:
                            external_displays[:] = enhanced_displays
                            logging.debug(f"Enhanced displays with EDID info: {enhanced_displays}")
            except Exception as e:
                logging.debug(f"Failed to enhance monitor info via PNP entities: {e}")

            # Additional fallback: Try Win32_PnPEntity for more detailed monitor info
            if not external_displays:
                try:
                    pnp_items = _query_com_wmi(com_wmi, "Win32_PnPEntity WHERE PNPClass='Monitor'")
                    if pnp_items:
                        for i in range(pnp_items.Count):
                            pnp = pnp_items.ItemIndex(i)
                            name = pnp.Properties_("Name").Value
                            device_id = pnp.Properties_("DeviceID").Value

                            if name and device_id:
                                # Parse manufacturer from device ID
                                manufacturer = "Unknown"
                                if "SAM" in device_id.upper():
                                    manufacturer = "Samsung"
                                elif "GSM" in device_id.upper() or "LGD" in device_id.upper():
                                    manufacturer = "LG"
                                elif "ACR" in device_id.upper():
                                    manufacturer = "Acer"
                                elif "AOC" in device_id.upper():
                                    manufacturer = "AOC"
                                elif "DEL" in device_id.upper():
                                    manufacturer = "Dell"
                                elif "HP" in device_id.upper():
                                    manufacturer = "HP"

                                display_info = f"{manufacturer} {name}"

                                # Try to get resolution from video controller
                                try:
                                    video_items = _query_com_wmi(com_wmi, "Win32_VideoController")
                                    if video_items and video_items.Count > 0:
                                        controller = video_items.ItemIndex(0)
                                        h_res = controller.Properties_("CurrentHorizontalResolution").Value
                                        v_res = controller.Properties_("CurrentVerticalResolution").Value
                                        refresh = controller.Properties_("CurrentRefreshRate").Value
                                        if h_res and v_res:
                                            display_info += f" - {h_res}x{v_res}"
                                            if refresh:
                                                display_info += f" @ {refresh}Hz"
                                except Exception as e:
                                    logging.debug(f"Failed to get resolution/refresh from video controller: {e}")

                                external_displays.append(display_info)
                                logging.debug(f"Found monitor via PNP: {display_info}")
                except Exception as e:
                    logging.debug(f"Failed to get monitor info via PNP entities: {e}")

        # Comprehensive priority logic for all system types
        logging.debug(f"Display priority logic: System={system_type}, Built-in={has_builtin_display}, Internal={len(internal_displays)}, External={len(external_displays)}")

        if system_type == "Laptop":
            # Laptops: Prioritize internal panel, then external monitors
            logging.debug("Applying laptop display logic: internal first, then external")
            if internal_displays:
                displays.extend(internal_displays)
            if external_displays:
                displays.extend(external_displays)

        elif system_type == "All-in-One":
            # All-in-One PCs: Show built-in display first, then external monitors
            logging.debug("Applying AIO display logic: built-in first, then external")
            if internal_displays:
                displays.extend(internal_displays)
            if external_displays:
                displays.extend(external_displays)

        elif system_type in ["Desktop", "Mini PC"]:
            # Desktops and Mini PCs: External monitors only (no built-in display)
            logging.debug("Applying desktop/mini-PC display logic: external monitors only")
            displays.extend(external_displays)

        else:
            # Unknown system type - make intelligent decision based on capabilities
            logging.debug("Applying fallback display logic for unknown system type")
            if has_builtin_display and internal_displays:
                # System has built-in display capability, show internal first
                displays.extend(internal_displays)
                displays.extend(external_displays)
            else:
                # No built-in display detected, show external only
                displays.extend(external_displays)

        # Final fallback: if no displays detected at all, try to get any available monitor info
        if not displays and external_displays:
            logging.debug("No displays in final list, using external displays as fallback")
            displays.extend(external_displays)

        if displays:
            return "\n".join(displays)
        return "Display information unavailable"
    except Exception as e:
        logging.warning(f"Failed to get display info: {e}")
        return "Display information unavailable"


def _get_system_health():
    """Get system health information"""
    health_info = []
    
    try:
        # System uptime
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        uptime_str = str(uptime).split('.')[0]  # Remove microseconds
        health_info.append(f"Uptime: {uptime_str}")
        
        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=1)
        health_info.append(f"CPU Usage: {cpu_percent:.1f}%")
        
        # Memory usage (already in RAM info, but include here for completeness)
        mem = psutil.virtual_memory()
        health_info.append(f"Memory Usage: {mem.percent:.1f}%")
        
        # Disk usage summary
        disk_usage = []
        partitions = psutil.disk_partitions()
        for partition in partitions:
            try:
                if 'cdrom' in partition.opts:
                    continue
                usage = psutil.disk_usage(partition.mountpoint)
                drive_letter = partition.mountpoint[0] if partition.mountpoint else "?"
                disk_usage.append(f"{drive_letter}: {usage.percent:.1f}%")
            except:
                continue
        if disk_usage:
            health_info.append(f"Disk Usage: {', '.join(disk_usage)}")
        
        return ", ".join(health_info)
    except Exception as e:
        logging.warning(f"Failed to get system health: {e}")
        return "System health information unavailable"


def _get_windows_details(com_wmi):
    """Get Windows-specific details using COM/WMI"""
    details = []
    
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_OperatingSystem")
                if items and items.Count > 0:
                    os_data = items.ItemIndex(0)
                    
                    # Windows edition
                    caption = os_data.Properties_("Caption").Value
                    if caption:
                        details.append(f"Edition: {caption}")
                    
                    # Version
                    version = os_data.Properties_("Version").Value
                    if version:
                        details.append(f"Version: {version}")
                    
                    # Build number
                    build = os_data.Properties_("BuildNumber").Value
                    if build:
                        details.append(f"Build: {build}")
                    
                    # Install date
                    install_date = os_data.Properties_("InstallDate").Value
                    if install_date:
                        try:
                            install_str = str(install_date).split('.')[0]
                            year = install_str[:4]
                            month = install_str[4:6]
                            day = install_str[6:8]
                            details.append(f"Installed: {year}-{month}-{day}")
                        except Exception as e:
                            logging.debug(f"Could not parse install date '{install_date}': {e}")
                            pass
                    
                    # Last boot time
                    boot_time = os_data.Properties_("LastBootUpTime").Value
                    if boot_time:
                        try:
                            boot_str = str(boot_time).split('.')[0]
                            year = boot_str[:4]
                            month = boot_str[4:6]
                            day = boot_str[6:8]
                            hour = boot_str[8:10]
                            minute = boot_str[10:12]
                            details.append(f"Last Boot: {year}-{month}-{day} {hour}:{minute}")
                        except Exception as e:
                            logging.debug(f"Could not parse boot time '{boot_time}': {e}")
                            pass
                    
                    if details:
                        return ", ".join(details)
            except Exception as e:
                logging.debug(f"Failed to get Windows details via COM/WMI: {e}")
        
        return "Windows details unavailable"
    except Exception as e:
        logging.warning(f"Failed to get Windows details: {e}")
        return "Windows details unavailable"


def _get_bios_info(com_wmi):
    """Get BIOS/UEFI information using COM/WMI - comprehensive Python-only approach (no msinfo32)"""
    bios_info = []
    
    try:
        if com_wmi:
            try:
                bios_items = com_wmi.ExecQuery("SELECT * FROM Win32_BIOS")
                if bios_items and len(bios_items) > 0:
                    bios = bios_items[0]
                    
                    # Get Manufacturer
                    try:
                        mfr = bios.Properties_("Manufacturer").Value
                        if mfr and str(mfr).strip():
                            bios_info.append(f"Manufacturer: {mfr}")
                    except Exception:
                        pass
                    
                    # Get Version (try SMBIOSBIOSVersion first, fallback to Version)
                    version = None
                    try:
                        version = bios.Properties_("SMBIOSBIOSVersion").Value
                    except Exception:
                        pass
                    if not version:
                        try:
                            version = bios.Properties_("Version").Value
                        except Exception:
                            pass
                    if version and str(version).strip():
                        bios_info.append(f"Version: {version}")
                    
                    # Get Release Date
                    try:
                        release_date = bios.Properties_("ReleaseDate").Value
                        if release_date:
                            date_str = str(release_date).split('.')[0]
                            if len(date_str) >= 8:
                                year = date_str[:4]
                                month = date_str[4:6]
                                day = date_str[6:8]
                                bios_info.append(f"Date: {month}/{day}/{year}")
                    except Exception:
                        pass
                    
                    # Get SMBIOS Version
                    try:
                        major = bios.Properties_("SMBIOSMajorVersion").Value
                        minor = bios.Properties_("SMBIOSMinorVersion").Value
                        if major is not None and minor is not None:
                            bios_info.append(f"SMBIOS: {major}.{minor}")
                    except Exception:
                        pass
                    
                    # Get Serial Number (include in BIOS section instead of separate field)
                    try:
                        serial = bios.Properties_("SerialNumber").Value
                        if serial:
                            serial_str = str(serial).strip().upper()
                            # Skip placeholder/invalid serials
                            if serial_str and serial_str not in ["NOT AVAILABLE", "NONE", "N/A", "DEFAULT STRING", "TO BE FILLED BY O.E.M.", "SYSTEM SERIAL NUMBER"]:
                                bios_info.append(f"Serial: {str(serial).strip()}")
                    except Exception:
                        pass
                            
            except Exception as e:
                logging.debug(f"Failed to get BIOS info via COM/WMI: {e}")
        
        # Return info as tuple (first_line, remaining_lines_list)
        if bios_info:
            return (bios_info[0] if bios_info else "BIOS information unavailable", bios_info[1:] if len(bios_info) > 1 else [])
        return ("BIOS information unavailable", [])
    except Exception as e:
        logging.warning(f"Failed to get BIOS info: {e}")
        return ("BIOS information unavailable", [])

# msinfo32 function REMOVED - causes window popups and is slow
# Using WMI and COM/WMI only - faster and more reliable


def _get_system_type(com_wmi):
    """Detect system type: Laptop, Desktop, All-in-One, or Mini PC using COM/WMI

    Enhanced AIO detection: Checks chassis type + built-in display presence
    """

    def _has_builtin_display(com_wmi):
        """Check if system has a built-in display (for AIO detection)"""
        try:
            # Check for video controllers with internal display indicators
            video_items = _query_com_wmi(com_wmi, "Win32_VideoController")
            if video_items:
                for i in range(video_items.Count):
                    controller = video_items.ItemIndex(i)
                    video_name = controller.Properties_("Name").Value

                    # Internal display manufacturers (panel makers for AIOs and laptops)
                    internal_indicators = ['innolux', 'lg display', 'au optronics', 'boe',
                                         'samsung display', 'sharp display', 'chimei']
                    if video_name and any(indicator in video_name.lower() for indicator in internal_indicators):
                        logging.debug(f"AIO Detection: Found built-in display - {video_name}")
                        return True
            return False
        except Exception as e:
            logging.debug(f"Failed to check for built-in display: {e}")
            return False

    try:
        if com_wmi:
            try:
                # Query Win32_SystemEnclosure for chassis type
                items = _query_com_wmi(com_wmi, "Win32_SystemEnclosure")
                if items and items.Count > 0:
                    enclosure = items.ItemIndex(0)
                    chassis_types_prop = enclosure.Properties_("ChassisTypes").Value

                    # ChassisTypes is an array - get first value
                    chassis_type = None
                    if chassis_types_prop:
                        if hasattr(chassis_types_prop, '__getitem__'):
                            chassis_type = chassis_types_prop[0] if len(chassis_types_prop) > 0 else None
                        else:
                            chassis_type = chassis_types_prop

                    if chassis_type is not None:
                        # Chassis type codes (DMTF SMBIOS Reference Specification)
                        # Desktop types: 3,4,5,6,7,15,16
                        # Laptop/Portable: 8,9,10,11,12,14,18,21,31,32
                        # All-in-One: 13 (definite), 34/35 (if built-in display)
                        # Mini PC: 34, 35 (compact form factor, no built-in display)
                        desktop_types = [3, 4, 5, 6, 7, 15, 16]
                        laptop_types = [8, 9, 10, 11, 12, 14, 18, 21, 31, 32]
                        minipc_or_aio_types = [34, 35]  # Can be either Mini PC or AIO

                        logging.debug(f"System Type Detection: Chassis type = {chassis_type}")

                        # Chassis type 13 = definite All-in-One
                        if chassis_type == 13:
                            logging.debug(f"AIO detected by chassis type 13")
                            return "All-in-One"

                        # Chassis types 34/35: Check for built-in display
                        # WITH display = AIO, WITHOUT display = Mini PC
                        elif chassis_type in minipc_or_aio_types:
                            if _has_builtin_display(com_wmi):
                                logging.debug(f"AIO detected: Chassis {chassis_type} with built-in display")
                                return "All-in-One"
                            else:
                                logging.debug(f"Mini PC detected: Chassis {chassis_type} without built-in display")
                                return "Mini PC"

                        # Desktop chassis + built-in display = AIO (Dell OptiPlex AIO, HP Envy AIO)
                        # These often report as desktop chassis type 3 but have integrated displays
                        elif chassis_type in desktop_types:
                            if _has_builtin_display(com_wmi):
                                logging.debug(f"AIO detected: Desktop chassis {chassis_type} with built-in display")
                                return "All-in-One"
                            else:
                                return "Desktop"

                        elif chassis_type in laptop_types:
                            return "Laptop"
                        else:
                            # Unknown chassis type - try battery detection for laptops
                            battery_items = _query_com_wmi(com_wmi, "Win32_Battery")
                            if battery_items and battery_items.Count > 0:
                                return f"Laptop (chassis type {chassis_type}, detected by battery)"
                            return f"Unknown (chassis type {chassis_type})"

                    # No chassis type - try battery detection
                    battery_items = _query_com_wmi(com_wmi, "Win32_Battery")
                    if battery_items and battery_items.Count > 0:
                        return "Laptop (detected by battery)"
            except Exception as e:
                logging.debug(f"Failed to get system type via COM/WMI: {e}")

        return "Unknown"
    except Exception as e:
        logging.warning(f"Failed to get system type: {e}")
        return "Unknown"


# PowerShell system type function removed - using COM/WMI only


def _get_laptop_model(com_wmi):
    """Get OEM system model (laptops, prebuilt desktops, AIOs) using COM/WMI
    Returns 'Not Available' for custom builds so GUI can display 'Custom Build' instead"""
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_ComputerSystem")
                if items and items.Count > 0:
                    computer_system = items.ItemIndex(0)
                    model = computer_system.Properties_("Model").Value or ""
                    manufacturer = computer_system.Properties_("Manufacturer").Value or ""
                    
                    # Debug logging to trace what we're getting
                    logging.debug(f"System Model Detection - Raw COM/WMI Data:")
                    logging.debug(f"  Manufacturer: '{manufacturer}'")
                    logging.debug(f"  Model: '{model}'")
                    
                    if model and model.strip():
                        model = model.strip()
                        manufacturer_upper = manufacturer.strip().upper() if manufacturer else ""
                        model_upper = model.upper()
                        
                        logging.debug(f"  Manufacturer (upper): '{manufacturer_upper}'")
                        logging.debug(f"  Model (upper): '{model_upper}'")
                        
                        # Filter out placeholder values (indicates custom build)
                        placeholder_models = ["TO BE FILLED BY O.E.M.", "DEFAULT STRING", "SYSTEM PRODUCT NAME", "N/A"]
                        if any(ph in model_upper for ph in placeholder_models):
                            logging.debug(f"  [CUSTOM] Detected placeholder model - Custom Build")
                            return "Not Available"
                        
                        # Check if manufacturer is a motherboard brand (indicates custom build)
                        # Custom builds show motherboard manufacturer as system manufacturer
                        mobo_manufacturers = ["GIGABYTE", "ASUS", "ASUSTEK", "MSI", "ASROCK", "EVGA", "BIOSTAR", "SUPERMICRO"]
                        matched_mobo = None
                        for mobo in mobo_manufacturers:
                            if mobo in manufacturer_upper:
                                matched_mobo = mobo
                                break
                        
                        if matched_mobo:
                            # This is a custom build - return Not Available so GUI shows "Custom Build"
                            logging.debug(f"  [CUSTOM] Detected motherboard manufacturer '{matched_mobo}' - Custom Build")
                            return "Not Available"
                        
                        # Check for ODM laptop chassis (Clevo, Tongfang, etc.)
                        # These are often rebranded as Aftershock, Metabox, Sager, etc.
                        odm_manufacturers = {
                            "NOTEBOOK": "ODM Laptop",  # Generic Clevo default
                            "CLEVO": "Clevo Laptop",
                            "TONGFANG": "Tongfang Laptop",
                            "TONFANG": "Tongfang Laptop",  # Typo variant
                            "XMG": "XMG Laptop"  # European Clevo reseller
                        }
                        
                        for odm_key, odm_label in odm_manufacturers.items():
                            if odm_key in manufacturer_upper:
                                # ODM laptop - note that it might be rebranded
                                logging.debug(f"  [ODM] Detected {odm_label} chassis (may be Aftershock/Metabox/other reseller)")
                                # Return the model with a note about ODM origin
                                if model_upper.startswith("P") or model_upper.startswith("N"):  # Clevo model codes
                                    return f"{odm_label} ({model})"
                                return f"{odm_label}"
                        
                        # HP-specific model parsing (learned from HP Laptop 15s-fq1xxx)
                        # HP models: "HP Laptop 15s-fq1xxx" format
                        # Format: "HP Laptop [series]-[variant]" (e.g., "15s-fq1xxx")
                        # See HP_LAPTOP_KNOWLEDGE_BASE.md for detailed HP patterns
                        if manufacturer_upper == "HP" and model_upper.startswith("HP LAPTOP"):
                            # Format: "HP Laptop 15s-fq1xxx" -> extract "15s-fq1xxx" or just "15s"
                            hp_model_match = re.search(r'HP\s+LAPTOP\s+([A-Z0-9]+-[A-Z0-9]+)', model_upper)
                            if hp_model_match:
                                hp_model_code = hp_model_match.group(1)
                                logging.debug(f"  [HP] Extracted HP model code: '{hp_model_code}'")
                                # Return clean format: "HP Laptop 15s-fq1xxx"
                                return model.strip()
                        
                        # Valid OEM system - combine manufacturer and model if both available
                        logging.debug(f"  [OEM] Valid OEM system detected")
                        if manufacturer and manufacturer_upper not in ["TO BE FILLED BY O.E.M.", "DEFAULT STRING"]:
                            manufacturer_clean = manufacturer.strip()
                            # Remove redundant manufacturer name from model if already present
                            if manufacturer_clean.upper() in model_upper:
                                logging.debug(f"  -> Returning model only: '{model}'")
                                return model
                            combined = f"{manufacturer_clean} {model}"
                            logging.debug(f"  -> Returning combined: '{combined}'")
                            return combined
                        logging.debug(f"  -> Returning model: '{model}'")
                        return model
            except Exception as e:
                logging.debug(f"Failed to get system model via COM/WMI: {e}")
        
        logging.debug(f"  -> No model detected, returning 'Not Available'")
        return "Not Available"
    except Exception as e:
        logging.warning(f"Failed to get system model: {e}")
        return "Not Available"


def _get_hp_specific_info():
    """Get HP-specific information from registry (SystemSKU, Product Number, etc.)
    This is data that HP Support Assistant would show but we can get from registry"""
    if platform.system() != "Windows":
        return None
    
    try:
        import winreg
        
        hp_info = {}
        
        # Check BIOS registry for HP-specific data
        try:
            bios_key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\BIOS"
            )
            
            # SystemSKU (HP Product Number/SKU)
            try:
                sku = winreg.QueryValueEx(bios_key, "SystemSKU")[0]
                if sku and sku.strip():
                    hp_info['system_sku'] = sku.strip()
            except (FileNotFoundError, OSError):
                pass
            
            # SystemFamily (HP family identifier)
            try:
                family = winreg.QueryValueEx(bios_key, "SystemFamily")[0]
                if family and family.strip():
                    hp_info['system_family'] = family.strip()
            except (FileNotFoundError, OSError):
                pass
            
            winreg.CloseKey(bios_key)
        except (FileNotFoundError, OSError):
            pass
        
        # Check HP Software registry for additional info
        try:
            hp_key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\HP"
            )
            
            # Look for product information in subkeys
            try:
                subkey_count = winreg.QueryInfoKey(hp_key)[0]
                for i in range(min(subkey_count, 10)):  # Limit to first 10 subkeys
                    try:
                        subkey_name = winreg.EnumKey(hp_key, i)
                        if 'product' in subkey_name.lower() or 'model' in subkey_name.lower():
                            subkey = winreg.OpenKey(hp_key, subkey_name)
                            try:
                                # Try to get product number or model
                                try:
                                    product = winreg.QueryValueEx(subkey, "ProductNumber")[0]
                                    if product and product.strip():
                                        hp_info['product_number'] = product.strip()
                                except (FileNotFoundError, OSError):
                                    pass
                            finally:
                                winreg.CloseKey(subkey)
                    except (OSError, WindowsError):
                        continue
            except (OSError, WindowsError):
                pass
            
            winreg.CloseKey(hp_key)
        except (FileNotFoundError, OSError):
            pass
        
        if hp_info:
            logging.info(f"HP-specific info found: {hp_info}")
            return hp_info
        
        return None
    except Exception as e:
        logging.debug(f"Failed to get HP-specific info: {e}")
        return None


def _get_desktop_type(com_wmi, system_type=''):
    """Detect detailed desktop classification: Brand/Gaming PC/Custom Build using COM/WMI

    Enhanced to distinguish between:
    - Specific OEM brands (Dell, HP, Lenovo, etc.)
    - Gaming PCs (Alienware, CyberPowerPC, iBuyPower, etc.)
    - Custom builds (motherboard manufacturers, placeholders)
    """
    if "Desktop" not in system_type and "All-in-One" not in system_type:
        return None  # Not a desktop

    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_ComputerSystem")
                if items and items.Count > 0:
                    computer_system = items.ItemIndex(0)
                    manufacturer = (computer_system.Properties_("Manufacturer").Value or "").strip().upper()
                    model = (computer_system.Properties_("Model").Value or "").strip().upper()

                    # Debug logging
                    logging.info(f"Desktop Type Detection (COM/WMI):")
                    logging.info(f"  Manufacturer: '{manufacturer}'")
                    logging.info(f"  Model: '{model}'")

                    # PRIORITY 1: Check for placeholder values (indicates custom build)
                    placeholder_models = [
                        "TO BE FILLED BY O.E.M.", "DEFAULT STRING",
                        "SYSTEM PRODUCT NAME", "N/A", "SYSTEM NAME"
                    ]
                    is_placeholder = any(ph in model for ph in placeholder_models)

                    if is_placeholder:
                        logging.info(f"  [CUSTOM] Placeholder model detected -> Custom Build")
                        return "Custom Build"

                    # PRIORITY 2: Check if it's a motherboard manufacturer (custom build)
                    # These brands primarily make motherboards, so system manufacturer = custom build
                    # BUT check for exceptions like ASUS ROG gaming PCs
                    mobo_manufacturers = ["GIGABYTE", "ASROCK", "EVGA", "BIOSTAR", "SUPERMICRO"]

                    # Special handling for ASUS and MSI - they make both mobos AND gaming PCs
                    if "ASUS" in manufacturer or "ASUSTEK" in manufacturer:
                        # Check if it's a gaming PC (ROG series with specific models)
                        rog_models = {
                            "ROG STRIX": "Gaming PC (ASUS ROG Strix)",
                            "G11": "Gaming PC (ASUS ROG G11)",
                            "G20": "Gaming PC (ASUS ROG G20)",
                            "G21": "Gaming PC (ASUS ROG G21)",
                            "G22": "Gaming PC (ASUS ROG G22)",
                            "GL10": "Gaming PC (ASUS ROG GL10)",
                            "GR8": "Gaming PC (ASUS ROG GR8)",
                            "GT51": "Gaming PC (ASUS ROG GT51)",
                            "GD30": "Gaming PC (ASUS ROG GD30)"
                        }
                        
                        for model_key, label in rog_models.items():
                            if model_key in model:
                                logging.info(f"  [GAMING] {label} detected")
                                return label
                        
                        # Generic ROG detection (fallback)
                        if "ROG" in model or "REPUBLIC OF GAMERS" in model:
                            logging.info(f"  [GAMING] ASUS ROG Gaming PC detected")
                            return "Gaming PC (ASUS ROG)"
                        
                        # Not a gaming PC - it's a custom build with ASUS mobo
                        logging.info(f"  [CUSTOM] ASUS motherboard detected -> Custom Build")
                        return "Custom Build"

                    if "MSI" in manufacturer:
                        # Check if it's a gaming PC (with specific model series)
                        msi_models = {
                            "TRIDENT": "Gaming PC (MSI Trident)",
                            "AEGIS": "Gaming PC (MSI Aegis)",
                            "INFINITE": "Gaming PC (MSI Infinite)",
                            "CODEX": "Gaming PC (MSI Codex)",
                            "MAG": "Gaming PC (MSI MAG)",
                            "MPG": "Gaming PC (MSI MPG)",
                            "MEG": "Gaming PC (MSI MEG)"
                        }
                        
                        for model_key, label in msi_models.items():
                            if model_key in model:
                                logging.info(f"  [GAMING] {label} detected")
                                return label
                        
                        # Not a gaming PC - it's a custom build with MSI mobo
                        logging.info(f"  [CUSTOM] MSI motherboard detected -> Custom Build")
                        return "Custom Build"
                    
                    # ACER - check for gaming lines BEFORE general OEM detection
                    if "ACER" in manufacturer:
                        acer_gaming_models = {
                            "PREDATOR ORION": "Gaming PC (Acer Predator Orion)",
                            "PREDATOR G": "Gaming PC (Acer Predator G)",
                            "PREDATOR": "Gaming PC (Acer Predator)",  # Generic Predator
                            "NITRO N": "Gaming PC (Acer Nitro)",
                            "NITRO 5": "Gaming PC (Acer Nitro)",
                            "ASPIRE GX": "Gaming PC (Acer Aspire GX)"
                        }
                        
                        for model_key, label in acer_gaming_models.items():
                            if model_key in model:
                                logging.info(f"  [GAMING] {label} detected")
                                return label
                        
                        # Not gaming - will be caught by OEM detection below

                    # Other motherboard manufacturers (always custom builds)
                    for mobo in mobo_manufacturers:
                        if mobo in manufacturer:
                            logging.info(f"  [CUSTOM] Motherboard manufacturer '{mobo}' detected -> Custom Build")
                            return "Custom Build"

                    # PRIORITY 3: Gaming PC brands (SI gaming builders + OEM gaming lines)
                    
                    # Dell Alienware and G-series gaming
                    if "DELL" in manufacturer:
                        dell_gaming_models = {
                            "ALIENWARE": "Gaming PC (Dell Alienware)",
                            "G3": "Gaming PC (Dell G3)",
                            "G5": "Gaming PC (Dell G5)",
                            "G7": "Gaming PC (Dell G7)",
                            "XPS TOWER": "Gaming PC (Dell XPS Tower)"  # XPS Tower is gaming-capable
                        }
                        
                        for model_key, label in dell_gaming_models.items():
                            if model_key in model:
                                logging.info(f"  [GAMING] {label} detected")
                                return label
                        
                        # Not gaming - will be caught by OEM detection below
                    
                    # HP Omen gaming line
                    if ("HP" in manufacturer or "HEWLETT-PACKARD" in manufacturer):
                        hp_gaming_models = {
                            "OMEN": "Gaming PC (HP Omen)",
                            "PAVILION GAMING": "Gaming PC (HP Pavilion Gaming)",
                            "ENVY GAMING": "Gaming PC (HP Envy Gaming)"
                        }
                        
                        for model_key, label in hp_gaming_models.items():
                            if model_key in model:
                                logging.info(f"  [GAMING] {label} detected")
                                return label
                        
                        # Not gaming - will be caught by OEM detection below
                    
                    # Lenovo Legion gaming line
                    if "LENOVO" in manufacturer:
                        lenovo_gaming_models = {
                            "LEGION T": "Gaming PC (Lenovo Legion T)",
                            "LEGION C": "Gaming PC (Lenovo Legion C)",
                            "LEGION Y": "Gaming PC (Lenovo Legion Y)",
                            "LEGION": "Gaming PC (Lenovo Legion)",  # Generic Legion
                            "IDEACENTRE GAMING": "Gaming PC (Lenovo IdeaCentre Gaming)"
                        }
                        
                        for model_key, label in lenovo_gaming_models.items():
                            if model_key in model:
                                logging.info(f"  [GAMING] {label} detected")
                                return label
                        
                        # Not gaming - will be caught by OEM detection below
                    
                    # System Integrator gaming brands (boutique builders)
                    gaming_brands = {
                        "ALIENWARE": "Gaming PC (Alienware)",  # Fallback if not caught by Dell detection
                        "AFTERSHOCK": "Gaming PC (Aftershock)",  # Australian/Singaporean brand (Clevo/Tongfang reseller)
                        "METABOX": "Gaming PC (Metabox)",  # Australian Clevo reseller
                        "CYBERPOWERPC": "Gaming PC (CyberPowerPC)",
                        "IBUYPOWER": "Gaming PC (iBuyPower)",
                        "ORIGIN PC": "Gaming PC (Origin PC)",
                        "MAINGEAR": "Gaming PC (Maingear)",
                        "FALCON NORTHWEST": "Gaming PC (Falcon Northwest)",
                        "DIGITAL STORM": "Gaming PC (Digital Storm)",
                        "RAZER": "Gaming PC (Razer)",
                        "CORSAIR": "Gaming PC (Corsair)",
                        "NZXT": "Gaming PC (NZXT)",
                        "SKYTECH": "Gaming PC (Skytech Gaming)",
                        "ABS": "Gaming PC (ABS)",  # Newegg's house brand
                        "POWERSPEC": "Gaming PC (PowerSpec)",  # Micro Center's brand
                        "PLE COMPUTERS": "Gaming PC (PLE Computers)",  # Australian brand
                        "SCORPTEC": "Gaming PC (Scorptec)"  # Australian brand
                    }

                    for brand, label in gaming_brands.items():
                        if brand in manufacturer:
                            logging.info(f"  [GAMING] {brand} detected")
                            return label

                    # PRIORITY 4: Business OEM manufacturers (Dell, HP, Lenovo, etc.)
                    business_oem_brands = {
                        "DELL": "Dell Desktop",
                        "HP": "HP Desktop",
                        "HEWLETT-PACKARD": "HP Desktop",
                        "LENOVO": "Lenovo Desktop",
                        "ACER": "Acer Desktop",
                        "GATEWAY": "Gateway Desktop",
                        "EMACHINES": "eMachines Desktop",
                        "COMPAQ": "Compaq Desktop",
                        "PACKARD BELL": "Packard Bell Desktop"
                    }

                    for brand, label in business_oem_brands.items():
                        if brand in manufacturer:
                            # Check if it's specifically an OptiPlex (Dell business line)
                            if brand == "DELL" and "OPTIPLEX" in model:
                                logging.info(f"  [OEM] Dell OptiPlex detected")
                                return "Dell OptiPlex"
                            # Check if it's EliteDesk/ProDesk (HP business line)
                            elif (brand == "HP" or brand == "HEWLETT-PACKARD") and ("ELITEDESK" in model or "PRODESK" in model):
                                logging.info(f"  [OEM] HP EliteDesk/ProDesk detected")
                                return "HP Business Desktop"
                            else:
                                logging.info(f"  [OEM] {brand} detected")
                                return label

                    # Unknown manufacturer
                    logging.info(f"  [UNKNOWN] Unknown manufacturer: '{manufacturer}'")
                    return "Unknown"
            except Exception as e:
                logging.debug(f"Failed to detect desktop type via COM/WMI: {e}")

        return "Unknown"
    except Exception as e:
        logging.warning(f"Failed to detect desktop type: {e}")
        return "Unknown"


# PowerShell desktop type and laptop model functions removed - using COM/WMI only


def _get_storage_health(wmi_conn):
    """Get storage health information including SMART status (legacy format)"""
    health_info = []
    
    try:
        if wmi_conn:
            try:
                # Get physical drives
                disks = wmi_conn.Win32_DiskDrive()
                for disk in disks:
                    if not disk.Model or not disk.Size or int(disk.Size) == 0:
                        continue
                    
                    disk_info = f"{disk.Model}"
                    
                    # Try to get SMART attributes via PowerShell (WMI doesn't expose SMART directly)
                    smart_info = _get_disk_smart_info(disk.Index)
                    if smart_info:
                        disk_info += f" - {smart_info}"
                    
                    health_info.append(disk_info)
            except Exception as e:
                logging.debug(f"Failed to get storage health via WMI: {e}")
        
        if health_info:
            return "\n".join(health_info)
        return "Storage health information unavailable"
    except Exception as e:
        logging.warning(f"Failed to get storage health: {e}")
        return "Storage health information unavailable"


def _get_storage_health_structured(com_wmi):
    """Get storage health with structured data and SMART interpretation for GUI display using COM/WMI"""
    drives = []
    
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_DiskDrive")
                if items and items.Count > 0:
                    for i in range(items.Count):
                        disk = items.ItemIndex(i)
                        model = disk.Properties_("Model").Value
                        size = disk.Properties_("Size").Value
                        disk_index = disk.Properties_("Index").Value
                        
                        if not model or not size or int(size) == 0:
                            continue
                        
                        drive_model = model.strip()
                        drive_size_gb = round(int(size) / (1024**3), 1) if size else 0

                        # Get partition style (GPT or MBR) via PowerShell Get-Disk
                        partition_style = None
                        try:
                            import subprocess
                            ps_result = subprocess.run(
                                ['powershell', '-NoProfile', '-Command',
                                 f'(Get-Disk -Number {disk_index} -ErrorAction SilentlyContinue).PartitionStyle'],
                                capture_output=True, text=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW
                            )
                            style = ps_result.stdout.strip().upper()
                            if style in ('GPT', 'MBR', 'RAW'):
                                partition_style = style
                        except Exception:
                            pass

                        drive_info = {
                            'model': drive_model,
                            'size_gb': drive_size_gb,
                            'disk_index': disk_index,
                            'partition_style': partition_style,
                            'media_type': disk.Properties_("MediaType").Value or "Unknown",
                            'interface': disk.Properties_("InterfaceType").Value or "Unknown",
                            'bus_type': _get_disk_bus_type(disk_index),
                        }
                        drive_info['friendly_type'] = _classify_basic_drive_type(
                            drive_info.get('model'),
                            drive_info.get('media_type'),
                            drive_info.get('interface'),
                            drive_info.get('bus_type'),
                        )

                        # USB drives - Skip SMART checking (unreliable for USB devices)
                        if 'USB' in drive_model.upper():
                            drive_info['status'] = 'N/A'
                            drive_info['health_percent'] = None
                            drive_info['interpretation'] = {
                                'score': None,
                                'grade': 'N/A',
                                'recommendation': 'SMART data not reliable for USB devices',
                                'critical_issues': [],
                                'warnings': []
                            }
                            logging.debug(f"USB drive detected: {drive_model} - skipping SMART check")
                        else:
                            # Get SMART status and health percentage for non-USB drives
                            smart_data = _get_disk_smart_structured(disk_index)
                            if smart_data:
                                drive_info.update(smart_data)
                            else:
                                drive_info['status'] = 'Unknown'
                                drive_info['health_percent'] = None

                            # Interpret SMART data
                            interpretation = _interpret_smart_data(smart_data, drive_model, drive_size_gb)
                            drive_info['interpretation'] = interpretation
                        
                        drives.append(drive_info)
            except Exception as e:
                logging.debug(f"Failed to get storage health via COM/WMI: {e}")
        
        return drives if drives else []
    except Exception as e:
        logging.warning(f"Failed to get storage health: {e}")
        return []


def _get_disk_smart_info(disk_index):
    """Get SMART status for a specific disk using PowerShell (legacy format)"""
    if platform.system() != "Windows":
        return None
    
    try:
        ps_script = f'''
        try {{
            # Try to get SMART status using Get-PhysicalDisk (Windows 8+)
            $disk = Get-PhysicalDisk -DeviceNumber {disk_index} -ErrorAction SilentlyContinue
            if ($disk) {{
                $healthStatus = $disk.HealthStatus
                $operationalStatus = $disk.OperationalStatus
                
                if ($healthStatus) {{
                    $status = switch ($healthStatus) {{
                        "Healthy" {{ "Good" }}
                        "Warning" {{ "Caution" }}
                        "Unhealthy" {{ "Bad" }}
                        default {{ $healthStatus }}
                    }}
                    
                    # Get additional info if available
                    $info = $status
                    if ($disk.MediaType) {{
                        $info += " ({0})" -f $disk.MediaType
                    }}
                    
                    Write-Output $info
                }}
            }}
        }} catch {{
            # Fallback: Try WMI SMART attributes
            try {{
                $smart = Get-WmiObject -Namespace "root\\wmi" -Class "MSStorageDriver_FailurePredictStatus" -ErrorAction SilentlyContinue | Where-Object {{ $_.InstanceName -like "*{disk_index}*" }}
                if ($smart -and $smart.PredictFailure -eq $false) {{
                    Write-Output "Good (SMART OK)"
                }} elseif ($smart -and $smart.PredictFailure -eq $true) {{
                    Write-Output "Caution (SMART Warning)"
                }}
            }} catch {{
                # If all methods fail, return nothing
            }}
        }}
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logging.debug(f"SMART info detection failed for disk {disk_index}: {e}")
    
    return None


def _get_disk_smart_structured(disk_index):
    """Get SMART status using smartctl - simple and accurate"""
    if platform.system() != "Windows":
        return None

    try:
        # Simple smartctl approach - most accurate and no admin required.
        # In bundled builds the binary may live at _MEIPASS/src/smartctl.exe
        # because the whole src tree is shipped as data.
        smartctl_path = _resolve_bundled_smartctl_path()
        logging.debug(f"Using smartctl path for disk {disk_index}: {smartctl_path}")

        ps_script = '''
        try {
            # Use local smartctl.exe from app directory
            $smartctlPath = "''' + smartctl_path.replace('\\', '\\\\') + '''"
            if (-not (Test-Path $smartctlPath)) {
                Write-Output "Error|0|Unknown|SMARTCTL_NOT_FOUND"
                exit
            }

            # Check if smartctl works
            $smartctlTest = & $smartctlPath --version 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Output "Error|0|Unknown|SMARTCTL_NOT_FOUND"
                exit
            }

            $diskIndex = ''' + str(disk_index) + '''

            $drivePath = "/dev/pd$diskIndex"

            # Detect drive type via Get-PhysicalDisk
            # Try DeviceId first (standard property), fall back to DeviceNumber
            $disk = Get-PhysicalDisk -ErrorAction SilentlyContinue |
                    Where-Object { $_.DeviceId -eq "$diskIndex" } |
                    Select-Object -First 1
            $busType = if ($disk) { $disk.BusType } else { "Unknown" }
            $mediaType = if ($disk) { $disk.MediaType } else { "Unknown" }
            $friendlyName = if ($disk) { $disk.FriendlyName } else { "" }
            $isNVMe = $friendlyName -like "NVMe*"

            # USB drives — skip SMART entirely, it's unreliable
            if ($busType -eq "USB") {
                Write-Output "N/A|0|USB|USB_DEVICE"
                exit
            }

            $smartInfo = $null
            $smartSuccess = $false
            $usedCSMI = $false

            if ($busType -eq "RAID") {
                # Intel RST (Rapid Storage Technology) detected.
                # SATA drives behind RST: accessible via CSMI interface
                # NVMe drives behind RST: smartctl cannot reach them (driver-level IOCTL block)

                if (-not $isNVMe) {
                    # SATA behind RST — try CSMI scan to find the right port
                    $scanOutput = & $smartctlPath --scan 2>&1 | Out-String
                    if ($scanOutput -match "/dev/csmi\\d+,(\\d+)") {
                        # Try each CSMI port found
                        $csmiPorts = [regex]::Matches($scanOutput, "/dev/(csmi\\d+,\\d+)")
                        foreach ($port in $csmiPorts) {
                            $csmiPath = "/dev/$($port.Groups[1].Value)"
                            $smartInfo = & $smartctlPath -a -d ata $csmiPath 2>&1
                            if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 4) {
                                # Verify this is the right disk by checking model/serial
                                $csmiOutput = $smartInfo | Out-String
                                $smartSuccess = $true
                                $usedCSMI = $true
                                break
                            }
                        }
                    }

                    # If CSMI didn't work, try SAT passthrough as fallback
                    if (-not $smartSuccess) {
                        $smartInfo = & $smartctlPath -a -d sat $drivePath 2>&1
                        if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 4) {
                            $smartSuccess = $true
                        }
                    }
                }

                if (-not $smartSuccess -and $isNVMe) {
                    # NVMe behind RST — try NVMe passthrough (works on some RST versions)
                    $smartInfo = & $smartctlPath -a -d nvme $drivePath 2>&1
                    if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 4) {
                        $smartSuccess = $true
                    }
                }

                if (-not $smartSuccess) {
                    # smartctl can't reach this drive through RST.
                    # Fall back to Windows storage health data — something is better than nothing.
                    $healthStatus = if ($disk) { $disk.HealthStatus } else { "Unknown" }
                    $opStatus = if ($disk) { $disk.OperationalStatus } else { "Unknown" }

                    # Get whatever StorageReliabilityCounter gives us
                    $rel = $null
                    try {
                        $rel = $disk | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue
                    } catch { }

                    $relTemp = if ($rel -and $rel.Temperature -gt 1) { $rel.Temperature } else { $null }
                    $relWear = if ($rel -and $rel.Wear) { $rel.Wear } else { $null }
                    $relHours = if ($rel -and $rel.PowerOnHours) { $rel.PowerOnHours } else { $null }

                    # Map Windows HealthStatus to our status scale
                    if ($healthStatus -eq "Healthy") {
                        $winStatus = "Good"
                        $winHealth = 95
                    } elseif ($healthStatus -eq "Warning") {
                        $winStatus = "Caution"
                        $winHealth = 60
                    } elseif ($healthStatus -eq "Unhealthy") {
                        $winStatus = "Bad"
                        $winHealth = 20
                    } else {
                        $winStatus = "Unknown"
                        $winHealth = 0
                    }

                    # Build output from Windows data
                    $output = "$winStatus|$winHealth"
                    if ($mediaType) { $output += "|MediaType:$mediaType" }
                    if ($busType) { $output += "|BusType:$busType" }
                    if ($relHours) { $output += "|PowerOnHours:$relHours" }
                    if ($relTemp) { $output += "|Temperature:$relTemp" }
                    if ($relWear) { $output += "|PercentageUsed:$relWear" }
                    $output += "|DataSource:WINDOWS_HEALTH_RST"
                    $output += "|RST:true"
                    $output += "|WindowsHealthStatus:$healthStatus"

                    Write-Output $output
                    exit
                }
            } else {
                # Non-RAID: standard smartctl path
                if ($busType -eq "NVMe" -or $isNVMe) {
                    $deviceType = "nvme"
                } else {
                    $deviceType = "sat"
                }

                $smartInfo = & $smartctlPath -a -d $deviceType $drivePath 2>&1

                # If that didn't work, try the other type
                if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 4) {
                    $altType = if ($deviceType -eq "nvme") { "sat" } else { "nvme" }
                    $smartInfo = & $smartctlPath -a -d $altType $drivePath 2>&1
                }

                if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 4) {
                    $smartSuccess = $true
                }
            }

            # Exit codes: 0=OK, 4=Some data unavailable (but drive may be healthy)
            if ($smartSuccess -and $smartInfo) {

                # Parse comprehensive SMART attributes for diagnostics
                    $reallocatedSectors = $null
                    $pendingSectors = $null
                    $powerOnHours = $null
                    $temperature = $null
                    $uncorrectableErrors = $null
                    $percentageUsed = $null
                    $availableSpare = $null
                    $criticalWarning = $null
                    $dataUnitsRead = $null
                    $dataUnitsWritten = $null
                    $powerCycles = $null
                    $unsafeShutdowns = $null
                    $mediaErrors = $null

                    foreach ($line in $smartInfo) {
                        # Parse traditional SMART attributes (SATA/HDD)
                        # Format: "ID# ATTRIBUTE_NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE"
                        # We want the RAW_VALUE at the end (after the dash)
                        if ($line -match "5\\s+Reallocated_Sector_Ct.*-\\s+(\\d+)") {
                            $reallocatedSectors = [int]$matches[1]
                        }
                        elseif ($line -match "197\\s+Pending_Sector.*-\\s+(\\d+)") {
                            $pendingSectors = [int]$matches[1]
                        }
                        elseif ($line -match "9\\s+Power_On_Hours.*-\\s+(\\d+)") {
                            $powerOnHours = [int]$matches[1]
                        }
                        elseif ($line -match "12\\s+Power_Cycle_Count.*-\\s+(\\d+)") {
                            $powerCycles = [int]$matches[1]
                        }
                        elseif ($line -match "194\\s+Temperature_Celsius.*-\\s+(\\d+)") {
                            $temperature = [int]$matches[1]
                        }
                        elseif ($line -match "190\\s+Airflow_Temperature_Cel.*-\\s+(\\d+)") {
                            if ($temperature -eq $null) {
                                $temperature = [int]$matches[1]
                            }
                        }
                        elseif ($line -match "187\\s+Uncorrectable_Error_Cnt.*-\\s+(\\d+)") {
                            $uncorrectableErrors = [int]$matches[1]
                        }
                        elseif ($line -match "198\\s+Uncorrectable_Error_Cnt.*-\\s+(\\d+)") {
                            if ($uncorrectableErrors -eq $null) {
                                $uncorrectableErrors = [int]$matches[1]
                            }
                        }
                        elseif ($line -match "177\\s+Wear_Leveling_Count\\s+\\S+\\s+(\\d+)") {
                            # For SATA SSDs, Wear_Leveling_Count VALUE column shows remaining life
                            # Starts at 100, decreases as drive wears out
                            # We want to show wear percentage (100 - remaining life)
                            $wearValue = [int]$matches[1]
                            $percentageUsed = 100 - $wearValue
                        }
                        elseif ($line -match "241\\s+Total_LBAs_Written.*-\\s+(\\d+)") {
                            # Convert LBAs to TB (assuming 512-byte sectors)
                            $lbas = [double]$matches[1]
                            $dataUnitsWritten = [Math]::Round(($lbas * 512) / 1099511627776, 1)
                        }
                        elseif ($line -match "195\\s+ECC_Error_Rate.*-\\s+(\\d+)") {
                            # ECC errors can indicate media issues
                            if ($mediaErrors -eq $null) {
                                $mediaErrors = [int]$matches[1]
                            }
                        }
                        elseif ($line -match "196\\s+Reallocated_Event_Count.*-\\s+(\\d+)") {
                            # Count of remap operations (HDD-specific)
                            # This is different from reallocated sector count
                            if ($reallocatedSectors -eq 0 -or $reallocatedSectors -eq $null) {
                                # Use this as a fallback indicator
                            }
                        }
                        elseif ($line -match "199\\s+UDMA_CRC_Error_Count.*-\\s+(\\d+)") {
                            # Cable/connection errors (common in HDDs)
                            # Not critical but can indicate cable issues
                        }
                        elseif ($line -match "200\\s+Multi_Zone_Error_Rate.*-\\s+(\\d+)") {
                            # Write errors (HDD-specific)
                            if ($mediaErrors -eq $null -or $mediaErrors -eq 0) {
                                $mediaErrors = [int]$matches[1]
                            }
                        }
                        elseif ($line -match "201\\s+Soft_Read_Error_Rate.*-\\s+(\\d+)") {
                            # Off-track errors (HDD-specific)
                            if ($mediaErrors -eq $null -or $mediaErrors -eq 0) {
                                $mediaErrors = [int]$matches[1]
                            }
                        }
                        elseif ($line -match "7\\s+Seek_Error_Rate.*-\\s+(\\d+)") {
                            # Seek errors (HDD-specific - mechanical)
                            # Usually a normalized value, not raw count
                        }
                        elseif ($line -match "10\\s+Spin_Retry_Count.*-\\s+(\\d+)") {
                            # Spin-up retry count (HDD-specific - mechanical)
                            # Non-zero value is concerning
                            $spinRetryCount = [int]$matches[1]
                            if ($spinRetryCount -gt 0 -and ($mediaErrors -eq $null -or $mediaErrors -eq 0)) {
                                $mediaErrors = $spinRetryCount
                            }
                        }
                        elseif ($line -match "11\\s+Calibration_Retry_Count.*-\\s+(\\d+)") {
                            # Calibration retry (HDD-specific)
                            # Non-zero indicates mechanical issues
                        }
                        # Parse NVMe attributes
                        elseif ($line -match "Critical Warning:\\s+0x([0-9A-Fa-f]+)") {
                            $criticalWarning = $matches[1]
                        }
                        elseif ($line -match "Temperature:\\s+(\\d+) Celsius") {
                            $temperature = [int]$matches[1]
                        }
                        elseif ($line -match "Available Spare:\\s+(\\d+)%") {
                            $availableSpare = [int]$matches[1]
                        }
                        elseif ($line -match "Percentage Used:\\s+(\\d+)%") {
                            $percentageUsed = [int]$matches[1]
                        }
                        elseif ($line -match "Data Units Read:\\s+([0-9,]+).*\\[([0-9.]+)\\s+TB\\]") {
                            $dataUnitsReadStr = $matches[2]  # Parse TB value from brackets
                            $dataUnitsRead = [double]$dataUnitsReadStr
                        }
                        elseif ($line -match "Data Units Written:\\s+([0-9,]+).*\\[([0-9.]+)\\s+TB\\]") {
                            $dataUnitsWrittenStr = $matches[2]  # Parse TB value from brackets
                            $dataUnitsWritten = [double]$dataUnitsWrittenStr
                        }
                        elseif ($line -match "Power Cycles:\\s+([0-9,]+)") {
                            $powerCyclesStr = $matches[1] -replace ",", ""
                            $powerCycles = [int]$powerCyclesStr
                        }
                        elseif ($line -match "Power On Hours:\\s+([0-9,]+)") {
                            $powerOnHoursStr = $matches[1] -replace ",", ""
                            $powerOnHours = [int]$powerOnHoursStr
                        }
                        elseif ($line -match "Unsafe Shutdowns:\\s+([0-9,]+)") {
                            $unsafeShutdownsStr = $matches[1] -replace ",", ""
                            $unsafeShutdowns = [int]$unsafeShutdownsStr
                        }
                        elseif ($line -match "Media and Data Integrity Errors:\\s+([0-9,]+)") {
                            $mediaErrorsStr = $matches[1] -replace ",", ""
                            $mediaErrors = [int]$mediaErrorsStr
                        }
                    }

                    # Calculate health percentage based on drive type
                    $healthPercent = 100
                    if ($percentageUsed -ne $null) {
                        # NVMe drive - use percentage used as primary health indicator
                        $healthPercent = 100 - $percentageUsed
                        if ($percentageUsed -gt 90) { $healthPercent -= 20 }
                        if ($percentageUsed -gt 95) { $healthPercent -= 30 }
                    } else {
                        # Traditional drive - use error counts for health calculation
                        $penalties = 0
                        if ($reallocatedSectors -and $reallocatedSectors -gt 0) {
                            $penalties += [Math]::Min(50, $reallocatedSectors * 5)
                        }
                        if ($pendingSectors -and $pendingSectors -gt 0) {
                            $penalties += [Math]::Min(40, $pendingSectors * 10)
                        }
                        if ($uncorrectableErrors -and $uncorrectableErrors -gt 0) {
                            $penalties += [Math]::Min(50, $uncorrectableErrors * 20)
                        }
                        $healthPercent = [Math]::Max(0, 100 - $penalties)
                    }

                    # Apply additional penalties
                    if ($powerOnHours -and $powerOnHours -gt 40000) {
                        $agePenalty = [Math]::Min(20, ($powerOnHours - 40000) / 2000)
                        $healthPercent -= $agePenalty
                    }

                    if ($temperature -and $temperature -gt 50) {
                        $tempPenalty = [Math]::Min(15, ($temperature - 50) / 2)
                        $healthPercent -= $tempPenalty
                    }

                    # Critical warning for NVMe drives
                    if ($criticalWarning -and $criticalWarning -ne "00") {
                        $healthPercent = [Math]::Max(0, $healthPercent - 50)
                    }

                    $healthPercent = [Math]::Max(0, [Math]::Round($healthPercent, 1))

                    # Determine status
                    if ($healthPercent -lt 30) {
                        $status = "Bad"
                    } elseif ($healthPercent -lt 70) {
                        $status = "Caution"
                    } else {
                        $status = "Good"
                    }

                    # Build comprehensive output with all diagnostic data
                    $output = "$status|$healthPercent"
                    if ($mediaType) { $output += "|MediaType:$mediaType" }
                    if ($busType) { $output += "|BusType:$busType" }
                    if ($powerOnHours) { $output += "|PowerOnHours:$powerOnHours" }
                    if ($temperature) { $output += "|Temperature:$temperature" }
                    if ($reallocatedSectors -ne $null) { $output += "|ReallocatedSectors:$reallocatedSectors" }
                    if ($pendingSectors -ne $null) { $output += "|PendingSectors:$pendingSectors" }
                    if ($uncorrectableErrors -ne $null) { $output += "|UncorrectableErrors:$uncorrectableErrors" }
                    if ($percentageUsed -ne $null) { $output += "|PercentageUsed:$percentageUsed" }
                    if ($availableSpare -ne $null) { $output += "|AvailableSpare:$availableSpare" }
                    if ($criticalWarning) { $output += "|CriticalWarning:$criticalWarning" }
                    if ($dataUnitsRead) { $output += "|DataUnitsRead:$dataUnitsRead" }
                    if ($dataUnitsWritten) { $output += "|DataUnitsWritten:$dataUnitsWritten" }
                    if ($powerCycles) { $output += "|PowerCycles:$powerCycles" }
                    if ($unsafeShutdowns) { $output += "|UnsafeShutdowns:$unsafeShutdowns" }
                    if ($mediaErrors -ne $null) { $output += "|MediaErrors:$mediaErrors" }

                    # Include the full SMART output for complete diagnostics
                    $smartOutput = ($smartInfo | Where-Object { $_ -and $_.Trim() }) -join "`n"
                    $output += "|FullSMARTOutput:$smartOutput"
                    if ($usedCSMI) {
                        $output += "|DataSource:REAL_SMART_CSMI_RST"
                        $output += "|RST:true"
                    } else {
                        $output += "|DataSource:REAL_SMART_SMARTCTL"
                    }

                Write-Output $output
            } else {
                Write-Output "Error|0|Unknown|SMART_FAILED"
            }
        } catch {
            Write-Output "Error|0|Unknown|EXCEPTION"
        }
        '''

        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )

        if result.returncode == 0 and result.stdout.strip():
            raw_output = result.stdout.strip()
            
            # Full raw SMART output at DEBUG level only
            logging.debug(f"SMART raw output for disk {disk_index}: {raw_output}")
            
            # Check for specific error patterns
            if "ACCESS_DENIED" in raw_output or "Error=5" in raw_output or "access denied" in raw_output.lower():
                logging.warning(f"SMART query failed for disk {disk_index}: Access Denied - Need Administrator privileges [TAG:SMART ACCESS_DENIED disk={disk_index}]")
                return None
            
            parts = raw_output.split('|')
            smart_data = {
                'status': parts[0] if len(parts) > 0 else 'Unknown',
                'health_percent': float(parts[1]) if len(parts) > 1 and parts[1] and parts[1] != '0' else None,
                'raw_status': raw_output,  # preserve full raw for failure detection
            }

            # Parse comprehensive SMART attributes for diagnostics
            for part in parts[2:]:
                if ':' in part:
                    key, value = part.split(':', 1)
                    if key == 'MediaType':
                        smart_data['media_type'] = value
                    elif key == 'BusType':
                        smart_data['bus_type'] = value
                    elif key == 'PowerOnHours':
                        try:
                            smart_data['power_on_hours'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'Temperature':
                        try:
                            smart_data['temperature'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'ReallocatedSectors':
                        try:
                            smart_data['reallocated_sectors'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'PendingSectors':
                        try:
                            smart_data['pending_sectors'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'UncorrectableErrors':
                        try:
                            smart_data['uncorrectable_errors'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'PercentageUsed':
                        try:
                            smart_data['percentage_used'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'AvailableSpare':
                        try:
                            smart_data['available_spare'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'CriticalWarning':
                        smart_data['critical_warning'] = value
                    elif key == 'DataUnitsRead':
                        try:
                            smart_data['data_units_read'] = float(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'DataUnitsWritten':
                        try:
                            smart_data['data_units_written'] = float(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'PowerCycles':
                        try:
                            smart_data['power_cycles'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'UnsafeShutdowns':
                        try:
                            smart_data['unsafe_shutdowns'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'MediaErrors':
                        try:
                            smart_data['media_errors'] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key == 'FullSMARTOutput':
                        smart_data['full_smart_output'] = value
                    elif key == 'DataSource':
                        smart_data['data_source'] = value
                    elif key == 'RST':
                        smart_data['rst'] = value == 'true'
                    elif key == 'WindowsHealthStatus':
                        smart_data['windows_health_status'] = value

            # Structured INFO-level summary (human-readable interpretation)
            status = smart_data.get('status', 'Unknown')
            health = smart_data.get('health_percent', 0)
            media = smart_data.get('media_type', 'Unknown')
            temp = smart_data.get('temperature')
            hours = smart_data.get('power_on_hours')
            realloc = smart_data.get('reallocated_sectors', 0)
            
            # Determine appropriate TAG based on data quality
            if status in ['Good', 'Caution', 'Bad'] and health:
                # Valid SMART data with health percentage
                tag_status = 'SUCCESS'
                log_level = logging.info
                summary_parts = [f"{status}"]
                if health:
                    summary_parts.append(f"{health}% health")
                if media and media != 'Unknown':
                    summary_parts.append(media)
                if hours:
                    summary_parts.append(f"{hours}h runtime")
                if temp:
                    summary_parts.append(f"{temp}°C")
                if realloc > 0:
                    summary_parts.append(f"⚠️ {realloc} reallocated sectors")
                
                log_level(f"SMART summary for disk {disk_index}: {', '.join(summary_parts)} [TAG:SMART {tag_status} disk={disk_index} health={health}]")
            elif status == 'N/A':
                # USB/external device - SMART not applicable
                logging.info(f"SMART summary for disk {disk_index}: N/A (SMART not reliable for USB devices) [TAG:SMART NO_DATA disk={disk_index} reason=usb]")
            else:
                # Error/Unknown - SMART query returned data but it's not usable
                logging.warning(f"SMART summary for disk {disk_index}: No valid SMART data (status={status}) [TAG:SMART NO_DATA disk={disk_index} status={status}]")
            
            return smart_data
        else:
            # SMART query failed - structured logging
            logging.warning(f"SMART query failed for disk {disk_index}: Return code {result.returncode} [TAG:SMART FAILED disk={disk_index} exitcode={result.returncode}]")
            # Full error output at DEBUG level
            logging.debug(f"SMART failure STDOUT for disk {disk_index}: {result.stdout if result.stdout else 'empty'}")
            logging.debug(f"SMART failure STDERR for disk {disk_index}: {result.stderr if result.stderr else 'empty'}")
    except Exception as e:
        logging.warning(f"SMART query exception for disk {disk_index}: {str(e)} [TAG:SMART EXCEPTION disk={disk_index}]")
        logging.debug(f"Full exception details for disk {disk_index}:", exc_info=True)
    
    return None


def _interpret_smart_data(smart_data, drive_model, drive_size_gb):
    """
    Interpret SMART data and generate health score, issues, warnings, and recommendations
    
    Args:
        smart_data: Dictionary with SMART attributes (status, health_percent, power_on_hours, temperature, etc.)
        drive_model: Drive model name
        drive_size_gb: Drive size in GB
    
    Returns:
        Dictionary with interpretation: score, grade, recommendation, critical_issues, warnings
    """
    if not smart_data:
        return {
            'score': None,
            'grade': 'N/A',
            'recommendation': 'SMART data unavailable',
            'critical_issues': [],
            'warnings': []
        }
    
    health_score = 100
    critical_issues = []
    warnings = []
    
    status = smart_data.get('status', 'Unknown')
    health_percent = smart_data.get('health_percent')
    power_on_hours = smart_data.get('power_on_hours')
    temperature = smart_data.get('temperature')
    media_type = smart_data.get('media_type', '')
    reallocated_sectors = smart_data.get('reallocated_sectors')
    pending_sectors = smart_data.get('pending_sectors')
    uncorrectable_errors = smart_data.get('uncorrectable_errors')
    data_source = smart_data.get('data_source', 'UNKNOWN')

    # Base score from calculated health percentage (now from real SMART data)
    if health_percent is not None:
        health_score = health_percent  # Use the calculated percentage directly
        if health_percent < 30:
            critical_issues.append(f"❌ Drive health critically low: {health_percent}%")
        elif health_percent < 70:
            warnings.append(f"⚠️ Drive health declining: {health_percent}%")
        elif health_percent < 85:
            warnings.append(f"ℹ️ Drive showing wear: {health_percent}%")
    else:
        # Fallback to status-based scoring if no percentage available
        if status == 'Bad':
            health_score = 20
            critical_issues.append("❌ Drive marked as UNHEALTHY by system")
        elif status == 'Caution':
            health_score = 70
            warnings.append("⚠️ Drive showing WARNING status - monitor closely")
        elif status == 'Good':
            health_score = 95
        else:
            # No health percentage AND no recognizable status — SMART is unavailable
            return {
                'score': None,
                'grade': 'N/A',
                'recommendation': 'SMART data unavailable',
                'critical_issues': [],
                'warnings': []
            }

    # Add specific SMART attribute warnings (even though they're already factored into health_percent)
    if reallocated_sectors and reallocated_sectors > 0:
        if reallocated_sectors > 10:
            critical_issues.append(f"❌ High reallocated sectors: {reallocated_sectors} - drive may fail soon")
        else:
            warnings.append(f"⚠️ Reallocated sectors detected: {reallocated_sectors}")

    if pending_sectors and pending_sectors > 0:
        critical_issues.append(f"❌ Pending sectors detected: {pending_sectors} - immediate backup recommended")

    if uncorrectable_errors and uncorrectable_errors > 0:
        critical_issues.append(f"❌ Uncorrectable read errors: {uncorrectable_errors} - data corruption risk")
    
    # Power-on hours analysis (if available)
    if power_on_hours:
        days = power_on_hours // 24
        if power_on_hours > 50000:  # ~5.7 years
            health_score -= 15
            critical_issues.append(f"⛔ Extremely high runtime: {power_on_hours:,} hours ({days:,} days)")
        elif power_on_hours > 30000:  # ~3.4 years
            health_score -= 10
            warnings.append(f"🕒 High runtime: {power_on_hours:,} hours ({days:,} days) - failure risk increasing")
        elif power_on_hours > 20000:  # ~2.3 years
            health_score -= 5
            warnings.append(f"ℹ️ Moderate runtime: {power_on_hours:,} hours ({days:,} days)")
    
    # Temperature analysis (if available)
    if temperature:
        if temperature > 60:
            health_score -= 10
            critical_issues.append(f"🌡️ Running very hot: {temperature}°C - check cooling")
        elif temperature > 50:
            health_score -= 5
            warnings.append(f"🌡️ Running warm: {temperature}°C - monitor temperature")
    
    # Media type considerations
    if 'HDD' in media_type or 'Hard Disk Drive' in media_type:
        # HDDs are more prone to failure than SSDs
        if health_score < 70:
            warnings.append("💾 Traditional HDD detected - consider SSD upgrade for reliability")
    
    # Ensure score stays in valid range
    health_score = max(0, min(100, health_score))
    
    # Generate letter grade
    if health_score >= 95:
        grade = 'A+'
    elif health_score >= 90:
        grade = 'A'
    elif health_score >= 85:
        grade = 'B+'
    elif health_score >= 80:
        grade = 'B'
    elif health_score >= 75:
        grade = 'C+'
    elif health_score >= 70:
        grade = 'C'
    elif health_score >= 60:
        grade = 'D'
    elif health_score >= 50:
        grade = 'F'
    else:
        grade = 'F'
    
    # Generate recommendation
    if data_source == 'ESTIMATED':
        recommendation = f"⚠️ LIMITED ANALYSIS - Using estimated health ({health_score:.1f}%). Run as Administrator for detailed SMART data."
    elif health_score < 50:
        recommendation = "❌ IMMEDIATE REPLACEMENT REQUIRED - Data loss imminent. Backup data immediately."
    elif health_score < 70:
        recommendation = "⚠️ BACKUP DATA NOW - Failure likely within 6 months. Recommend replacement soon."
    elif health_score < 85:
        recommendation = "ℹ️ Monitor closely - Drive showing early wear indicators. Consider replacement within 12 months."
    else:
        recommendation = "✅ Drive healthy - No immediate concerns"
    
    return {
        'score': health_score,
        'grade': grade,
        'recommendation': recommendation,
        'critical_issues': critical_issues,
        'warnings': warnings
    }


def _get_battery_static_data():
    """Get battery static data from root\\wmi BatteryStaticData (contains manufacturer, serial, etc.)"""
    if platform.system() != "Windows":
        return None
    
    try:
        ps_script = '''
        $battery = Get-WmiObject -Namespace root\\wmi -Class BatteryStaticData -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($battery) {
            $data = @{}
            if ($battery.DeviceName) { $data['device_name'] = $battery.DeviceName }
            if ($battery.ManufactureName) { $data['manufacture_name'] = $battery.ManufactureName }
            if ($battery.SerialNumber) { $data['serial_number'] = $battery.SerialNumber.Trim() }
            if ($battery.ManufactureDate) { $data['manufacture_date'] = $battery.ManufactureDate }
            if ($battery.Chemistry) { $data['chemistry_code'] = $battery.Chemistry }
            if ($battery.DesignedCapacity) { $data['designed_capacity_mwh'] = $battery.DesignedCapacity }
            if ($battery.UniqueID) { $data['unique_id'] = $battery.UniqueID.Trim() }
            # Also query BatteryFullChargedCapacity (separate class — current max capacity)
            $fullCap = Get-WmiObject -Namespace root\\wmi -Class BatteryFullChargedCapacity -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($fullCap -and $fullCap.FullChargedCapacity) { $data['full_charged_capacity_mwh'] = [int]$fullCap.FullChargedCapacity }
            $data | ConvertTo-Json -Compress
        }
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if data:
                logging.debug(f"BatteryStaticData found: {data}")
                return data
    except Exception as e:
        logging.debug(f"Failed to get BatteryStaticData: {e}")
    
    return None


def _get_battery_details(com_wmi):
    """Get comprehensive battery information - powercfg primary, BatteryStaticData for identification, WMI for real-time status"""
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_Battery")
                if not items or items.Count == 0:
                    return None  # No battery (desktop)
                
                # PRIORITY 1: Get comprehensive static data from powercfg (most reliable)
                logging.info("Collecting battery data from powercfg battery report...")
                powercfg_details = _get_battery_from_powercfg()
                
                if not powercfg_details:
                    logging.warning("powercfg battery report unavailable, falling back to WMI only")
                    powercfg_details = {}
                
                # PRIORITY 1.5: Get static data from root\\wmi BatteryStaticData (may have serial/model)
                logging.info("Collecting battery static data from root\\wmi...")
                static_data = _get_battery_static_data()
                
                # PRIORITY 2: Get real-time status from WMI (charge %, charging state)
                battery = items.ItemIndex(0)
                
                # Build complete battery details dictionary
                details = {}
                
                # === STATIC DATA (from powercfg - most reliable) ===
                
                # Design capacity (mAh) - converted from mWh
                if powercfg_details.get('design_capacity_mwh'):
                    details['design_capacity_mah'] = round(powercfg_details['design_capacity_mwh'] / 11.1)
                    details['design_capacity_mwh'] = powercfg_details['design_capacity_mwh']
                elif static_data and static_data.get('designed_capacity_mwh'):
                    # BatteryStaticData from root\wmi — works on aftermarket batteries
                    details['design_capacity_mwh'] = static_data['designed_capacity_mwh']
                    details['design_capacity_mah'] = round(static_data['designed_capacity_mwh'] / 11.1)
                    logging.info(f"Battery design capacity from BatteryStaticData WMI: {static_data['designed_capacity_mwh']} mWh")
                else:
                    # Fallback to WMI Win32_Battery (usually NULL)
                    design_cap = battery.Properties_("DesignCapacity").Value
                    details['design_capacity_mah'] = int(design_cap) if design_cap else None
                    details['design_capacity_mwh'] = None
                
                # Full charge capacity (mAh) - converted from mWh
                if powercfg_details.get('full_charge_capacity_mwh'):
                    details['full_charge_capacity_mah'] = round(powercfg_details['full_charge_capacity_mwh'] / 11.1)
                    details['full_charge_capacity_mwh'] = powercfg_details['full_charge_capacity_mwh']
                elif static_data and static_data.get('full_charged_capacity_mwh'):
                    # BatteryFullChargedCapacity from root\\wmi — works on aftermarket batteries
                    details['full_charge_capacity_mwh'] = static_data['full_charged_capacity_mwh']
                    details['full_charge_capacity_mah'] = round(static_data['full_charged_capacity_mwh'] / 11.1)
                    logging.info(f"Battery full charge capacity from BatteryFullChargedCapacity WMI: {static_data['full_charged_capacity_mwh']} mWh")
                else:
                    # Fallback to WMI Win32_Battery (usually NULL)
                    full_cap = battery.Properties_("FullChargeCapacity").Value
                    details['full_charge_capacity_mah'] = int(full_cap) if full_cap else None
                    details['full_charge_capacity_mwh'] = None
                
                # Cycle count (powercfg only - never available in WMI)
                details['cycle_count'] = powercfg_details.get('cycle_count')
                
                # Battery identification - PRIORITY ORDER:
                # 1. BatteryStaticData (root\wmi) - may have serial number
                # 2. powercfg - reliable for manufacturer/model
                # 3. WMI Win32_Battery - fallback only
                
                # Try BatteryStaticData first (best chance for serial number)
                if static_data:
                    if static_data.get('serial_number') and static_data['serial_number'].strip():
                        details['serial_number'] = static_data['serial_number'].strip()
                        logging.info(f"Battery serial from BatteryStaticData: {details['serial_number']}")
                    
                    if static_data.get('manufacture_name') and not details.get('manufacturer'):
                        details['manufacturer'] = static_data['manufacture_name'].strip()
                    
                    if static_data.get('device_name') and static_data['device_name'].strip() not in ['Primary', 'Microsoft']:
                        if not details.get('model_name'):
                            details['model_name'] = static_data['device_name'].strip()
                
                # Use powercfg data (reliable for manufacturer, but NAME is often "Primary")
                powercfg_used = False
                if powercfg_details.get('serial_number') and powercfg_details['serial_number'] not in ['-', '', 'N/A']:
                    if not details.get('serial_number'):  # Only use if BatteryStaticData didn't provide it
                        details['serial_number'] = powercfg_details['serial_number']
                        powercfg_used = True
                
                if powercfg_details.get('manufacturer') and not details.get('manufacturer'):
                    details['manufacturer'] = powercfg_details['manufacturer']
                    powercfg_used = True
                
                # Only use powercfg model_name if it's not generic
                if powercfg_details.get('model_name') and powercfg_details['model_name'] not in ['Primary', 'Microsoft', 'Unknown', '-']:
                    if not details.get('model_name'):
                        details['model_name'] = powercfg_details['model_name']
                        powercfg_used = True
                
                if powercfg_used or static_data:
                    logging.info(f"Battery identification: model={details.get('model_name', 'N/A')}, manufacturer={details.get('manufacturer', 'N/A')}, serial={details.get('serial_number', 'N/A')}")
                
                # WMI fallback ONLY if powercfg didn't provide the data
                # (WMI often returns generic names like "Primary" or "Microsoft")
                if not details.get('model_name'):
                    try:
                        wmi_name = battery.Properties_("Name").Value
                        if wmi_name and wmi_name.strip() and wmi_name.strip() not in ['Primary', 'Microsoft', 'Unknown']:
                            details['model_name'] = wmi_name.strip()
                            logging.debug(f"Battery model from WMI fallback: {wmi_name.strip()}")
                    except:
                        pass
                
                if not details.get('manufacturer'):
                    try:
                        # Try PNPDeviceID which sometimes has manufacturer info
                        pnp_id = battery.Properties_("PNPDeviceID").Value
                        if pnp_id:
                            # PNPDeviceID format: ACPI\\VEN_XXXX&DEV_XXXX
                            # Extract vendor code
                            ven_match = re.search(r'VEN_([A-Z0-9]+)', pnp_id)
                            if ven_match:
                                ven_code = ven_match.group(1)
                                # Map common vendor codes (though battery vendors are less standardized)
                                ven_map = {
                                    'HP': 'HP',
                                    'LGC': 'LG Chem',
                                    'SMP': 'Simplo',
                                    'CEL': 'Celxpert',
                                    'SANYO': 'Sanyo',
                                    'PANASONIC': 'Panasonic',
                                    'SONY': 'Sony',
                                }
                                if ven_code in ven_map:
                                    details['manufacturer'] = ven_map[ven_code]
                                    logging.debug(f"Battery manufacturer from PNPDeviceID: {ven_map[ven_code]}")
                    except:
                        pass
                
                # Chemistry - prefer powercfg, fallback to WMI
                if powercfg_details.get('chemistry'):
                    details['chemistry'] = powercfg_details['chemistry']
                else:
                    # WMI fallback (often returns "Unknown")
                    chemistry_map = {
                        1: "Other",
                        2: "Unknown",
                        3: "Lead Acid",
                        4: "Nickel Cadmium",
                        5: "Nickel Metal Hydride",
                        6: "Lithium-ion",
                        7: "Zinc air",
                        8: "Lithium Polymer"
                    }
                    chem = battery.Properties_("Chemistry").Value
                    details['chemistry'] = chemistry_map.get(int(chem), "Unknown") if chem else "Unknown"
                
                # === REAL-TIME DATA (from WMI - updated frequently) ===
                
                # Current charge remaining (%)
                charge = battery.Properties_("EstimatedChargeRemaining").Value
                details['charge_percent'] = int(charge) if charge is not None else None
                
                # Charging status (WMI first, psutil fallback for reliability)
                status_map = {
                    1: "Other",
                    2: "Unknown",
                    3: "Fully Charged",
                    4: "Low",
                    5: "Critical",
                    6: "Charging",
                    7: "Not Charging"
                }
                battery_status = battery.Properties_("BatteryStatus").Value
                if battery_status and int(battery_status) != 2:  # If not "Unknown"
                    details['status'] = status_map.get(int(battery_status), "Unknown")
                else:
                    # WMI returned Unknown - use psutil as more reliable fallback
                    try:
                        import psutil
                        psutil_battery = psutil.sensors_battery()
                        if psutil_battery:
                            if psutil_battery.power_plugged:
                                # Plugged in - check if fully charged
                                if details.get('charge_percent') and details['charge_percent'] >= 99:
                                    details['status'] = "Fully Charged"
                                else:
                                    details['status'] = "Charging"
                            else:
                                details['status'] = "On Battery"
                        else:
                            details['status'] = "Unknown"
                    except:
                        details['status'] = "Unknown"
                
                # === REAL-TIME POWER READINGS (root\\wmi BatteryStatus) ===
                # ChargeRate = mW flowing INTO battery (charging)
                # DischargeRate = mW flowing OUT of battery (on battery)
                # Voltage = current battery voltage in mV
                try:
                    import subprocess
                    ps_power = """
                    $b = Get-WmiObject -Namespace root\\wmi -Class BatteryStatus -ErrorAction SilentlyContinue
                    if ($b) {
                        $charge   = if ($b.ChargeRate)    { [int]$b.ChargeRate }    else { 0 }
                        $discharge= if ($b.DischargeRate) { [int]$b.DischargeRate } else { 0 }
                        $voltage  = if ($b.Voltage)       { [int]$b.Voltage }       else { 0 }
                        $plugged  = if ($b.PowerOnline)   { 'True' }                else { 'False' }
                        $runtime  = (Get-WmiObject Win32_Battery -ErrorAction SilentlyContinue).EstimatedRunTime
                        Write-Output "charge=$charge discharge=$discharge voltage=$voltage plugged=$plugged runtime=$runtime"
                    }
                    """
                    pr = subprocess.run(
                        ['powershell', '-NoProfile', '-Command', ps_power],
                        capture_output=True, text=True, timeout=8,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    out = pr.stdout.strip()
                    if out:
                        pvals = dict(kv.split('=', 1) for kv in out.split() if '=' in kv)

                        # AC power source
                        details['ac_power'] = pvals.get('plugged', '').lower() == 'true'

                        # Charge/discharge rate in watts (mW -> W)
                        charge_mw = int(pvals.get('charge', 0) or 0)
                        discharge_mw = int(pvals.get('discharge', 0) or 0)
                        if charge_mw > 0:
                            details['charge_rate_w'] = round(charge_mw / 1000, 1)
                        if discharge_mw > 0:
                            details['discharge_rate_w'] = round(discharge_mw / 1000, 1)

                        # Voltage in volts
                        voltage_mv = int(pvals.get('voltage', 0) or 0)
                        if voltage_mv > 0:
                            details['voltage_v'] = round(voltage_mv / 1000, 2)

                        # Estimated runtime (minutes) — only meaningful on battery
                        runtime_min = pvals.get('runtime', '')
                        try:
                            rt = int(runtime_min)
                            # 71582788 = WMI "unknown" sentinel value
                            if rt and rt < 71582788:
                                details['estimated_runtime_min'] = rt
                        except (ValueError, TypeError):
                            pass

                        logging.debug(f"[TAG:POWER] ac={details.get('ac_power')} "
                                      f"charge={details.get('charge_rate_w')}W "
                                      f"discharge={details.get('discharge_rate_w')}W "
                                      f"voltage={details.get('voltage_v')}V "
                                      f"runtime={details.get('estimated_runtime_min')}min")
                except Exception as e:
                    logging.debug(f"Power readings failed: {e}")

                # === CALCULATED DATA ===
                
                # Battery health percentage (wear level)
                if details.get('design_capacity_mah') and details.get('full_charge_capacity_mah'):
                    health_percent = round((details['full_charge_capacity_mah'] / details['design_capacity_mah']) * 100, 1)
                    details['health_percent'] = health_percent
                    
                    # Calculate wear percentage
                    wear_percent = round(100 - health_percent, 1)
                    details['wear_percent'] = wear_percent
                else:
                    details['health_percent'] = None
                    details['wear_percent'] = None
                
                # Convert capacity to Wh for user display (more common unit)
                if details.get('design_capacity_mwh'):
                    details['design_capacity_wh'] = round(details['design_capacity_mwh'] / 1000, 1)
                if details.get('full_charge_capacity_mwh'):
                    details['full_charge_capacity_wh'] = round(details['full_charge_capacity_mwh'] / 1000, 1)
                
                ac_str = "AC" if details.get('ac_power') else "Battery"
                rate_str = (f"{details['charge_rate_w']}W in" if details.get('charge_rate_w')
                            else f"{details.get('discharge_rate_w', '?')}W out" if details.get('discharge_rate_w')
                            else "rate unknown")
                logging.info(f"Battery: {details.get('health_percent')}% health, "
                             f"{details.get('cycle_count') or 'N/A'} cycles, "
                             f"{ac_str}, {rate_str}, {details.get('voltage_v', '?')}V "
                             f"[TAG:BATTERY health={details.get('health_percent')} "
                             f"cycles={details.get('cycle_count')} "
                             f"ac={details.get('ac_power')} "
                             f"voltage={details.get('voltage_v')}]")
                
                return details
            except Exception as e:
                logging.debug(f"Failed to get battery details: {e}")
        
        return None
    except Exception as e:
        logging.warning(f"Failed to get battery details: {e}")
        return None


def _get_battery_from_powercfg():
    """Get battery details from powercfg /batteryreport (fallback when WMI fails)"""
    if platform.system() != "Windows":
        return None
    
    import tempfile
    import re
    
    try:
        # Generate battery report HTML
        temp_file = os.path.join(tempfile.gettempdir(), 'battery_report.html')
        
        result = subprocess.run(
            ['powercfg', '/batteryreport', '/output', temp_file],
            capture_output=True,
            text=True,
            timeout=30,  # Aftermarket batteries can be slow to respond
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode != 0:
            logging.debug(f"powercfg /batteryreport failed: {result.stderr}")
            return None
        
        # Read HTML file
        with open(temp_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # Clean up temp file
        try:
            os.remove(temp_file)
        except:
            pass
        
        # Parse battery details from HTML using regex (multiple patterns for robustness)
        details = {}
        
        # Helper function to extract value after a label
        def extract_value(label_text, html):
            """Extract value from HTML table after a label"""
            invalid_values = ['', 'N/A', 'Unknown', '-', 'Not Available', 'NONE', '0']
            
            # Try multiple patterns for different HTML structures
            patterns = [
                # Standard format: <span class="label">LABEL</span></td><td>VALUE</td>
                rf'<span[^>]*class="label"[^>]*>{re.escape(label_text)}</span></td><td[^>]*>([^<]+)</td>',
                # Alternative: <td>LABEL</td><td>VALUE</td>
                rf'<td[^>]*>{re.escape(label_text)}</td><td[^>]*>([^<]+)</td>',
                # With whitespace: LABEL</td>...<td>VALUE</td>
                rf'{re.escape(label_text)}\s*</td>\s*<td[^>]*>([^<]+)</td>',
                # Label in quotes: "LABEL"...VALUE</td>
                rf'"{re.escape(label_text)}"[^>]*>([^<]+)</td>',
                # Case-insensitive search
                rf'(?i){re.escape(label_text)}[^>]*>([^<]+)</td>',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    value = match.group(1).strip()
                    # Clean up HTML entities
                    value = value.replace('&nbsp;', ' ').replace('&amp;', '&').strip()
                    if value and value not in invalid_values:
                        return value
            return None
        
        # NAME (Battery Model)
        model = extract_value('NAME', html_content)
        if model:
            details['model_name'] = model
        
        # MANUFACTURER
        manufacturer = extract_value('MANUFACTURER', html_content)
        if manufacturer:
            details['manufacturer'] = manufacturer
        
        # SERIAL NUMBER
        serial = extract_value('SERIAL NUMBER', html_content)
        if serial:
            details['serial_number'] = serial
        
        # CHEMISTRY
        chem_raw = extract_value('CHEMISTRY', html_content)
        if chem_raw:
            # Map abbreviations to full names
            chem_map = {
                'Li-I': 'Lithium-ion',
                'Li-Po': 'Lithium Polymer',
                'Li-ion': 'Lithium-ion',
                'LiP': 'Lithium Polymer',
                'LION': 'Lithium-ion',  # Common in powercfg reports
                'NiMH': 'Nickel Metal Hydride',
                'NiCd': 'Nickel Cadmium',
                'PbAc': 'Lead Acid'
            }
            details['chemistry'] = chem_map.get(chem_raw, chem_raw)
        
        # DESIGN CAPACITY (in mWh)
        match = re.search(r'<span class="label">DESIGN CAPACITY</span></td><td>([\d,]+)\s*mWh', html_content)
        if match:
            capacity_str = match.group(1).replace(',', '')
            details['design_capacity_mwh'] = int(capacity_str)
        
        # FULL CHARGE CAPACITY (in mWh)
        match = re.search(r'<span class="label">FULL CHARGE CAPACITY</span></td><td>([\d,]+)\s*mWh', html_content)
        if match:
            capacity_str = match.group(1).replace(',', '')
            details['full_charge_capacity_mwh'] = int(capacity_str)
        
        # CYCLE COUNT
        match = re.search(r'<span class="label">CYCLE COUNT</span></td><td>(\d+)', html_content)
        if match:
            details['cycle_count'] = int(match.group(1))
        
        # Log what we found
        if details:
            found_items = []
            if details.get('model_name'):
                found_items.append(f"model={details['model_name']}")
            if details.get('manufacturer'):
                found_items.append(f"manufacturer={details['manufacturer']}")
            if details.get('serial_number'):
                found_items.append(f"serial={details['serial_number']}")
            if found_items:
                logging.info(f"powercfg battery report: {', '.join(found_items)}")
            else:
                logging.debug("powercfg battery report: No identification data found (NAME/MANUFACTURER/SERIAL)")
        
        logging.debug(f"powercfg battery report parsed: {details}")
        return details if details else None
        
    except Exception as e:
        logging.debug(f"Failed to get battery details from powercfg: {e}")
        return None


def _get_battery_cycle_count():
    """Get battery cycle count using PowerShell (if available) - DEPRECATED, use powercfg fallback"""
    # This function is now deprecated in favor of _get_battery_from_powercfg()
    # Kept for backward compatibility
    return None


def _get_panel_details():
    """Get LCD panel details from EDID data (laptops only)"""
    if platform.system() != "Windows":
        return None
    
    try:
        ps_script = '''
        $panel = @{}
        
        # Get monitor ID (EDID data)
        $monitorID = Get-WmiObject -Namespace root\\wmi -Class WmiMonitorID -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($monitorID) {
            # Manufacturer code (3-letter code)
            $mfgCode = ($monitorID.ManufacturerName | ForEach-Object { if ($_ -ne 0) { [char]$_ } }) -join ''
            $panel['manufacturer_code'] = $mfgCode.Trim()
            
            # Map manufacturer codes to full names
            $mfgMap = @{
                'AUO' = 'AU Optronics'
                'BOE' = 'BOE Technology'
                'CMN' = 'Chimei Innolux'
                'LGD' = 'LG Display'
                'SDC' = 'Samsung'
                'SHP' = 'Sharp'
                'SEC' = 'Samsung'
                'LEN' = 'Lenovo'
                'APP' = 'Apple'
                'DEL' = 'Dell'
                'HWP' = 'HP'
                'CSO' = 'Chi Mei Optoelectronics'
                'INL' = 'Innolux'
                'PHL' = 'Philips'
                'IVO' = 'InfoVision'
            }
            if ($mfgMap.ContainsKey($mfgCode.Trim())) {
                $panel['manufacturer'] = $mfgMap[$mfgCode.Trim()]
            } else {
                $panel['manufacturer'] = $mfgCode.Trim()
            }
            
            # Year of manufacture
            if ($monitorID.YearOfManufacture) {
                $panel['manufacture_year'] = $monitorID.YearOfManufacture
            }
            
            # Week of manufacture
            if ($monitorID.WeekOfManufacture) {
                $panel['manufacture_week'] = $monitorID.WeekOfManufacture
            }
            
            # Serial number
            $serial = ($monitorID.SerialNumberID | ForEach-Object { if ($_ -ne 0) { [char]$_ } }) -join ''
            if ($serial -and $serial -ne '0') {
                $panel['serial_number'] = $serial.Trim()
            }
        }
        
        # Get physical size
        $basic = Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBasicDisplayParams -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($basic) {
            $hSize = $basic.MaxHorizontalImageSize
            $vSize = $basic.MaxVerticalImageSize
            if ($hSize -and $vSize) {
                # Calculate exact diagonal
                $diagonal = [math]::Sqrt([math]::Pow($hSize, 2) + [math]::Pow($vSize, 2)) / 2.54
                
                # Round to nearest standard panel size (industry standard)
                $standardSizes = @(10.1, 11.6, 12.5, 13.3, 14.0, 15.6, 17.3, 18.4, 21.5, 24.0, 27.0)
                $closestSize = $standardSizes[0]
                $minDiff = [math]::Abs($diagonal - $closestSize)
                
                foreach ($size in $standardSizes) {
                    $diff = [math]::Abs($diagonal - $size)
                    if ($diff -lt $minDiff) {
                        $minDiff = $diff
                        $closestSize = $size
                    }
                }
                
                $panel['size_inches'] = $closestSize
                $panel['size_inches_exact'] = [math]::Round($diagonal, 1)
                $panel['size_cm_h'] = $hSize
                $panel['size_cm_v'] = $vSize
            }
        }
        
        # Get native resolution
        $modes = Get-WmiObject -Namespace root\\wmi -Class WmiMonitorListedSupportedSourceModes -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($modes -and $modes.MonitorSourceModes) {
            $native = $modes.MonitorSourceModes | Sort-Object -Property HorizontalActivePixels -Descending | Select-Object -First 1
            if ($native) {
                $panel['resolution_h'] = $native.HorizontalActivePixels
                $panel['resolution_v'] = $native.VerticalActivePixels
            }
        }
        
        # Get connection type
        $conn = Get-WmiObject -Namespace root\\wmi -Class WmiMonitorConnectionParams -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) {
            # Extract model code from InstanceName (e.g., "DISPLAY\\CMN1514\\..." or "DISPLAY\\CMN15F5\\...")
            # HP Laptop pattern learned: "DISPLAY\\CMN15F5\\4&642b5b0&0&UID8388688_0"
            # Pattern: DISPLAY\\[MODELCODE]\\... where MODELCODE is extracted (e.g., CMN15F5)
            # See HP_LAPTOP_KNOWLEDGE_BASE.md for detailed LCD panel patterns
            # Try multiple patterns to catch different formats
            if ($conn.InstanceName) {
                # Pattern 1: DISPLAY\\MODELCODE\\ (most common, e.g., DISPLAY\\CMN15F5\\...)
                if ($conn.InstanceName -match 'DISPLAY\\\\+([A-Z0-9]{6,})\\\\') {
                    $panel['model_code'] = $matches[1]
                }
                # Pattern 2: \\MODELCODE\\ (fallback for other formats)
                elseif ($conn.InstanceName -match '\\\\([A-Z]{3}[A-Z0-9]{3,})\\\\') {
                    $panel['model_code'] = $matches[1]
                }
                # Pattern 3: Any alphanumeric code after backslashes (broader match)
                elseif ($conn.InstanceName -match '\\\\([A-Z0-9]{6,})\\\\') {
                    $panel['model_code'] = $matches[1]
                }
            }
            
            # VideoOutputTechnology codes
            $techMap = @{
                -1 = 'Internal'
                0 = 'VGA'
                1 = 'S-Video'
                2 = 'Composite'
                3 = 'Component'
                4 = 'DVI'
                5 = 'HDMI'
                6 = 'LVDS'
                8 = 'D-Jpn'
                9 = 'SDI'
                10 = 'DisplayPort (External)'
                11 = 'DisplayPort (Embedded)'
                12 = 'UDI (External)'
                13 = 'UDI (Embedded)'
                14 = 'SDTV Dongle'
                15 = 'Miracast'
                2147483648 = 'Internal (eDP/LVDS)'
            }
            if ($conn.VideoOutputTechnology -and $techMap.ContainsKey([int]$conn.VideoOutputTechnology)) {
                $panel['connection_type'] = $techMap[[int]$conn.VideoOutputTechnology]
            }
        }
        
        # Detect touch screen capability (ULTRA STRICT - only actual touch digitizers)
        # FALSE POSITIVES are EXTREMELY costly - touch panels cost 2-3x more than non-touch
        # DEFAULT TO FALSE unless we find definitive proof of touch hardware
        try {
            $isTouchScreen = $false
            
            # ONLY METHOD: Look for devices with "touch screen" EXPLICITLY in the name
            # This excludes: touchpads, mice, fingerprint sensors, etc.
            $touchDevices = Get-WmiObject -Class Win32_PnPEntity -ErrorAction SilentlyContinue | Where-Object {
                # Must have "touch screen" in the name (exact match, case insensitive)
                ($_.Name -like '*touch screen*' -and 
                 $_.Name -notlike '*touch pad*' -and       # Exclude touchpads
                 $_.Name -notlike '*touchpad*' -and        # Exclude touchpads
                 $_.Name -notlike '*fingerprint*' -and     # Exclude fingerprint sensors
                 $_.Name -notlike '*sensor*') -and         # Exclude generic sensors
                $_.Status -eq 'OK' -and                    # Must be working
                $_.ConfigManagerErrorCode -eq 0            # No errors
            }
            
            # Count actual matches
            if ($touchDevices) {
                $touchCount = ($touchDevices | Measure-Object).Count
                if ($touchCount -gt 0) {
                    $isTouchScreen = $true
                }
            }
            
            $panel['is_touch'] = $isTouchScreen
            
        } catch {
            # On ANY error, default to false (conservative approach - never guess)
            $panel['is_touch'] = $false
        }
        
        # Output as JSON
        $panel | ConvertTo-Json -Compress
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            panel_data = json.loads(result.stdout.strip())
            
            # Only return if we got meaningful data
            if panel_data and (panel_data.get('manufacturer') or panel_data.get('resolution_h')):
                logging.info(f"LCD Panel: {panel_data.get('size_inches', 'Unknown')}\" {panel_data.get('manufacturer', 'Unknown')} {panel_data.get('resolution_h')}x{panel_data.get('resolution_v')}, Touch={panel_data.get('is_touch', False)} [TAG:PANEL size={panel_data.get('size_inches')} touch={panel_data.get('is_touch')}]")
                logging.debug(f"LCD Panel full details: {panel_data}")
                return panel_data
        
        return None
    except Exception as e:
        logging.debug(f"Failed to get panel details: {e}")
        return None


def _get_screen_size(com_wmi):
    """Get screen size in inches for laptops using COM/WMI"""
    try:
        if com_wmi:
            try:
                items = _query_com_wmi(com_wmi, "Win32_DesktopMonitor")
                if items and items.Count > 0:
                    for i in range(items.Count):
                        monitor = items.ItemIndex(i)
                        width = monitor.Properties_("ScreenWidth").Value
                        height = monitor.Properties_("ScreenHeight").Value
                        
                        if width and height:
                            width_px = int(width)
                            height_px = int(height)
                            
                            # Calculate diagonal in inches
                            # Standard DPI assumptions: 96 DPI for most displays
                            width_inches = width_px / 96.0
                            height_inches = height_px / 96.0
                            diagonal_inches = (width_inches**2 + height_inches**2)**0.5
                            
                            # Round to common screen sizes
                            common_sizes = [10.1, 11.6, 12.5, 13.3, 14.0, 15.6, 17.3, 19.0, 21.5, 24.0, 27.0]
                            closest_size = min(common_sizes, key=lambda x: abs(x - diagonal_inches))
                            
                            # Get resolution
                            resolution = f"{width_px}x{height_px}"
                            
                            # Determine if FHD, QHD, etc.
                            if height_px >= 2160:
                                res_type = "4K"
                            elif height_px >= 1440:
                                res_type = "QHD"
                            elif height_px >= 1080:
                                res_type = "FHD"
                            elif height_px >= 768:
                                res_type = "HD"
                            else:
                                res_type = resolution
                            
                            return f"{closest_size}\" {res_type}"
            except Exception as e:
                logging.debug(f"Failed to get screen size via COM/WMI: {e}")
        
        return None
    except Exception as e:
        logging.warning(f"Failed to get screen size: {e}")
        return None


# PowerShell screen size function removed - using COM/WMI only


def _get_recent_critical_errors():
    """Get recent critical errors from Windows Event Log (last 7 days)"""
    errors = []
    
    if platform.system() != "Windows":
        return []
    
    try:
        ps_script = '''
        $cutoffDate = (Get-Date).AddDays(-7)
        $errors = Get-EventLog -LogName System -EntryType Error,Critical -After $cutoffDate -ErrorAction SilentlyContinue | 
            Select-Object -First 20 TimeGenerated, Source, EventID, Message
        
        $summary = @()
        foreach ($error in $errors) {
            $message = $error.Message -replace "`r`n", " " -replace "`n", " " -replace "`t", " "
            if ($message.Length -gt 100) {
                $message = $message.Substring(0, 100) + "..."
            }
            $summary += "$($error.TimeGenerated.ToString('yyyy-MM-dd HH:mm')) - $($error.Source) (ID: $($error.EventID)): $message"
        }
        
        if ($summary.Count -gt 0) {
            Write-Output ($summary -join "`n")
        } else {
            Write-Output "No critical errors in last 7 days"
        }
        '''
        
        result = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            error_lines = result.stdout.strip().split('\n')
            return error_lines[:10]  # Limit to 10 most recent
        
        return ["No critical errors found"]
    except Exception as e:
        logging.warning(f"Failed to get recent errors: {e}")
        return ["Error log retrieval unavailable"]


def _get_fallback_specs():
    """Return minimal specs using psutil when WMI is unavailable"""
    logging.warning("Using fallback specs - WMI unavailable")
    specs = {}
    
    specs['OS'] = platform.platform() or "Unknown"
    specs['CPU'] = platform.processor() or "Unknown"
    
    # RAM (psutil)
    try:
        ram = psutil.virtual_memory()
        ram_total_gb = round(ram.total / (1024**3), 2)
        ram_used_percent = ram.percent
        ram_available_gb = round(ram.available / (1024**3), 1)
        specs['RAM'] = f"{ram_total_gb} GB ({ram_used_percent}% used, {ram_available_gb} GB available)"
    except:
        specs['RAM'] = "Unknown"
    
    # GPU
    specs['GPU'] = "WMI unavailable - cannot detect GPU"
    
    # Motherboard
    specs['Motherboard'] = "WMI unavailable"
    
    # Serial Number
    specs['SerialNumber'] = "Not Available"
    
    # Battery (psutil)
    try:
        battery = psutil.sensors_battery()
        if battery:
            specs['Battery'] = f"{battery.percent}% ({'Charging' if battery.power_plugged else 'Discharging'})"
        else:
            specs['Battery'] = "Not Installed"
    except:
        specs['Battery'] = "Unknown"
    
    # Storage (psutil)
    try:
        storage_info = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                total_gb = round(usage.total / (1024**3), 2)
                free_gb = round(usage.free / (1024**3), 1)
                used_percent = usage.percent
                storage_info.append(f"Drive {partition.device}: {total_gb}GB total, {free_gb} GB free ({used_percent}% used)")
            except:
                continue
        specs['Storage'] = '\n'.join(storage_info) if storage_info else "Unknown"
    except:
        specs['Storage'] = "Unknown"
    
    # Network
    specs['Network'] = "WMI unavailable - cannot detect adapters"
    
    # Display
    specs['Display'] = "WMI unavailable"
    
    # System Health (psutil)
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory_percent = psutil.virtual_memory().percent
        specs['SystemHealth'] = f"CPU: {cpu_percent}%, Memory: {memory_percent}%"
    except:
        specs['SystemHealth'] = "Unknown"
    
    # Windows Details
    specs['WindowsDetails'] = "WMI unavailable"
    specs['BIOS'] = "WMI unavailable"
    
    # Phase 1 features
    specs['SystemType'] = "Unknown"
    specs['LaptopModel'] = "Not Available"
    specs['DesktopType'] = "Unknown"
    specs['StorageHealth'] = []
    
    # Phase 2 features
    specs['BatteryDetails'] = None
    specs['ScreenSize'] = None
    specs['RecentErrors'] = ["WMI unavailable - cannot retrieve error logs"]
    
    return specs


def _get_driver_info(com_wmi):
    """Get driver information, focusing on Intel chipset drivers.

    Uses a targeted WMI WHERE clause to avoid scanning all PnP drivers.
    """
    drivers = []

    if not com_wmi:
        return drivers

    try:
        # Pre-filter in WMI — only Intel-manufactured drivers
        query = ("SELECT DeviceName, Manufacturer, DriverVersion, DriverDate "
                 "FROM Win32_PnPSignedDriver "
                 "WHERE Manufacturer LIKE '%Intel%'")
        items = com_wmi.ExecQuery(query)
        if items and items.Count > 0:
            chipset_keywords = (
                'chipset', 'pci', 'usb', 'sata', 'ahci', 'raid',
                'lan', 'ethernet', 'audio', 'management engine',
                'smbus', 'serial io', 'thermal', 'mei', 'heci',
            )
            intel_drivers = []
            for i in range(items.Count):
                driver = items.ItemIndex(i)
                name = driver.Properties_("DeviceName").Value
                if not name:
                    continue
                name_lower = name.lower()
                if any(kw in name_lower for kw in chipset_keywords):
                    intel_drivers.append({
                        'name': name,
                        'manufacturer': 'Intel',
                        'version': driver.Properties_("DriverVersion").Value or 'Unknown',
                        'date': driver.Properties_("DriverDate").Value or 'Unknown',
                    })

            # Sort by name and return top 10 most relevant
            intel_drivers.sort(key=lambda x: x['name'])
            drivers = intel_drivers[:10]

    except Exception as e:
        logging.debug(f"Failed to get driver info: {e}")

    return drivers




def _get_macos_specs(progress_callback=None):
    """Get macOS-specific system specifications via system_profiler + psutil"""
    import json as _json

    def _progress(msg):
        if progress_callback:
            progress_callback(msg)

    def _profiler(data_type):
        """Run system_profiler for a given data type and return parsed JSON."""
        try:
            raw = subprocess.check_output(
                ["system_profiler", data_type, "-json"],
                text=True, timeout=15, stderr=subprocess.DEVNULL
            )
            return _json.loads(raw)
        except Exception as e:
            logging.debug(f"system_profiler {data_type} failed: {e}")
            return {}

    specs = {}

    # ── OS ────────────────────────────────
    _progress("Detecting system identity...")
    specs['OS'] = f"macOS {platform.mac_ver()[0]}"

    # ── Hardware overview (model, serial, CPU, RAM total) ──
    _progress("Reading hardware overview...")
    hw_data = _profiler("SPHardwareDataType")
    hw = {}
    try:
        hw = hw_data.get("SPHardwareDataType", [{}])[0]
    except (IndexError, TypeError):
        pass

    model_name = hw.get("machine_name", "") or hw.get("model_name", "")
    model_id = hw.get("machine_model", "") or hw.get("model_identifier", "")
    specs['LaptopModel'] = f"{model_name} ({model_id})" if model_name and model_id else model_name or model_id or "Unknown"
    specs['SerialNumber'] = hw.get("serial_number", "N/A")

    # System type
    model_lower = model_name.lower()
    if any(k in model_lower for k in ["macbook", "powerbook", "ibook"]):
        specs['SystemType'] = "Laptop"
    elif "imac" in model_lower:
        specs['SystemType'] = "All-in-One"
    elif any(k in model_lower for k in ["mac pro", "mac studio", "mac mini"]):
        specs['SystemType'] = "Desktop"
    else:
        specs['SystemType'] = "Desktop"

    specs['DesktopType'] = None  # Apple is always OEM

    # CPU
    _progress("Detecting CPU...")
    cpu_name = hw.get("chip_type", "") or hw.get("cpu_type", "")
    if not cpu_name:
        cpu_name = platform.processor() or "Unknown"
    try:
        cores = psutil.cpu_count(logical=False) or 0
        threads = psutil.cpu_count(logical=True) or 0
        freq = psutil.cpu_freq()
        base_ghz = f"{freq.max / 1000:.1f}" if freq and freq.max > 0 else ""
        cpu_str = cpu_name
        if base_ghz:
            cpu_str += f" | Base: {base_ghz} GHz"
        if cores and threads:
            cpu_str += f" ({cores}C/{threads}T)"
        specs['CPU'] = cpu_str
    except Exception:
        specs['CPU'] = cpu_name
    specs['CPUDetails'] = {}

    # ── RAM ───────────────────────────────
    _progress("Detecting RAM modules...")
    mem = psutil.virtual_memory()
    ram_total_gb = round(mem.total / (1024**3), 2)
    ram_used_pct = mem.percent
    ram_avail_gb = round(mem.available / (1024**3), 1)

    ram_modules = []
    ram_type = ""
    ram_speed = ""
    mem_data = _profiler("SPMemoryDataType")
    try:
        mem_items = mem_data.get("SPMemoryDataType", [{}])[0].get("_items", [])
        for slot in mem_items:
            mod = {}
            size_str = slot.get("dimm_size", "")
            size_match = re.search(r'(\d+)', str(size_str))
            if size_match:
                mod['size_gb'] = int(size_match.group(1))
            mod['slot'] = slot.get("_name", "")
            dtype = slot.get("dimm_type", "")
            if dtype:
                mod['type'] = dtype
                if not ram_type and dtype.startswith("DDR"):
                    ram_type = dtype
            spd = slot.get("dimm_speed", "")
            spd_match = re.search(r'(\d+)', str(spd))
            if spd_match:
                mod['speed'] = int(spd_match.group(1))
                if not ram_speed:
                    ram_speed = spd_match.group(1)
            mfr = slot.get("dimm_manufacturer", "")
            if mfr:
                mod['manufacturer'] = mfr
            part = slot.get("dimm_part_number", "")
            if part:
                mod['part_number'] = part
            if mod.get('size_gb') and mod['size_gb'] > 0:
                ram_modules.append(mod)
    except Exception:
        pass

    # Apple Silicon unified memory: no DIMMs, just report total
    module_count = len(ram_modules)
    if ram_type and ram_speed:
        specs['RAM'] = f"{ram_total_gb} GB {ram_type} @ {ram_speed}MHz ({module_count} modules) - {ram_used_pct}% used, {ram_avail_gb} GB available"
    elif hw.get("physical_memory"):
        specs['RAM'] = f"{ram_total_gb} GB Unified Memory - {ram_used_pct}% used, {ram_avail_gb} GB available"
    else:
        specs['RAM'] = f"{ram_total_gb} GB - {ram_used_pct}% used, {ram_avail_gb} GB available"
    specs['RAMDetails'] = ram_modules

    # ── GPU ───────────────────────────────
    _progress("Detecting GPU...")
    gpu_data = _profiler("SPDisplaysDataType")
    gpu_name = "Unknown"
    try:
        displays_list = gpu_data.get("SPDisplaysDataType", [])
        if displays_list:
            gpu_entry = displays_list[0]
            gpu_name = gpu_entry.get("sppci_model", "") or gpu_entry.get("_name", "Unknown")
            vram = gpu_entry.get("spdisplays_vram", "") or gpu_entry.get("spdisplays_vram_shared", "")
            if vram:
                gpu_name += f" ({vram})"
    except Exception:
        pass
    specs['GPU'] = gpu_name
    specs['GPUDetails'] = {}

    # ── Motherboard / BIOS ────────────────
    specs['Motherboard'] = model_id or "Apple Silicon" if "apple" in (hw.get("chip_type", "")).lower() else model_id or "Unknown"
    boot_rom = hw.get("boot_rom_version", "")
    os_loader = hw.get("os_loader_version", "")
    bios_parts = [p for p in ["Apple", boot_rom, os_loader] if p]
    specs['BIOS'] = ", ".join(bios_parts) if bios_parts else "Unknown"

    # ── Battery ───────────────────────────
    _progress("Checking battery...")
    battery = psutil.sensors_battery()
    if battery:
        specs['Battery'] = f"Installed ({battery.percent}%)"
        # Try to get detailed battery info from system_profiler
        pwr_data = _profiler("SPPowerDataType")
        bd = {}
        try:
            pwr_items = pwr_data.get("SPPowerDataType", [])
            for item in pwr_items:
                batt_info = item.get("sppower_battery_health_info", {})
                batt_model = item.get("sppower_battery_model_info", {})
                if batt_info:
                    cycle_str = batt_info.get("sppower_battery_cycle_count", "")
                    if cycle_str:
                        try:
                            bd['cycle_count'] = int(cycle_str)
                        except ValueError:
                            pass
                    condition = batt_info.get("sppower_battery_health", "")
                    if condition:
                        bd['condition'] = condition
                    max_cap = batt_info.get("sppower_battery_health_maximum_capacity", "")
                    if max_cap:
                        cap_match = re.search(r'(\d+)', str(max_cap))
                        if cap_match:
                            bd['health_percent'] = int(cap_match.group(1))
                if batt_model:
                    bd['manufacturer'] = batt_model.get("sppower_battery_manufacturer", "")
                    bd['model_name'] = batt_model.get("sppower_battery_device_name", "")
                    bd['serial_number'] = batt_model.get("sppower_battery_serial_number", "")
        except Exception:
            pass
        specs['BatteryDetails'] = bd if bd else None
    else:
        specs['Battery'] = "Not Installed"
        specs['BatteryDetails'] = None

    # ── Storage ───────────────────────────
    _progress("Scanning storage devices...")
    storage_parts = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            total_gb = round(usage.total / (1024**3), 1)
            free_gb = round(usage.free / (1024**3), 1)
            pct = usage.percent
            storage_parts.append(f"{part.device} {total_gb}GB ({pct}% used, {free_gb}GB free)")
        except Exception:
            pass
    specs['Storage'] = " | ".join(storage_parts) if storage_parts else "Unknown"

    # Try smartctl for SMART data
    storage_health = []
    try:
        scan_result = subprocess.check_output(
            ["smartctl", "--scan", "--json"],
            text=True, timeout=10, stderr=subprocess.DEVNULL
        )
        scan_data = _json.loads(scan_result)
        for device_entry in scan_data.get("devices", []):
            dev_name = device_entry.get("name", "")
            if not dev_name:
                continue
            try:
                smart_raw = subprocess.check_output(
                    ["smartctl", "-a", "--json", dev_name],
                    text=True, timeout=15, stderr=subprocess.DEVNULL
                )
                smart_data = _json.loads(smart_raw)
                drive = {}
                drive['model'] = smart_data.get("model_name", "") or smart_data.get("model_family", "Unknown")
                cap = smart_data.get("user_capacity", {})
                if cap.get("bytes"):
                    drive['size_gb'] = round(int(cap['bytes']) / (1024**3), 1)
                health = smart_data.get("smart_status", {})
                drive['smart_passed'] = health.get("passed", None)
                poh = smart_data.get("power_on_time", {})
                if poh.get("hours"):
                    drive['power_on_hours'] = poh['hours']
                temp_info = smart_data.get("temperature", {})
                if temp_info.get("current"):
                    drive['temperature'] = temp_info['current']
                rot = smart_data.get("rotation_rate", 0)
                if rot == 0:
                    drive['media_type'] = "SSD"
                elif rot and rot > 0:
                    drive['media_type'] = f"HDD ({rot} RPM)"
                pct_used = smart_data.get("nvme_smart_health_information_log", {}).get("percentage_used", None)
                if pct_used is not None:
                    drive['health_percent'] = max(0, 100 - pct_used)
                storage_health.append(drive)
            except Exception:
                pass
    except Exception:
        pass
    specs['StorageHealth'] = storage_health

    # ── Network ───────────────────────────
    _progress("Detecting network adapters...")
    try:
        addrs = psutil.net_if_addrs()
        net_parts = []
        for iface, addr_list in addrs.items():
            if iface == 'lo0':
                continue
            # macOS uses AF_LINK (psutil.AF_LINK = 18 on macOS)
            mac = None
            for a in addr_list:
                if a.family.name in ('AF_LINK', 'AF_PACKET'):
                    mac = a.address
                    break
            if mac and mac != '00:00:00:00:00:00':
                net_parts.append(f"{iface} ({mac})")
        specs['Network'] = " | ".join(net_parts) if net_parts else "Unknown"
    except Exception:
        specs['Network'] = "Unknown"

    # ── Display ───────────────────────────
    _progress("Detecting displays...")
    display_info = []
    try:
        disp_data = _profiler("SPDisplaysDataType")
        disp_num = 0
        for gpu_item in disp_data.get("SPDisplaysDataType", []):
            for screen in gpu_item.get("spdisplays_ndrvs", []):
                disp_num += 1
                name = screen.get("_name", f"Display {disp_num}")
                res = screen.get("_spdisplays_resolution", "")
                main_flag = " (Primary)" if screen.get("spdisplays_main") == "spdisplays_yes" else ""
                display_info.append(f"Display {disp_num} ({name}) - {res}{main_flag}")
    except Exception:
        pass
    specs['Display'] = "\n".join(display_info) if display_info else ""

    # ── System Health ─────────────────────
    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    days = uptime.days
    hours = uptime.seconds // 3600
    specs['SystemHealth'] = f"CPU Usage: {cpu_pct}% | Memory Usage: {mem.percent}% | Uptime: {days}d {hours}h"
    specs['BootTime'] = (f"Uptime: {days}d {hours}h", "ok" if days < 14 else "warning")

    # ── HP Specific (not applicable) ──────
    specs['HPSpecific'] = {}

    # ── Misc ──────────────────────────────
    specs['ScreenSize'] = None
    specs['PanelDetails'] = None
    specs['WindowsDetails'] = None
    specs['DriverInfo'] = None
    specs['RecentErrors'] = []
    specs['EventLogSummary'] = None
    specs['WindowsUpdateStatus'] = None
    specs['DefenderStatus'] = None
    specs['StartupItems'] = None
    specs['DeviceManagerIssues'] = None
    specs['ActivePowerPlan'] = None
    specs['NetworkDrivers'] = []

    return specs

def _dmidecode_field(dmi_type, field):
    """Read a single field from dmidecode."""
    try:
        output = subprocess.check_output(
            ["sudo", "dmidecode", "-t", dmi_type],
            text=True, timeout=5, stderr=subprocess.DEVNULL
        )
        for line in output.splitlines():
            line = line.strip()
            if line.startswith(f"{field}:"):
                val = line.split(":", 1)[1].strip()
                if val and val not in ("Default string", "To Be Filled By O.E.M.", "Not Specified", ""):
                    return val
    except Exception:
        pass
    return None


def _get_linux_specs(progress_callback=None, log_callback=None):
    """Get Linux-specific system specifications via dmidecode + lspci + psutil"""
    def _progress(msg):
        if progress_callback:
            progress_callback(msg)
        logging.info(msg)
        if log_callback:
            log_callback(f"  {msg}\n")

    specs = {}

    # ── OS ────────────────────────────────
    _progress("Detecting system identity...")
    specs['OS'] = platform.platform()

    # ── System / Model ────────────────────
    sys_manufacturer = _dmidecode_field("system", "Manufacturer") or ""
    sys_product = _dmidecode_field("system", "Product Name") or ""
    sys_version = _dmidecode_field("system", "Version") or ""
    sys_family = _dmidecode_field("system", "Family") or ""
    sys_sku = _dmidecode_field("system", "SKU Number") or ""
    sys_serial = _dmidecode_field("system", "Serial Number") or "N/A"

    # Build model string: prefer Version (human-readable), fall back to Product Name
    model = sys_version if sys_version else sys_product
    if sys_manufacturer and model and sys_manufacturer.lower() not in model.lower():
        model = f"{sys_manufacturer} {model}"
    elif not model:
        model = sys_manufacturer or "Unknown"

    specs['LaptopModel'] = model
    specs['SerialNumber'] = sys_serial

    # System type detection
    try:
        chassis_output = subprocess.check_output(
            ["sudo", "dmidecode", "-t", "chassis"],
            text=True, timeout=5, stderr=subprocess.DEVNULL
        )
        chassis_type = ""
        for line in chassis_output.splitlines():
            if "Type:" in line:
                chassis_type = line.split(":", 1)[1].strip().lower()
                break
        if any(t in chassis_type for t in ["laptop", "notebook", "portable", "sub notebook"]):
            specs['SystemType'] = "Laptop"
        elif any(t in chassis_type for t in ["desktop", "tower", "mini tower", "low profile", "space-saving"]):
            specs['SystemType'] = "Desktop"
        elif "all in one" in chassis_type:
            specs['SystemType'] = "All-in-One"
        else:
            specs['SystemType'] = "Desktop"
    except Exception:
        specs['SystemType'] = "Unknown"

    # OEM vs Custom Build
    oem_brands = ['lenovo', 'dell', 'hp', 'acer', 'asus', 'microsoft', 'apple', 'samsung', 'toshiba', 'fujitsu']
    is_oem = any(b in sys_manufacturer.lower() for b in oem_brands)
    specs['DesktopType'] = None if is_oem else "Custom Build"

    # HP specific
    specs['HPSpecific'] = {}
    if 'hp' in sys_manufacturer.lower():
        specs['HPSpecific'] = {'system_sku': sys_sku, 'system_family': sys_family}

    # ── CPU ───────────────────────────────
    _progress("Detecting CPU...")
    cpu_name = _dmidecode_field("processor", "Version") or platform.processor() or "Unknown"
    try:
        cores = psutil.cpu_count(logical=False) or 0
        threads = psutil.cpu_count(logical=True) or 0
        freq = psutil.cpu_freq()
        base_ghz = f"{freq.max / 1000:.1f}" if freq and freq.max > 0 else ""
        cpu_str = cpu_name
        if base_ghz:
            cpu_str += f" | Base: {base_ghz} GHz"
        if cores and threads:
            cpu_str += f" ({cores}C/{threads}T)"
        specs['CPU'] = cpu_str
    except Exception:
        specs['CPU'] = cpu_name

    specs['CPUDetails'] = {}

    # ── RAM ───────────────────────────────
    _progress("Detecting RAM modules...")
    mem = psutil.virtual_memory()
    ram_total_gb = round(mem.total / (1024**3), 2)
    ram_used_pct = mem.percent
    ram_avail_gb = round(mem.available / (1024**3), 1)

    ram_modules = []
    ram_type = ""
    ram_speed = ""
    try:
        dmi_mem = subprocess.check_output(
            ["sudo", "dmidecode", "-t", "memory"],
            text=True, timeout=5, stderr=subprocess.DEVNULL
        )
        current_module = {}
        in_device = False
        for line in dmi_mem.splitlines():
            stripped = line.strip()
            if stripped.startswith("Memory Device"):
                if current_module.get('size_gb') and current_module['size_gb'] > 0:
                    ram_modules.append(current_module)
                current_module = {}
                in_device = True
            elif stripped.startswith("Physical Memory Array"):
                if current_module.get('size_gb') and current_module['size_gb'] > 0:
                    ram_modules.append(current_module)
                current_module = {}
                in_device = False
            elif in_device and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if key == "Size":
                    size_match = re.search(r'(\d+)\s*(MB|GB)', val)
                    if size_match:
                        sz = int(size_match.group(1))
                        if size_match.group(2) == 'MB':
                            sz = round(sz / 1024, 1)
                        current_module['size_gb'] = sz
                    else:
                        current_module['size_gb'] = 0
                elif key == "Locator":
                    current_module['slot'] = val
                elif key == "Type" and val not in ('Unknown', 'Other', ''):
                    current_module['type'] = val
                    if not ram_type and val.startswith('DDR'):
                        ram_type = val
                elif key == "Speed" and 'MT/s' in val:
                    spd = re.search(r'(\d+)', val)
                    if spd:
                        current_module['speed'] = int(spd.group(1))
                        if not ram_speed:
                            ram_speed = spd.group(1)
                elif key == "Configured Memory Speed" and 'MT/s' in val:
                    spd = re.search(r'(\d+)', val)
                    if spd:
                        current_module['configured_speed'] = int(spd.group(1))
                elif key == "Manufacturer":
                    if val and val not in ('Unknown', 'Not Specified', ''):
                        current_module['manufacturer'] = val
                elif key == "Part Number":
                    if val and val.strip() not in ('Unknown', 'Not Specified', ''):
                        current_module['part_number'] = val.strip()
                elif key == "Form Factor":
                    if val and val not in ('Unknown', 'Other', ''):
                        current_module['form_factor'] = val
                elif key == "Configured Voltage":
                    current_module['voltage'] = val

        # Don't forget the last module
        if current_module.get('size_gb') and current_module['size_gb'] > 0:
            ram_modules.append(current_module)
    except Exception:
        pass

    # Build RAM summary string matching Windows format
    module_count = len(ram_modules)
    if ram_type and ram_speed:
        specs['RAM'] = f"{ram_total_gb} GB {ram_type} @ {ram_speed}MHz ({module_count} modules) - {ram_used_pct}% used, {ram_avail_gb} GB available"
    else:
        specs['RAM'] = f"{ram_total_gb} GB"
    specs['RAMDetails'] = ram_modules

    # ── GPU ───────────────────────────────
    _progress("Detecting GPU...")
    try:
        lspci = subprocess.check_output(["lspci"], text=True, timeout=5, stderr=subprocess.DEVNULL)
        gpu_lines = [l for l in lspci.splitlines() if 'VGA' in l or '3D controller' in l]
        if gpu_lines:
            # Extract just the device name (after the colon)
            gpu_name = gpu_lines[0].split(": ", 1)[-1] if ": " in gpu_lines[0] else gpu_lines[0]
            specs['GPU'] = gpu_name
        else:
            specs['GPU'] = "No discrete GPU detected"
    except Exception:
        specs['GPU'] = "Unknown"

    specs['GPUDetails'] = {}

    # ── Motherboard ───────────────────────
    mobo_manufacturer = _dmidecode_field("baseboard", "Manufacturer") or ""
    mobo_product = _dmidecode_field("baseboard", "Product Name") or ""
    if mobo_manufacturer and mobo_product:
        specs['Motherboard'] = f"{mobo_manufacturer} {mobo_product}"
    elif mobo_product:
        specs['Motherboard'] = mobo_product
    else:
        specs['Motherboard'] = "Unknown"

    # ── BIOS ──────────────────────────────
    bios_vendor = _dmidecode_field("bios", "Vendor") or ""
    bios_version = _dmidecode_field("bios", "Version") or ""
    bios_date = _dmidecode_field("bios", "Release Date") or ""
    bios_parts = [p for p in [bios_vendor, bios_version, bios_date] if p]
    specs['BIOS'] = ", ".join(bios_parts) if bios_parts else "Unknown"

    # ── Battery ───────────────────────────
    battery = psutil.sensors_battery()
    if battery:
        specs['Battery'] = f"Installed ({battery.percent}%)"
    else:
        specs['Battery'] = "Not Installed"
    specs['BatteryDetails'] = None

    # ── Storage ───────────────────────────
    _progress("Scanning storage devices...")
    storage_parts = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            total_gb = round(usage.total / (1024**3), 1)
            free_gb = round(usage.free / (1024**3), 1)
            pct = usage.percent
            storage_parts.append(f"{part.device} {total_gb}GB ({pct}% used, {free_gb}GB free)")
        except Exception:
            pass
    specs['Storage'] = " | ".join(storage_parts) if storage_parts else "Unknown"
    specs['StorageHealth'] = []

    # ── Network ───────────────────────────
    _progress("Detecting network adapters...")
    try:
        addrs = psutil.net_if_addrs()
        net_parts = []
        for iface, addr_list in addrs.items():
            if iface == 'lo':
                continue
            mac = next((a.address for a in addr_list if a.family.name == 'AF_PACKET'), None)
            if mac:
                net_parts.append(f"{iface} ({mac})")
        specs['Network'] = " | ".join(net_parts) if net_parts else "Unknown"
    except Exception:
        specs['Network'] = "Unknown"

    # ── Display ───────────────────────────
    _progress("Detecting displays...")
    display_info = []
    try:
        xrandr_out = subprocess.check_output(["xrandr", "--query"],
                                              text=True, timeout=5,
                                              stderr=subprocess.DEVNULL)
        display_num = 0
        for line in xrandr_out.splitlines():
            if ' connected' in line:
                display_num += 1
                parts = line.split()
                output_name = parts[0]
                is_primary = 'primary' in line
                # Find resolution: e.g., "1920x1080+0+0"
                res_match = re.search(r'(\d+x\d+)\+', line)
                resolution = res_match.group(1) if res_match else "Unknown"
                primary_tag = " (Primary)" if is_primary else ""
                display_info.append(f"Display {display_num} ({output_name}) - {resolution}{primary_tag}")
    except Exception:
        pass
    specs['Display'] = "\n".join(display_info) if display_info else ""

    # ── System Health ─────────────────────
    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    days = uptime.days
    hours = uptime.seconds // 3600
    specs['SystemHealth'] = f"CPU Usage: {cpu_pct}% | Memory Usage: {mem.percent}% | Uptime: {days}d {hours}h"
    specs['BootTime'] = (f"Uptime: {days}d {hours}h", "ok" if days < 14 else "warning")

    # ── Misc ──────────────────────────────
    specs['ScreenSize'] = None
    specs['WindowsDetails'] = None
    specs['DriverInfo'] = None
    specs['RecentErrors'] = []
    specs['EventLogSummary'] = None
    specs['WindowsUpdateStatus'] = None
    specs['DefenderStatus'] = None
    specs['StartupItems'] = None
    specs['DeviceManagerIssues'] = None
    specs['ActivePowerPlan'] = None

    return specs
