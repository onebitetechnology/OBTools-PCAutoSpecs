"""
Extended System Health Checks

Provides comprehensive system diagnostics:
- Event Viewer analysis (critical/error events)
- Windows Update health
- Microsoft Defender status
- Temperature monitoring (CPU/GPU)
- Startup impact analysis
- Disk speed testing

All functions fail gracefully with status: "unavailable" if checks cannot be performed.
"""

import subprocess
import json
import logging
import os
import time
import tempfile
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List


# Resolve full path to powershell.exe — Git Bash and portable envs don't have it on PATH
import sys as _sys
_POWERSHELL_EXE = os.path.join(
    os.environ.get('SYSTEMROOT', r'C:\Windows'),
    'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'
)
if not os.path.isfile(_POWERSHELL_EXE):
    _POWERSHELL_EXE = 'powershell.exe'  # fallback to PATH

_WMI_IMPORT_ATTEMPTED = False
_WMI_MODULE = None


def _can_auto_install_vendor_deps() -> bool:
    """Allow one-time pip installs only when running from source, not a frozen app."""
    return not getattr(_sys, 'frozen', False)


def _import_wmi():
    """Import the optional ``wmi`` package once and reuse the result."""
    global _WMI_IMPORT_ATTEMPTED, _WMI_MODULE
    if _WMI_IMPORT_ATTEMPTED:
        return _WMI_MODULE

    _WMI_IMPORT_ATTEMPTED = True
    try:
        import wmi as _wmi  # type: ignore
        _WMI_MODULE = _wmi
    except ImportError:
        logging.debug(
            "Optional dependency 'wmi' is not installed; "
            "WMI temperature fallbacks will be skipped"
        )
        _WMI_MODULE = None
    except Exception as e:
        logging.debug(f"Failed to import optional 'wmi' module: {e}")
        _WMI_MODULE = None

    return _WMI_MODULE


# ============================================================================
# HELPER: PowerShell JSON Execution
# ============================================================================

def _run_powershell_json(script: str, timeout: int = 15) -> Optional[Any]:
    """
    Execute PowerShell script and parse JSON output.
    
    Args:
        script: PowerShell script that outputs JSON
        timeout: Maximum execution time in seconds
        
    Returns:
        Parsed JSON object or None if failed
    """
    try:
        completed = subprocess.run(
            [_POWERSHELL_EXE, "-NoLogo", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if completed.returncode != 0:
            error_msg = completed.stderr.strip() or "PowerShell execution failed"
            logging.debug(f"PowerShell error: {error_msg}")
            return None
        
        output = completed.stdout.strip()
        if not output or output == "null":
            return None
            
        return json.loads(output)
    except subprocess.TimeoutExpired:
        logging.warning(f"PowerShell script timed out after {timeout}s")
        return None
    except json.JSONDecodeError as e:
        logging.warning(f"Failed to parse PowerShell JSON output: {e}")
        return None
    except Exception as e:
        logging.debug(f"PowerShell execution exception: {e}")
        return None


# ============================================================================
# 1. EVENT VIEWER PARSING
# ============================================================================

def collect_event_viewer_summary(days: int = 7) -> Dict[str, Any]:
    """
    Analyze Windows Event Log for critical/error events in the last N days.
    
    Returns:
        dict with status, total_events, top_sources, latest_critical
    """
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        ps_script = rf"""
        $ErrorActionPreference = 'SilentlyContinue'
        $start = Get-Date "{start_date}"

        # Get Critical (Level 1) and Error (Level 2) from System and Application logs
        $events = Get-WinEvent -FilterHashtable @{{
            LogName='System','Application'
            Level=1,2
            StartTime=$start
        }} -ErrorAction SilentlyContinue

        if ($events) {{
            $grouped = $events | Group-Object -Property ProviderName |
                Sort-Object Count -Descending |
                Select-Object -First 8

            $sources = @()
            foreach ($g in $grouped) {{
                $recent = @()
                $g.Group | Select-Object -First 3 | ForEach-Object {{
                    $evt = @{{
                        time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm')
                        id = $_.Id
                        level = if ($_.Level -eq 1) {{ 'Critical' }} else {{ 'Error' }}
                    }}

                    # Application Error (1000) — extract faulting app and module
                    if ($_.Id -eq 1000 -and $_.ProviderName -eq 'Application Error') {{
                        try {{
                            if ($_.Properties.Count -ge 7) {{
                                $evt.app = [string]$_.Properties[0].Value
                                $evt.app_ver = [string]$_.Properties[1].Value
                                $evt.module = [string]$_.Properties[3].Value
                                $evt.exception = '0x{{0:X8}}' -f [int64]$_.Properties[6].Value
                            }}
                        }} catch {{}}
                    }}
                    # Application Hang (1002) — extract hung app
                    elseif ($_.Id -eq 1002 -and $_.ProviderName -eq 'Application Hang') {{
                        try {{
                            if ($_.Properties.Count -ge 1) {{
                                $evt.app = [string]$_.Properties[0].Value
                            }}
                        }} catch {{}}
                    }}
                    # Kernel-Power 41 — unexpected shutdown
                    elseif ($_.Id -eq 41 -and $_.ProviderName -like '*Kernel-Power*') {{
                        $evt.description = 'Unexpected shutdown'
                        try {{
                            if ($_.Properties.Count -ge 1) {{
                                $bc = [int64]$_.Properties[0].Value
                                if ($bc -ne 0) {{ $evt.bugcheck = '0x{{0:X}}' -f $bc }}
                            }}
                        }} catch {{}}
                    }}
                    # WHEA-Logger — hardware error type
                    elseif ($_.ProviderName -like '*WHEA*') {{
                        try {{
                            if ($_.Message) {{
                                $first = ($_.Message -split "`n")[0].Trim()
                                if ($first.Length -gt 150) {{ $first = $first.Substring(0, 150) + '...' }}
                                $evt.description = $first
                            }}
                        }} catch {{}}
                    }}
                    # Everything else — first line of message
                    else {{
                        if ($_.Message) {{
                            $first = ($_.Message -split "`n")[0].Trim()
                            if ($first.Length -gt 150) {{ $first = $first.Substring(0, 150) + '...' }}
                            $evt.description = $first
                        }}
                    }}

                    $recent += $evt
                }}

                $sources += @{{
                    name = $g.Name
                    count = $g.Count
                    recent = $recent
                }}
            }}

            # Latest critical event
            $latest = $events | Where-Object {{ $_.Level -eq 1 }} | Select-Object -First 1
            $latestObj = $null
            if ($latest) {{
                $msg = ''
                if ($latest.Message) {{
                    $msg = $latest.Message.Substring(0, [Math]::Min(200, $latest.Message.Length))
                }}
                $latestObj = @{{
                    time = $latest.TimeCreated.ToString('yyyy-MM-dd HH:mm')
                    source = $latest.ProviderName
                    message = $msg
                }}
            }}

            @{{
                total_count = $events.Count
                sources = $sources
                latest_critical = $latestObj
            }} | ConvertTo-Json -Depth 4
        }} else {{
            @{{ total_count = 0; sources = @(); latest_critical = $null }} | ConvertTo-Json
        }}
        """

        result = _run_powershell_json(ps_script, timeout=25)

        if not result:
            return {
                "status": "unavailable",
                "reason": "Event log query failed or timed out"
            }

        total_count = result.get("total_count", 0)
        sources_raw = result.get("sources", [])
        latest_critical = result.get("latest_critical")

        # Normalize sources (could be single dict or list)
        if isinstance(sources_raw, dict):
            sources_raw = [sources_raw]
        else:
            sources_raw = sources_raw or []

        # Format sources — keep name/count for backward compat, add recent events
        top_sources = []
        for s in sources_raw:
            source = {
                "name": s.get("name") or s.get("Name", "Unknown"),
                "count": s.get("count") or s.get("Count", 0),
            }
            # Normalize recent events list
            recent = s.get("recent", [])
            if isinstance(recent, dict):
                recent = [recent]
            source["recent"] = recent or []
            top_sources.append(source)

        # Format latest_critical for backward compat with report_formatter
        latest_formatted = None
        if latest_critical:
            latest_formatted = {
                "timestamp": latest_critical.get("time"),
                "source": latest_critical.get("source"),
                "message": latest_critical.get("message", "")[:200],
            }

        driver_titles = (available_updates or {}).get("DriverTitles", []) or []
        optional_titles = (available_updates or {}).get("OptionalTitles", []) or []
        if isinstance(driver_titles, str):
            driver_titles = [driver_titles]
        if isinstance(optional_titles, str):
            optional_titles = [optional_titles]

        return {
            "status": "ok",
            "days_lookback": days,
            "total_events": int(total_count),
            "top_sources": top_sources,
            "latest_critical": latest_formatted,
        }

    except Exception as e:
        logging.warning(f"Event Viewer collection failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


# ============================================================================
# 2. WINDOWS UPDATE HEALTH
# ============================================================================

def collect_windows_update_health() -> Dict[str, Any]:
    """
    Check Windows Update status: last update, failed updates, pending reboot.
    
    Returns:
        dict with status, last_update, failed_count, pending_reboot
    """
    try:
        # 1. Last installed update
        ps_last_update = r"""
        $ErrorActionPreference = 'SilentlyContinue'
        $latest = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1
        if ($latest) {
            @{
                HotFixID = $latest.HotFixID
                Description = $latest.Description
                InstalledOn = $latest.InstalledOn.ToString('yyyy-MM-dd')
            } | ConvertTo-Json
        } else {
            $null | ConvertTo-Json
        }
        """
        
        last_update = _run_powershell_json(ps_last_update)
        
        # 2. Failed updates in last 30 days
        ps_failed_updates = r"""
        $ErrorActionPreference = 'SilentlyContinue'
        $start = (Get-Date).AddDays(-30)
        $failures = Get-WinEvent -FilterHashtable @{
            LogName='System'
            ProviderName='Microsoft-Windows-WindowsUpdateClient'
            Level=2
            StartTime=$start
        } -ErrorAction SilentlyContinue
        
        @{ Count = if($failures) { $failures.Count } else { 0 } } | ConvertTo-Json
        """
        
        failures = _run_powershell_json(ps_failed_updates)
        failed_count = failures.get("Count", 0) if failures else 0
        
        # 3. Pending reboot check
        ps_pending_reboot = r"""
        $ErrorActionPreference = 'SilentlyContinue'
        $pending = $false
        
        # Check common reboot-pending registry keys
        $keys = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
        )
        
        foreach($key in $keys) {
            if(Test-Path $key) {
                $pending = $true
                break
            }
        }
        
        # Check PendingFileRenameOperations
        $pfr = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
        if($pfr -and $pfr.PendingFileRenameOperations) {
            $pending = $true
        }
        
        @{ PendingReboot = $pending } | ConvertTo-Json
        """
        
        reboot_check = _run_powershell_json(ps_pending_reboot)
        pending_reboot = reboot_check.get("PendingReboot", False) if reboot_check else False
        
        # 4. Available optional / driver updates
        ps_available_updates = r"""
        $ErrorActionPreference = 'SilentlyContinue'
        try {
            $session = New-Object -ComObject Microsoft.Update.Session
            $searcher = $session.CreateUpdateSearcher()
            $result = $searcher.Search("IsInstalled=0 and IsHidden=0")
            $updates = @($result.Updates)

            $driverTitles = @()
            $optionalTitles = @()
            foreach ($update in $updates) {
                $isDriver = $false
                foreach ($category in $update.Categories) {
                    if ($category.Name -eq 'Drivers') {
                        $isDriver = $true
                        break
                    }
                }

                $browseOnly = $false
                try { $browseOnly = [bool]$update.BrowseOnly } catch {}

                if ($isDriver) {
                    $driverTitles += $update.Title
                } elseif ($browseOnly) {
                    $optionalTitles += $update.Title
                }
            }

            @{
                AvailableCount = $updates.Count
                DriverCount = $driverTitles.Count
                OptionalCount = $optionalTitles.Count
                DriverTitles = @($driverTitles | Select-Object -First 5)
                OptionalTitles = @($optionalTitles | Select-Object -First 5)
            } | ConvertTo-Json -Depth 3
        } catch {
            $null | ConvertTo-Json
        }
        """

        available_updates = _run_powershell_json(ps_available_updates, timeout=35)

        return {
            "status": "ok",
            "last_update": {
                "hotfix_id": last_update.get("HotFixID", "Unknown"),
                "description": last_update.get("Description", "Unknown"),
                "installed_on": last_update.get("InstalledOn", "Unknown")
            } if last_update else None,
            "failed_updates_last_30_days": int(failed_count),
            "pending_reboot": pending_reboot,
            "available_updates_count": int((available_updates or {}).get("AvailableCount", 0) or 0),
            "driver_updates_count": int((available_updates or {}).get("DriverCount", 0) or 0),
            "optional_updates_count": int((available_updates or {}).get("OptionalCount", 0) or 0),
            "driver_update_titles": driver_titles,
            "optional_update_titles": optional_titles,
        }
        
    except Exception as e:
        logging.warning(f"Windows Update health check failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


# ============================================================================
# 3. MICROSOFT DEFENDER STATUS
# ============================================================================

def collect_defender_status() -> Dict[str, Any]:
    """
    Check Microsoft Defender real-time protection, signature age, scan history.
    
    Returns:
        dict with status, realtime_enabled, signature_age, last_scan
    """
    try:
        ps_defender = r"""
        $ErrorActionPreference = 'SilentlyContinue'
        
        if (-not (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue)) {
            @{ Available = $false } | ConvertTo-Json
        } else {
            $status = Get-MpComputerStatus
            
            @{
                Available = $true
                RealTimeProtectionEnabled = $status.RealTimeProtectionEnabled
                AntispywareEnabled = $status.AntispywareEnabled
                AMServiceEnabled = $status.AMServiceEnabled
                AntivirusSignatureLastUpdated = $status.AntivirusSignatureLastUpdated.ToString('yyyy-MM-dd HH:mm')
                LastFullScanTime = if($status.LastFullScanTime) { $status.LastFullScanTime.ToString('yyyy-MM-dd HH:mm') } else { $null }
                LastQuickScanTime = if($status.LastQuickScanTime) { $status.LastQuickScanTime.ToString('yyyy-MM-dd HH:mm') } else { $null }
                FullScanAge = $status.FullScanAge
                QuickScanAge = $status.QuickScanAge
            } | ConvertTo-Json
        }
        """
        
        status = _run_powershell_json(ps_defender)
        
        if not status or not status.get("Available"):
            return {
                "status": "unavailable",
                "reason": "Defender cmdlets not available (may be disabled or third-party AV)"
            }
        
        # Calculate signature age
        sig_date_str = status.get("AntivirusSignatureLastUpdated")
        sig_age_days = None
        if sig_date_str:
            try:
                sig_date = datetime.strptime(sig_date_str, "%Y-%m-%d %H:%M")
                sig_age_days = (datetime.now() - sig_date).days
            except:
                pass
        
        return {
            "status": "ok",
            "realtime_enabled": status.get("RealTimeProtectionEnabled", False),
            "antispyware_enabled": status.get("AntispywareEnabled", False),
            "amservice_enabled": status.get("AMServiceEnabled", False),
            "signature_last_updated": sig_date_str,
            "signature_age_days": sig_age_days,
            "last_full_scan": status.get("LastFullScanTime"),
            "last_quick_scan": status.get("LastQuickScanTime"),
            "full_scan_age_days": status.get("FullScanAge"),
            "quick_scan_age_days": status.get("QuickScanAge")
        }
        
    except Exception as e:
        logging.warning(f"Defender status check failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


# ============================================================================
# 4. TEMPERATURE MONITORING
# ============================================================================

def _collect_gpu_temp_nvml() -> Optional[Dict[str, Any]]:
    """Try to get GPU temperature via NVML (NVIDIA only). Auto-installs pynvml if missing."""
    def _try_nvml():
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode('utf-8')
        pynvml.nvmlShutdown()
        return {
            "name": name,
            "temp_c": temp,
            "sensor": "GPU Core",
            "source": "NVML",
        }

    try:
        return _try_nvml()
    except ImportError:
        if not _can_auto_install_vendor_deps():
            logging.debug("Skipping pynvml auto-install in frozen build")
            return None
        # Auto-install pynvml to vendor/ (one-time setup)
        try:
            import subprocess
            from settings import get_app_dir
            vendor_dir = os.path.join(get_app_dir(), 'vendor')
            os.makedirs(vendor_dir, exist_ok=True)
            result = subprocess.run(
                [_sys.executable, '-m', 'pip', 'install', 'pynvml',
                 '--target', vendor_dir, '--quiet'],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                if vendor_dir not in _sys.path:
                    _sys.path.insert(0, vendor_dir)
                return _try_nvml()
        except Exception as install_err:
            logging.debug(f"pynvml auto-install failed: {install_err}")
        return None
    except Exception as e:
        logging.debug(f"NVML GPU temp unavailable: {e}")
        return None


def _collect_gpu_temp_amd() -> Optional[Dict[str, Any]]:
    """Try to get AMD GPU temperature via WMI

    AMD GPUs expose temperature sensors through WMI on Windows.
    This function queries various WMI namespaces to find AMD GPU temperature.
    """
    try:
        wmi = _import_wmi()
        if not wmi:
            return None

        # Try AMD-specific WMI namespace first
        try:
            c = wmi.WMI(namespace="root\\wmi")
            # AMD GPUs expose temperature via AMDGPU_Sensors or similar classes
            # Query for temperature sensors
            sensors = c.query("SELECT * FROM AMDGPU_Temperature")
            if sensors:
                for sensor in sensors:
                    try:
                        # AMD temps are typically in Celsius * 1000
                        temp_raw = sensor.CurrentTemperature
                        if temp_raw:
                            temp_c = temp_raw / 1000.0
                            # Sanity check (valid GPU temps: 20-110°C)
                            if 20 <= temp_c <= 120:
                                return {
                                    "name": "AMD GPU",
                                    "temp_c": int(temp_c),
                                    "sensor": "AMDGPU_Temperature",
                                    "source": "WMI",
                                }
                    except Exception:
                        pass
        except Exception as e:
            logging.debug(f"AMD WMI namespace query failed: {e}")

        # Fallback: Try OpenHardwareMonitor/LibreHardwareMonitor WMI interface
        # These tools expose GPU temps in a standardized way
        try:
            c = wmi.WMI(namespace="root\\OpenHardwareMonitor")
            sensors = c.Sensor()
            for sensor in sensors:
                try:
                    # Look for GPU temperature sensors
                    if hasattr(sensor, 'SensorType') and sensor.SensorType == 'Temperature':
                        if hasattr(sensor, 'Name') and 'GPU' in str(sensor.Name).upper():
                            if 'AMD' in str(sensor.Name).upper() or 'RADEON' in str(sensor.Name).upper():
                                temp_c = float(sensor.Value)
                                if 20 <= temp_c <= 120:
                                    gpu_name = str(sensor.Parent) if hasattr(sensor, 'Parent') else "AMD GPU"
                                    return {
                                        "name": gpu_name,
                                        "temp_c": int(temp_c),
                                        "sensor": str(sensor.Name),
                                        "source": "OpenHardwareMonitor WMI",
                                    }
                except Exception:
                    pass
        except Exception as e:
            logging.debug(f"OpenHardwareMonitor WMI query failed: {e}")

        # Alternative: Try reading from registry or AMD OverDrive API
        # This is a last resort and may not work on all systems
        try:
            import winreg
            # AMD stores some GPU info in registry
            key_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0000"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                try:
                    driver_desc = winreg.QueryValueEx(key, "DriverDesc")[0]
                    if "AMD" in driver_desc.upper() or "RADEON" in driver_desc.upper():
                        # If we detect AMD GPU but can't get temp, at least log it
                        logging.debug(f"AMD GPU detected ({driver_desc}) but temperature unavailable via WMI")
                except Exception:
                    pass
        except Exception as e:
            logging.debug(f"AMD registry query failed: {e}")

        logging.debug("AMD GPU temperature not available via any method")
        return None

    except Exception as e:
        logging.debug(f"AMD GPU temp collection failed: {e}")
        return None


def _collect_cpu_temp_info() -> Optional[Dict[str, Any]]:
    """
    Get CPU package temperature.
    Tries in order:
      1. LibreHardwareMonitor HTTP (port 8085) — most accurate
      2. MSAcpi_ThermalZoneTemperature WMI — available on most systems even without LHM
      3. WMI Win32_TemperatureProbe — last resort
    """
    # ── 1. LHM HTTP ──────────────────────────────────────────────────
    try:
        import urllib.request, json
        url = "http://localhost:8085/data.json"
        req = urllib.request.Request(url, headers={"User-Agent": "PCAutoSpec"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        def walk(node, results):
            name = node.get('Text', '')
            value = node.get('Value', '')
            if ('CPU Package' in name or 'CPU Tdie' in name or 'CPU (Tctl/Tdie)' in name):
                try:
                    temp = float(value.replace('°C', '').replace(',', '.').strip())
                    if 20 <= temp <= 120:
                        results.append({
                            'temp_c': round(temp, 1),
                            'sensor': name.strip(),
                            'source': 'LibreHardwareMonitor',
                        })
                except (ValueError, AttributeError):
                    pass
            for child in node.get('Children', []):
                walk(child, results)

        temps = []
        for child in data.get('Children', []):
            walk(child, temps)

        if temps:
            logging.debug(f"CPU temp via LHM HTTP: {temps[0]['temp_c']:.1f}°C ({temps[0]['sensor']})")
            return temps[0]
    except Exception as e:
        logging.debug(f"LHM HTTP temp unavailable: {e}")

    # ── 2. MSAcpi_ThermalZoneTemperature (WMI) ───────────────────────
    # Available on most systems without any extra software.
    # Returns temp in tenths of Kelvin — convert to Celsius.
    try:
        _wmi = _import_wmi()
        if not _wmi:
            return None
        c = _wmi.WMI(namespace="root\\wmi")
        zones = c.MSAcpi_ThermalZoneTemperature()
        readings = []
        for z in zones:
            try:
                kelvin_tenths = z.CurrentTemperature
                if kelvin_tenths:
                    celsius = (kelvin_tenths / 10.0) - 273.15
                    if 20 <= celsius <= 120:
                        zone_name = getattr(z, 'InstanceName', None) or 'ACPI Thermal Zone'
                        readings.append((celsius, str(zone_name)))
            except Exception:
                pass
        if readings:
            avg = sum(temp for temp, _ in readings) / len(readings)
            zone_names = []
            for _, zone_name in readings:
                if zone_name not in zone_names:
                    zone_names.append(zone_name)
            sensor_name = zone_names[0] if len(zone_names) == 1 else f"ACPI Thermal Zones ({len(zone_names)})"
            logging.debug(f"CPU temp via MSAcpi thermal zone: {avg:.1f}°C ({len(readings)} zones)")
            return {
                'temp_c': round(avg, 1),
                'sensor': sensor_name,
                'source': 'MSAcpi_ThermalZoneTemperature',
            }
    except Exception as e:
        logging.debug(f"MSAcpi thermal zone unavailable: {e}")

    # ── 3. Win32_TemperatureProbe (WMI) ──────────────────────────────
    try:
        _wmi = _import_wmi()
        if not _wmi:
            return None
        c = _wmi.WMI()
        probes = c.Win32_TemperatureProbe()
        readings = []
        for p in probes:
            try:
                val = p.CurrentReading
                if val:
                    celsius = val / 10.0
                    if 20 <= celsius <= 120:
                        probe_name = getattr(p, 'Name', None) or 'Win32 Temperature Probe'
                        readings.append((celsius, str(probe_name)))
            except Exception:
                pass
        if readings:
            avg = sum(temp for temp, _ in readings) / len(readings)
            probe_names = []
            for _, probe_name in readings:
                if probe_name not in probe_names:
                    probe_names.append(probe_name)
            sensor_name = probe_names[0] if len(probe_names) == 1 else f"Win32 Temperature Probes ({len(probe_names)})"
            logging.debug(f"CPU temp via Win32_TemperatureProbe: {avg:.1f}°C")
            return {
                'temp_c': round(avg, 1),
                'sensor': sensor_name,
                'source': 'Win32_TemperatureProbe',
            }
    except Exception as e:
        logging.debug(f"Win32_TemperatureProbe unavailable: {e}")

    logging.debug("All CPU temperature methods failed")
    return None


def _collect_cpu_temp_lhm() -> Optional[float]:
    """Backward-compatible float-only CPU temp helper."""
    temp_info = _collect_cpu_temp_info()
    if temp_info:
        return temp_info.get('temp_c')
    return None


# Keep old name as alias so nothing else breaks
_collect_cpu_temp_wmi = _collect_cpu_temp_lhm


def _collect_memory_temp_lhm() -> Optional[float]:
    """
    Get RAM/memory temperature from LHM if available.
    DDR5 modules expose onboard thermal sensors; DDR4 usually does not.
    Returns the highest module temp found, or None if unavailable.
    """
    try:
        import urllib.request, json
        req = urllib.request.Request(
            "http://localhost:8085/data.json",
            headers={"User-Agent": "PCAutoSpec"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        def walk(node, results, parent_name=""):
            name = node.get('Text', '')
            value = node.get('Value', '')
            if ('Temperature' in name and
                    any(k in parent_name for k in ('Memory', 'RAM', 'IMC', 'DIMM'))):
                try:
                    temp = float(value.replace('°C', '').replace(',', '.').strip())
                    if 20 <= temp <= 100:
                        results.append(temp)
                except (ValueError, AttributeError):
                    pass
            for child in node.get('Children', []):
                walk(child, results, name)

        temps = []
        for child in data.get('Children', []):
            walk(child, temps)

        if temps:
            return round(max(temps), 1)

    except Exception as e:
        logging.debug(f"Memory temp via LHM unavailable: {e}")

    return None


def _collect_gpu_temp_lhm() -> Optional[Dict[str, Any]]:
    """
    Get GPU temperature from LHM HTTP - works for both NVIDIA and AMD.
    Returns dict with name and temp_c, or None.
    """
    try:
        import urllib.request, json
        req = urllib.request.Request(
            "http://localhost:8085/data.json",
            headers={"User-Agent": "PCAutoSpec"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        def walk(node, results, gpu_name=""):
            name = node.get('Text', '')
            value = node.get('Value', '')
            img = node.get('ImageURL', '')
            if 'nvidia' in img or 'amd' in img:
                gpu_name = name
            if 'GPU Core' in name or 'GPU Temperature' in name:
                try:
                    temp = float(value.replace('°C', '').replace(',', '.').strip())
                    if 20 <= temp <= 120:
                        results.append({
                            'name': gpu_name or 'GPU',
                            'temp_c': round(temp, 1),
                            'sensor': name.strip(),
                            'source': 'LibreHardwareMonitor',
                        })
                except (ValueError, AttributeError):
                    pass
            for child in node.get('Children', []):
                walk(child, results, gpu_name)

        results = []
        for child in data.get('Children', []):
            walk(child, results)

        if results:
            return results[0]

    except Exception as e:
        logging.debug(f"GPU temp via LHM unavailable: {e}")

    return None



def collect_temperatures() -> Dict[str, Any]:
    """
    Collect system temperatures (CPU and GPU).
    Best-effort approach - fails gracefully if sensors unavailable.

    Enhanced with AMD GPU support:
    - Tries NVIDIA GPU temp via NVML first
    - Falls back to AMD GPU temp via WMI if NVIDIA fails
    - Collects CPU temp via WMI thermal zones

    Returns:
        dict with status, cpu_temp_c, gpu data
    """
    try:
        # Wait for LHM to settle and CPU to reach true idle temp.
        # Without this, the first reading may catch residual heat from
        # system startup activity and read artificially high.
        import time
        time.sleep(5)

        cpu_temp_info = _collect_cpu_temp_info()
        cpu_temp = cpu_temp_info.get('temp_c') if cpu_temp_info else None

        # Try NVIDIA GPU first
        gpu_data = _collect_gpu_temp_nvml()

        # If NVIDIA failed, try AMD GPU
        if gpu_data is None:
            logging.debug("NVIDIA GPU temp unavailable, trying AMD...")
            gpu_data = _collect_gpu_temp_amd()

        if cpu_temp is None and gpu_data is None:
            return {
                "status": "unavailable",
                "reason": "No temperature sensors accessible (WMI thermal zones not exposed, GPU temp unavailable)"
            }

        return {
            "status": "ok",
            "cpu_temp_c": round(cpu_temp, 1) if cpu_temp else None,
            "cpu_sensor": cpu_temp_info.get('sensor') if cpu_temp_info else None,
            "cpu_sensor_source": cpu_temp_info.get('source') if cpu_temp_info else None,
            "gpu": gpu_data
        }

    except Exception as e:
        logging.warning(f"Temperature collection failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


def _cpu_stress_worker(load_queue):
    """
    Module-level CPU stress worker — must be at module level for multiprocessing on Windows.
    Reads current load_fraction (0.0-1.0) from a multiprocessing Queue.
    The main process pushes updated fractions as the ramp progresses.
    """
    import time
    load_fraction = 0.2  # start gentle
    while True:
        # Check for updated load fraction (non-blocking)
        try:
            while not load_queue.empty():
                load_fraction = load_queue.get_nowait()
        except Exception:
            pass

        if load_fraction >= 1.0:
            # Full load — no sleep
            x = 0.0
            for i in range(1, 100000):
                x += (i ** 0.5) / i
        else:
            # Duty cycle: work for load_fraction of a 50ms window, sleep the rest
            window = 0.05
            work = window * load_fraction
            rest = window * (1.0 - load_fraction)
            t = time.monotonic()
            while time.monotonic() - t < work:
                x = 0.0
                for i in range(1, 1000):
                    x += (i ** 0.5) / i
            if rest > 0:
                time.sleep(rest)


def collect_gpu_temp_under_load(
        duration_sec: int = 20,
        thermal_limit_c: float = 100.0,
        ramp_sec: int = 15,
        log_callback=None,
        temp_callback=None,
        finished_callback=None) -> Dict[str, Any]:
    """
    Stress the GPU using OpenCL (works on both NVIDIA and AMD).
    Requires pyopencl to be installed in vendor/.

    - Ramps GPU load gently over ramp_sec seconds
    - Records peak temperature via LHM HTTP
    - Aborts if temp reaches thermal_limit_c

    Returns dict with status, peak_temp_c, aborted, samples[]
    """
    import time

    def _log(msg):
        logging.info(msg)
        if log_callback:
            log_callback(msg)

    try:
        import pyopencl as cl
        import numpy as np
    except ImportError:
        if not _can_auto_install_vendor_deps():
            _log("  pyopencl not available in packaged build — skipping GPU stress install attempt\n")
            return {"status": "unavailable", "reason": "pyopencl not available in packaged build"}
        _log("  pyopencl not found — installing now (one-time setup)...\n")
        try:
            import subprocess, sys
            from settings import get_app_dir
            vendor_dir = os.path.join(get_app_dir(), 'vendor')
            os.makedirs(vendor_dir, exist_ok=True)
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', 'pyopencl', 'numpy',
                 '--target', vendor_dir, '--quiet'],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                _log("  pyopencl installed successfully — retrying GPU stress...\n")
                import importlib
                import pyopencl as cl
                import numpy as np
            else:
                _log(f"  pyopencl install failed: {result.stderr.strip()}\n")
                return {"status": "unavailable", "reason": "pyopencl install failed"}
        except Exception as e:
            _log(f"  pyopencl auto-install failed: {e}\n")
            return {"status": "unavailable", "reason": f"pyopencl not available: {e}"}

    # ── Set up OpenCL context on best GPU device ───────────────────
    try:
        platforms = cl.get_platforms()
        gpu_device = None
        for platform in platforms:
            devices = platform.get_devices(cl.device_type.GPU)
            if devices:
                gpu_device = devices[0]
                break

        if gpu_device is None:
            _log("  GPU stress skipped — no OpenCL GPU device found\n")
            return {"status": "unavailable", "reason": "No OpenCL GPU device found"}

        gpu_name = gpu_device.name.strip()
        _log(f"  GPU stress: {gpu_name}\n")

        ctx = cl.Context([gpu_device])
        queue = cl.CommandQueue(ctx)

        # Heavy matrix multiply kernel — hammers GPU shader units
        kernel_src = """
        __kernel void matrix_stress(
            __global const float* A,
            __global const float* B,
            __global float* C,
            const int N)
        {
            int row = get_global_id(0);
            int col = get_global_id(1);
            if (row < N && col < N) {
                float sum = 0.0f;
                for (int k = 0; k < N; k++) {
                    sum += A[row * N + k] * B[k * N + col];
                }
                C[row * N + col] = sum;
            }
        }
        """
        program = cl.Program(ctx, kernel_src).build()

        # Matrix size — larger = more GPU load
        N = 512
        mf = cl.mem_flags
        A = np.random.rand(N, N).astype(np.float32)
        B = np.random.rand(N, N).astype(np.float32)
        C = np.zeros((N, N), dtype=np.float32)
        A_buf = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=A)
        B_buf = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=B)
        C_buf = cl.Buffer(ctx, mf.WRITE_ONLY, C.nbytes)

    except Exception as e:
        _log(f"  GPU stress setup failed: {e}\n")
        return {"status": "unavailable", "reason": str(e)}

    samples = []
    aborted = False
    abort_reason = None
    last_sensor = None

    try:
        # ── Ramp phase ─────────────────────────────────────────────
        _log(f"  Ramping GPU load over {ramp_sec}s...\n")
        ramp_start = time.monotonic()
        while time.monotonic() - ramp_start < ramp_sec:
            elapsed = time.monotonic() - ramp_start
            # Ramp: 1 kernel call at start, up to 8 at full ramp
            iterations = max(1, int(8 * elapsed / ramp_sec))
            for _ in range(iterations):
                program.matrix_stress(
                    queue, (N, N), None,
                    A_buf, B_buf, C_buf, np.int32(N))
            queue.finish()

            temp_data = _collect_gpu_temp_lhm()
            temp = temp_data['temp_c'] if temp_data else None
            if temp:
                last_sensor = temp_data.get('sensor') or last_sensor
                pct = int(100 * elapsed / ramp_sec)
                _log(f"  GPU ramp [{int(elapsed)}s / {ramp_sec}s] {pct}% — {temp:.0f}\u00b0C\n")
                if temp_callback:
                    try: temp_callback(temp)
                    except Exception: pass
                if temp >= thermal_limit_c:
                    abort_reason = f"GPU thermal limit during ramp: {temp:.0f}\u00b0C"
                    _log(f"  \u26a0 {abort_reason} — aborting\n")
                    return {"status": "ok", "gpu_name": gpu_name,
                            "peak_temp_c": round(temp, 1), "sensor": last_sensor, "aborted": True,
                            "abort_reason": abort_reason, "samples": [round(temp, 1)],
                            "duration_sec": round(time.monotonic() - ramp_start, 1)}

        # ── Full load measurement phase ─────────────────────────────
        _log(f"  GPU full load — measuring peak temperature...\n")
        start = time.monotonic()
        while time.monotonic() - start < duration_sec:
            # Keep GPU pegged
            for _ in range(8):
                program.matrix_stress(
                    queue, (N, N), None,
                    A_buf, B_buf, C_buf, np.int32(N))
            queue.finish()
            time.sleep(1)

            temp_data = _collect_gpu_temp_lhm()
            temp = temp_data['temp_c'] if temp_data else None
            if temp:
                last_sensor = temp_data.get('sensor') or last_sensor
                elapsed = int(time.monotonic() - start)
                samples.append(round(temp, 1))
                _log(f"  GPU load temp [{elapsed}s]: {temp:.0f}\u00b0C\n")
                if temp_callback:
                    try: temp_callback(temp)
                    except Exception: pass
                if temp >= thermal_limit_c:
                    aborted = True
                    abort_reason = f"GPU thermal limit: {temp:.0f}\u00b0C >= {thermal_limit_c:.0f}\u00b0C"
                    _log(f"  \u26a0 {abort_reason} — aborting\n")
                    break

        peak = max(samples) if samples else None
        return {
            "status": "ok",
            "gpu_name": gpu_name,
            "peak_temp_c": peak,
            "sensor": last_sensor,
            "aborted": aborted,
            "abort_reason": abort_reason,
            "samples": samples,
            "duration_sec": round(time.monotonic() - start, 1),
        }

    except Exception as e:
        logging.warning(f"GPU stress test failed: {e}")
        return {"status": "error", "reason": str(e)}

    finally:
        if finished_callback:
            try: finished_callback()
            except Exception: pass


def collect_cpu_temp_under_load(
        duration_sec: int = 20,
        thermal_limit_c: float = 100.0,
        ramp_sec: int = 60,
        log_callback=None,
        started_callback=None,
        temp_callback=None,
        finished_callback=None,
        cancel_requested_callback=None) -> Dict[str, Any]:
    """
    Stress all CPU cores for `duration_sec` seconds and record peak temperature.

    - Ramps load gradually over ramp_sec to let fans spin up before full load
    - Spawns one worker process per logical CPU core via multiprocessing
    - Samples CPU temp every second via LHM (or MSAcpi fallback)
    - Aborts immediately if temp reaches thermal_limit_c
    - Workers are always cleaned up, even on abort or exception

    Returns:
        dict with status, peak_temp_c, aborted (bool), samples[]
    """
    import multiprocessing
    import time

    def _log(msg):
        logging.info(msg)
        if log_callback:
            log_callback(msg)

    def _cancel_requested() -> bool:
        if not cancel_requested_callback:
            return False
        try:
            return bool(cancel_requested_callback())
        except Exception:
            return False

    workers = []
    load_queues = []
    try:
        core_count = multiprocessing.cpu_count()
        _log(f"  Starting CPU stress ({core_count} cores, {ramp_sec}s ramp + {duration_sec}s test)...\n")

        # ── Spawn all workers at 20% duty cycle ───────────────────
        # All cores are active from the start but generating minimal heat.
        # We ramp duty cycle from 20% → 100% over ramp_sec seconds so
        # fans can spin up gradually before full load hits.
        _log(f"  Ramping up load gently (fans spinning up)...\n")
        for _ in range(core_count):
            q = multiprocessing.Queue()
            q.put(0.2)  # start at 20% load
            p = multiprocessing.Process(target=_cpu_stress_worker, args=(q,), daemon=True)
            p.start()
            workers.append(p)
            load_queues.append(q)

        # Notify GUI to show dialog
        if started_callback:
            try:
                started_callback()
            except Exception:
                pass

        if _cancel_requested():
            _log("  CPU stress test cancelled before ramp began\n")
            return {
                "status": "cancelled",
                "aborted": True,
                "abort_reason": "Cancelled by tech",
                "samples": [],
                "duration_sec": 0,
            }

        # ── Ramp phase — increase duty cycle 20% → 100% ───────────
        # Update every second, stepping load up smoothly
        ramp_start = time.monotonic()
        ramp_step_sec = 1
        ramp_peak = 0.0  # track highest temp seen during ramp
        last_sensor = None
        while time.monotonic() - ramp_start < ramp_sec:
            if _cancel_requested():
                _log("  CPU stress test cancelled during ramp-up\n")
                return {
                    "status": "cancelled",
                    "peak_temp_c": round(ramp_peak, 1) if ramp_peak > 0 else None,
                    "sensor": last_sensor,
                    "aborted": True,
                    "abort_reason": "Cancelled by tech",
                    "samples": [],
                    "duration_sec": round(time.monotonic() - ramp_start, 1),
                }
            time.sleep(ramp_step_sec)
            elapsed = time.monotonic() - ramp_start
            # Linear ramp from 0.2 to 1.0
            fraction = 0.2 + (0.8 * min(elapsed / ramp_sec, 1.0))
            for q in load_queues:
                try:
                    q.put_nowait(fraction)
                except Exception:
                    pass
            temp_info = _collect_cpu_temp_info()
            temp = temp_info.get('temp_c') if temp_info else None
            if temp is not None:
                last_sensor = temp_info.get('sensor') or last_sensor
                ramp_peak = max(ramp_peak, temp)
                pct = int(fraction * 100)
                _log(f"  Ramp [{int(elapsed)}s / {ramp_sec}s] {pct}% load — {temp:.0f}°C\n")
                if temp_callback:
                    try:
                        temp_callback(temp)
                    except Exception:
                        pass
                if temp >= thermal_limit_c:
                    abort_reason = f"Thermal limit reached during ramp: {temp:.0f}°C"
                    _log(f"  ⚠ {abort_reason} — aborting\n")
                    return {
                        "status": "ok",
                        "peak_temp_c": round(temp, 1),
                        "sensor": last_sensor,
                        "aborted": True,
                        "abort_reason": abort_reason,
                        "samples": [round(temp, 1)],
                        "duration_sec": round(time.monotonic() - ramp_start, 1),
                    }

        if ramp_peak > 0:
            _log(f"  Ramp complete — peak during ramp: {ramp_peak:.0f}°C\n")

        # Push full load for measurement phase
        for q in load_queues:
            try:
                q.put_nowait(1.0)
            except Exception:
                pass

        # ── Full load measurement phase ────────────────────────────
        _log(f"  Full load — measuring peak temperature...\n")
        samples = []
        aborted = False
        abort_reason = None
        start = time.monotonic()

        while time.monotonic() - start < duration_sec:
            if _cancel_requested():
                _log("  CPU stress test cancelled during measurement phase\n")
                return {
                    "status": "cancelled",
                    "peak_temp_c": round(max(ramp_peak, max(samples) if samples else 0.0), 1)
                    if (ramp_peak > 0 or samples) else None,
                    "sensor": last_sensor,
                    "aborted": True,
                    "abort_reason": "Cancelled by tech",
                    "samples": samples,
                    "duration_sec": round(time.monotonic() - start, 1),
                }
            time.sleep(1)
            temp_info = _collect_cpu_temp_info()
            temp = temp_info.get('temp_c') if temp_info else None
            if temp is not None:
                last_sensor = temp_info.get('sensor') or last_sensor
                samples.append(round(temp, 1))
                elapsed = int(time.monotonic() - start)
                _log(f"  CPU load temp [{elapsed}s]: {temp:.0f}°C\n")
                if temp_callback:
                    try:
                        temp_callback(temp)
                    except Exception:
                        pass

                if temp >= thermal_limit_c:
                    aborted = True
                    abort_reason = (
                        f"Thermal limit reached: {temp:.0f}°C \u2265 {thermal_limit_c:.0f}°C"
                    )
                    _log(f"  ⚠ {abort_reason} — aborting stress test\n")
                    break

        # Use the highest temp seen across BOTH ramp and measurement phases
        # This catches thermal throttling — CPU may spike during ramp then cool
        # as throttling kicks in, making the measurement phase look deceptively cool
        measurement_peak = max(samples) if samples else 0.0
        peak = round(max(ramp_peak, measurement_peak), 1)
        throttling = ramp_peak > measurement_peak + 10  # 10°C+ drop = likely throttling
        if throttling:
            _log(f"  Note: Peak {peak:.0f}°C occurred during ramp (thermal throttling detected)\n")

        if aborted:
            return {
                "status": "ok",
                "peak_temp_c": peak,
                "sensor": last_sensor,
                "aborted": True,
                "abort_reason": abort_reason,
                "throttling_detected": throttling,
                "samples": samples,
                "duration_sec": round(time.monotonic() - start, 1),
            }

        return {
            "status": "ok",
            "peak_temp_c": peak,
            "sensor": last_sensor,
            "aborted": False,
            "throttling_detected": throttling,
            "samples": samples,
            "duration_sec": duration_sec,
        }

    except Exception as e:
        logging.warning(f"CPU load temp test failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e),
        }

    finally:
        # Always clean up workers
        for p in workers:
            try:
                if p.is_alive():
                    p.terminate()
                p.join(timeout=3)
                if p.is_alive():
                    p.kill()
            except Exception:
                pass
        # Close queues
        for q in load_queues:
            try:
                q.close()
            except Exception:
                pass
        # Notify GUI to close dialog
        if finished_callback:
            try:
                finished_callback()
            except Exception:
                pass
        _log("  CPU stress test complete\n")


# ============================================================================
# 5. STARTUP IMPACT ANALYSIS
# ============================================================================

def collect_startup_impact() -> Dict[str, Any]:
    """
    Analyze startup items (Run registry keys + Startup folders).
    
    Returns:
        dict with status, startup_item_count, items[]
    """
    try:
        ps_startup = r"""
        $ErrorActionPreference = 'SilentlyContinue'
        $items = @()
        
        # Check Run registry keys
        $runKeys = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
        )
        
        foreach($rk in $runKeys) {
            if(Test-Path $rk) {
                $props = Get-ItemProperty $rk -ErrorAction SilentlyContinue
                if($props) {
                    $props.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object {
                        $items += [PSCustomObject]@{
                            Source = $rk -replace 'HKLM:\\', 'HKLM:\' -replace 'HKCU:\\', 'HKCU:\'
                            Name   = $_.Name
                            Command = $_.Value
                        }
                    }
                }
            }
        }
        
        # Check Startup folders
        $startupFolders = @(
            "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup",
            "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp"
        )
        
        foreach($folder in $startupFolders) {
            if(Test-Path $folder) {
                Get-ChildItem $folder -ErrorAction SilentlyContinue | ForEach-Object {
                    $items += [PSCustomObject]@{
                        Source = $folder
                        Name   = $_.Name
                        Command = $_.FullName
                    }
                }
            }
        }
        
        $items | ConvertTo-Json
        """
        
        items = _run_powershell_json(ps_startup, timeout=10)
        
        if not items:
            items = []
        elif isinstance(items, dict):
            items = [items]
        
        # Extract just names for summary
        item_names = [item.get("Name", "Unknown") for item in items]
        
        return {
            "status": "ok",
            "startup_item_count": len(items),
            "items": items[:20],  # Don't flood report with hundreds of items
            "item_names": item_names[:10]  # Top 10 for quick summary
        }
        
    except Exception as e:
        logging.warning(f"Startup impact analysis failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


# ============================================================================
# 6. DISK SPEED TEST
# ============================================================================

def _mb_per_sec(byte_count: int, seconds: float) -> float:
    """Convert bytes and time to MB/s"""
    if seconds <= 0:
        return 0.0
    return (byte_count / (1024 * 1024)) / seconds


def collect_disk_speed_test(path: str = "C:\\", test_size_mb: int = 256) -> Dict[str, Any]:
    """
    Perform simple sequential read/write speed test.
    Non-destructive, uses temp file.
    
    Args:
        path: Drive path to test
        test_size_mb: Test file size in MB
        
    Returns:
        dict with status, write_mb_s, read_mb_s
    """
    test_file = None
    
    try:
        # Ensure path exists and is writable
        if not os.path.exists(path):
            return {
                "status": "unavailable",
                "reason": f"Path {path} does not exist"
            }
        
        # Create 1MB buffer
        buffer = os.urandom(1024 * 1024)
        
        # Write test
        with tempfile.NamedTemporaryFile(dir=path, delete=False, suffix=".tmp") as f:
            test_file = f.name
            write_start = time.perf_counter()
            
            for _ in range(test_size_mb):
                f.write(buffer)
            
            f.flush()
            os.fsync(f.fileno())
            write_time = time.perf_counter() - write_start
        
        # Read test
        read_bytes = 0
        read_start = time.perf_counter()
        
        with open(test_file, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
        
        read_time = time.perf_counter() - read_start
        
        # Calculate speeds
        write_speed = _mb_per_sec(test_size_mb * 1024 * 1024, write_time)
        read_speed = _mb_per_sec(read_bytes, read_time)

        # Windows file cache can make slow HDDs appear wildly fast on the read
        # pass immediately after the write. When the read result is implausibly
        # higher than the sustained write speed, prefer a conservative displayed
        # read speed so the UI/report does not mislabel the drive class.
        cached_read_likely = (
            write_speed > 0
            and write_speed < 350
            and read_speed > max(write_speed * 2.25, 450)
        )
        display_read_speed = read_speed
        if cached_read_likely:
            display_read_speed = min(read_speed, max(write_speed * 1.15, write_speed + 20))

        return {
            "status": "ok",
            "test_size_mb": test_size_mb,
            "write_mb_s": round(write_speed, 1),
            "read_mb_s": round(read_speed, 1),
            "display_write_mb_s": round(write_speed, 1),
            "display_read_mb_s": round(display_read_speed, 1),
            "cached_read_likely": cached_read_likely,
            "path": path,
            "write_time_sec": round(write_time, 2),
            "read_time_sec": round(read_time, 2)
        }
        
    except PermissionError:
        return {
            "status": "unavailable",
            "reason": f"Permission denied writing to {path} (run as Administrator)"
        }
    except Exception as e:
        logging.warning(f"Disk speed test failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }
    finally:
        # Cleanup
        if test_file and os.path.exists(test_file):
            try:
                os.remove(test_file)
            except Exception as e:
                logging.debug(f"Failed to remove test file: {e}")


# ============================================================================
# 7. DEVICE MANAGER & SYSTEM CONFIGURATION
# ============================================================================

def collect_device_manager_errors() -> Dict[str, Any]:
    """
    Check Windows Device Manager for hardware errors.

    Critical for diagnosing ~30% of hardware issues:
    - Driver conflicts
    - Missing drivers
    - Disabled devices
    - Hardware initialization failures

    Returns:
        dict with status, error_count, devices list
    """
    try:
        import win32com.client
        import pythoncom

        pythoncom.CoInitialize()

        try:
            # Connect to WMI
            locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            wmi_conn = locator.ConnectServer(".", "root\\cimv2")

            # Query for devices with errors (ConfigManagerErrorCode != 0)
            query = "SELECT Name, DeviceID, ConfigManagerErrorCode, Description FROM Win32_PnPEntity WHERE ConfigManagerErrorCode <> 0"
            devices = wmi_conn.ExecQuery(query)

            error_devices = []

            # Error code translations (most common)
            error_codes = {
                1: "Device not configured correctly",
                3: "Driver corrupted or missing",
                10: "Device cannot start",
                12: "Not enough free resources",
                18: "Reinstall drivers for this device",
                19: "Registry returned unknown result",
                21: "Device being removed",
                22: "Device is disabled",
                24: "Device not present, not working properly, or does not have all drivers installed",
                28: "Drivers not installed",
                29: "Device disabled (firmware did not provide required resources)",
                31: "Device not working properly",
                32: "Driver disabled (service key cannot be loaded)",
                33: "Cannot determine resources",
                34: "Cannot determine settings",
                35: "System firmware lacks information to configure device",
                36: "IRQ translation failed",
                37: "Driver failed DriverEntry",
                38: "Driver returned failure from AddDevice",
                39: "Driver could not load (name might not be found)",
                40: "Driver access to service key denied",
                41: "Driver failed to load due to previous instance failure",
                42: "There is a duplicate device",
                43: "Hardware reported failure",
                44: "Application or service shut down this device",
                45: "Currently being restarted"
            }

            for device in devices:
                try:
                    error_code = device.Properties_("ConfigManagerErrorCode").Value
                    name = device.Properties_("Name").Value or "Unknown Device"
                    description = device.Properties_("Description").Value or name

                    error_devices.append({
                        'name': name,
                        'description': description,
                        'error_code': error_code,
                        'error_description': error_codes.get(error_code, f"Unknown error code {error_code}")
                    })

                    logging.debug(f"Device error found: {name} - Code {error_code}")
                except Exception as e:
                    logging.debug(f"Failed to parse device error: {e}")
                    continue

            pythoncom.CoUninitialize()

            if not error_devices:
                return {
                    "status": "ok",
                    "error_count": 0,
                    "message": "No Device Manager errors detected"
                }

            return {
                "status": "ok",
                "error_count": len(error_devices),
                "devices": error_devices,
                "message": f"{len(error_devices)} device(s) with errors detected"
            }

        except Exception as e:
            pythoncom.CoUninitialize()
            raise e

    except Exception as e:
        logging.debug(f"Device Manager error check failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


def collect_active_power_plan() -> Dict[str, Any]:
    """
    Get currently active Windows power plan.

    Critical for performance analysis:
    - "Power Saver" mode causes slowness
    - "High Performance" needed for gaming/workstation
    - "Balanced" is default

    Returns:
        dict with status, plan_name, plan_guid
    """
    try:
        import subprocess

        result = subprocess.run(
            ['powercfg', '/getactivescheme'],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if result.returncode == 0:
            output = result.stdout.strip()

            # Parse output: "Power Scheme GUID: <guid>  (<Plan Name>)"
            # Example: "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)"

            plan_name = "Unknown"
            plan_guid = "Unknown"

            if '(' in output and ')' in output:
                plan_name = output.split('(')[1].split(')')[0].strip()

            if 'GUID:' in output:
                plan_guid = output.split('GUID:')[1].split('(')[0].strip()

            # Classify plan
            performance_level = "normal"
            if "power saver" in plan_name.lower():
                performance_level = "power_saver"
            elif "high performance" in plan_name.lower() or "ultimate" in plan_name.lower():
                performance_level = "high_performance"
            elif "balanced" in plan_name.lower():
                performance_level = "balanced"

            logging.debug(f"Active power plan: {plan_name} ({performance_level})")

            return {
                "status": "ok",
                "plan_name": plan_name,
                "plan_guid": plan_guid,
                "performance_level": performance_level
            }
        else:
            return {
                "status": "unavailable",
                "reason": f"powercfg failed with exit code {result.returncode}"
            }

    except Exception as e:
        logging.debug(f"Power plan detection failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


def collect_boot_time() -> Dict[str, Any]:
    """
    Measure system boot time from Event Viewer.

    Critical for quantifying "slow boot" complaints:
    - < 15s: Excellent (NVMe SSD)
    - 15-30s: Good (SATA SSD)
    - 30-60s: Fair (HDD or slow SSD)
    - > 60s: Poor (problem detected)

    Returns:
        dict with status, boot_time_seconds, classification
    """
    try:
        import subprocess
        import xml.etree.ElementTree as ET

        # Query Event ID 100 from Microsoft-Windows-Diagnostics-Performance/Operational
        # This event contains BootTime in milliseconds
        powershell_cmd = """
        Get-WinEvent -FilterHashtable @{
            LogName='Microsoft-Windows-Diagnostics-Performance/Operational';
            ID=100
        } -MaxEvents 1 -ErrorAction SilentlyContinue |
        ForEach-Object {
            [xml]$xml = $_.ToXml()
            $bootTime = $xml.Event.EventData.Data | Where-Object {$_.Name -eq 'BootTime'} | Select-Object -ExpandProperty '#text'
            [PSCustomObject]@{
                TimeCreated = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
                BootTimeMs = $bootTime
            }
        } | ConvertTo-Json
        """

        result = subprocess.run(
            [_POWERSHELL_EXE, '-NoProfile', '-Command', powershell_cmd],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if result.returncode == 0 and result.stdout.strip():
            import json
            data = json.loads(result.stdout)

            boot_time_ms = int(data.get('BootTimeMs', 0))
            boot_time_s = boot_time_ms / 1000.0
            time_created = data.get('TimeCreated', 'Unknown')

            # Classify boot time
            if boot_time_s < 15:
                classification = "Excellent (NVMe SSD performance)"
            elif boot_time_s < 30:
                classification = "Good (SATA SSD performance)"
            elif boot_time_s < 60:
                classification = "Fair (HDD or slow SSD)"
            else:
                classification = "Poor (performance issue detected)"

            logging.debug(f"Boot time: {boot_time_s:.1f}s ({classification})")

            return {
                "status": "ok",
                "boot_time_seconds": round(boot_time_s, 1),
                "boot_time_ms": boot_time_ms,
                "last_boot": time_created,
                "classification": classification
            }
        else:
            return {
                "status": "unavailable",
                "reason": "Event log not available or no boot events found"
            }

    except Exception as e:
        logging.debug(f"Boot time measurement failed: {e}")
        return {
            "status": "unavailable",
            "reason": str(e)
        }


# ============================================================================
# 8. WIFI DIAGNOSTICS
# ============================================================================

def collect_wifi_info() -> Dict[str, Any]:
    """
    Collect WiFi adapter diagnostics via netsh wlan show interfaces.
    Returns signal strength, link speed, radio type, adapter name, SSID.
    Works without any third-party tools — netsh is built into Windows.
    """
    import subprocess
    import re

    try:
        import sys
        result = subprocess.run(
            ['netsh', 'wlan', 'show', 'interfaces'],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )

        output = result.stdout
        logging.debug(f"WiFi netsh output (first 500 chars): {repr(output[:500])}")

        # Windows 11 can block WLAN details behind the Location permission.
        if 'location permission' in output.lower():
            logging.info("WiFi: WLAN details blocked by Windows Location permission [TAG:WIFI permission]")
            return {
                'status': 'permission_required',
                'reason': 'Windows Location permission is required to read WiFi details',
            }

        # No wireless adapter present
        if 'There is no wireless interface' in output or not output.strip():
            logging.info("WiFi: No wireless adapter found [TAG:WIFI no_adapter]")
            return {'status': 'no_adapter', 'reason': 'No wireless adapter found'}

        # No interfaces at all
        if 'There are no' in output and 'interfaces' in output:
            logging.info("WiFi: No wireless interfaces found [TAG:WIFI no_adapter]")
            return {'status': 'no_adapter', 'reason': 'No wireless adapter found'}

        def _extract(pattern, text, default=None):
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            return m.group(1).strip() if m else default

        state         = _extract(r'^\s+State\s*:\s*(.+)$', output, 'unknown')
        adapter_name  = _extract(r'^\s+Description\s*:\s*(.+)$', output)
        ssid          = _extract(r'^\s+SSID\s*:\s*(.+)$', output)
        radio_type    = _extract(r'^\s+Radio type\s*:\s*(.+)$', output)
        signal_str    = _extract(r'^\s+Signal\s*:\s*(\d+)%', output)
        rx_rate_str   = _extract(r'^\s+Receive rate \(Mbps\)\s*:\s*([\d.]+)', output)
        tx_rate_str   = _extract(r'^\s+Transmit rate \(Mbps\)\s*:\s*([\d.]+)', output)
        channel_str   = _extract(r'^\s+Channel\s*:\s*(\d+)', output)
        auth          = _extract(r'^\s+Authentication\s*:\s*(.+)$', output)

        # Not connected
        if state and state.lower() != 'connected':
            logging.info(f"WiFi: adapter present but state='{state}' [TAG:WIFI disconnected]")
            return {
                'status': 'disconnected',
                'adapter_name': adapter_name,
                'state': state,
                'reason': f'WiFi adapter present but not connected (state: {state})'
            }
        elif not state:
            logging.warning("WiFi: could not parse state from netsh output [TAG:WIFI parse_error]")
            logging.debug(f"Full netsh output: {repr(output)}")
            return {'status': 'unavailable', 'reason': 'Could not parse WiFi state from netsh output'}

        signal   = int(signal_str)   if signal_str   else None
        rx_rate  = float(rx_rate_str) if rx_rate_str  else None
        tx_rate  = float(tx_rate_str) if tx_rate_str  else None
        channel  = int(channel_str)  if channel_str  else None

        # Map radio type to friendly WiFi generation label
        wifi_gen = {
            '802.11ax': 'WiFi 6 (802.11ax)',
            '802.11ac': 'WiFi 5 (802.11ac)',
            '802.11n':  'WiFi 4 (802.11n)',
            '802.11g':  'WiFi 3 (802.11g) — outdated',
            '802.11b':  'WiFi 1/2 (802.11b) — very outdated',
        }.get(radio_type.lower() if radio_type else '', radio_type or 'Unknown')

        result_data = {
            'status':       'ok',
            'adapter_name': adapter_name,
            'ssid':         ssid,
            'radio_type':   radio_type,
            'wifi_gen':     wifi_gen,
            'signal_pct':   signal,
            'rx_rate_mbps': rx_rate,
            'tx_rate_mbps': tx_rate,
            'channel':      channel,
            'auth':         auth,
        }

        logging.info(
            f"WiFi: {adapter_name} — {signal}% signal, "
            f"RX {rx_rate} Mbps / TX {tx_rate} Mbps, {radio_type}, "
            f"SSID: {ssid} [TAG:WIFI ok]"
        )

        return result_data

    except FileNotFoundError:
        return {'status': 'unavailable', 'reason': 'netsh not found (non-Windows?)'}
    except subprocess.TimeoutExpired:
        return {'status': 'unavailable', 'reason': 'netsh timed out'}
    except Exception as e:
        logging.warning(f"WiFi info collection failed: {e}")
        return {'status': 'unavailable', 'reason': str(e)}


# ============================================================================
# MAIN COLLECTION FUNCTION
# ============================================================================

def _run_with_timeout(func, timeout_sec: int, label: str) -> Dict[str, Any]:
    """Run a health check function with a hard timeout via threading."""
    import threading

    result = [None]
    exc = [None]

    def _target():
        try:
            result[0] = func()
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        logging.warning(f"{label} timed out after {timeout_sec}s")
        return {"status": "unavailable", "reason": f"Timed out after {timeout_sec}s"}

    if exc[0]:
        logging.warning(f"{label} failed: {exc[0]}")
        return {"status": "unavailable", "reason": str(exc[0])}

    return result[0] or {"status": "unavailable", "reason": "No result"}


def _get_opencv_vendor_dir():
    """Get vendor directory for auto-installing opencv."""
    try:
        from settings import get_vendor_dir
        return get_vendor_dir()
    except Exception:
        import os
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor')


def collect_webcam_info() -> Dict[str, Any]:
    """
    Detect webcam(s) and test basic functionality.
    Uses WMI to enumerate devices, then attempts to open via OpenCV to verify.
    Falls back gracefully if OpenCV is not available.
    """
    import subprocess

    results = {
        'status': 'unavailable',
        'cameras': [],
    }

    # Step 1: Enumerate camera devices via PowerShell / WMI
    try:
        ps_script = """
        $cams = Get-WmiObject Win32_PnPEntity | Where-Object {
            $_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image' -or
            ($_.Name -match 'camera|webcam|web cam|imaging' -and $_.Status -eq 'OK')
        } | Select-Object Name, Status, Manufacturer, DeviceID
        if ($cams) { $cams | ConvertTo-Json -Compress } else { '[]' }
        """
        r = subprocess.run(
            [_POWERSHELL_EXE, '-NoProfile', '-Command', ps_script],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if _sys.platform == 'win32' else 0
        )
        if r.returncode == 0 and r.stdout.strip():
            import json
            raw = json.loads(r.stdout.strip())
            if isinstance(raw, dict):
                raw = [raw]  # single result returned as dict
            for cam in raw:
                results['cameras'].append({
                    'name':         cam.get('Name', 'Unknown'),
                    'manufacturer': cam.get('Manufacturer', ''),
                    'status':       cam.get('Status', ''),
                    'device_id':    cam.get('DeviceID', ''),
                })
    except Exception as e:
        logging.debug(f"Webcam WMI enumeration failed: {e}")

    if not results['cameras']:
        logging.info("Webcam: No camera devices found via WMI [TAG:WEBCAM none]")
        results['status'] = 'none'
        results['reason'] = 'No camera devices detected'
        return results

    # Step 2: Try to open camera with OpenCV to verify functional
    opencv_available = False
    try:
        import cv2
        opencv_available = True
    except ImportError:
        if not _can_auto_install_vendor_deps():
            return {
                "status": "unavailable",
                "reason": "opencv not available in packaged build",
                "camera_count": len(cameras),
                "cameras": cameras,
            }
        # Try to auto-install opencv-python-headless (small, no GUI deps)
        try:
            import subprocess as _sp
            _sp.run(
                [_sys.executable, '-m', 'pip', 'install', 'opencv-python-headless',
                 '--quiet', '--target', _get_opencv_vendor_dir()],
                timeout=60, capture_output=True
            )
            import importlib, sys as _sys2
            _sys2.path.insert(0, _get_opencv_vendor_dir())
            import cv2
            opencv_available = True
        except Exception:
            pass

    if opencv_available:
        try:
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    h, w = frame.shape[:2]
                    results['status'] = 'ok'
                    results['functional'] = True
                    results['resolution_test'] = f"{w}x{h}"
                    logging.info(
                        f"Webcam: {results['cameras'][0]['name']} — functional, "
                        f"test frame {w}x{h} [TAG:WEBCAM ok]"
                    )
                else:
                    results['status'] = 'detected_not_functional'
                    results['functional'] = False
                    logging.info("Webcam: device opened but could not capture frame [TAG:WEBCAM no_frame]")
            else:
                results['status'] = 'detected_not_functional'
                results['functional'] = False
                logging.info("Webcam: device found but could not be opened [TAG:WEBCAM open_failed]")
        except Exception as e:
            results['status'] = 'detected'
            results['functional'] = None
            logging.debug(f"Webcam OpenCV test failed: {e}")
    else:
        # OpenCV not available — report device found but untested
        results['status'] = 'detected'
        results['functional'] = None
        results['note'] = 'Device detected via WMI; functional test requires opencv'
        logging.info(
            f"Webcam: {len(results['cameras'])} camera(s) found, functional test unavailable "
            f"(opencv not installed) [TAG:WEBCAM detected_no_test]"
        )

    return results



def collect_advanced_health_summary(
        log_callback=None,
        stress_started_callback=None,
        stress_temp_callback=None,
        stress_finished_callback=None,
        stress_cancel_requested_callback=None,
        skip_categories=None) -> Dict[str, Any]:
    """
    Collect extended system health checks.

    Each check fails independently - one failure doesn't prevent others.
    Each check has a hard timeout to prevent hangs.

    Args:
        log_callback: Optional callable(str) for progress reporting to GUI

    Returns:
        dict with keys: event_viewer, windows_update, defender,
                       temperatures, cpu_load_temp, startup_impact,
                       device_manager, power_plan, boot_time
    """
    def _log(msg):
        logging.info(msg)
        if log_callback:
            log_callback(msg)

    _log("Collecting extended system health checks...")

    skip = skip_categories or set()
    results = {}

    checks = [
        ("event_viewer",    "Event Viewer summary",    lambda: collect_event_viewer_summary(days=7), 30),
        ("windows_update",  "Windows Update health",   collect_windows_update_health, 30),
        ("defender",        "Microsoft Defender",       collect_defender_status, 20),
        ("temperatures",    "Temperature data",         collect_temperatures, 15),
        ("startup_impact",  "Startup items",            collect_startup_impact, 15),
        ("device_manager",  "Device Manager errors",    collect_device_manager_errors, 20),
        ("power_plan",      "Active power plan",        collect_active_power_plan, 10),
        ("boot_time",       "Boot time",                collect_boot_time, 15),
        ("wifi",            "WiFi diagnostics",         collect_wifi_info, 15),
        ("webcam",          "Webcam",                   collect_webcam_info, 20),
    ]

    skip_map = {
        'event_viewer': 'event_logs',
        'windows_update': 'windows_update',
        'defender': 'defender',
        'startup_impact': 'startup_items',
        'device_manager': 'device_manager',
        'power_plan': 'power_boot',
        'boot_time': 'power_boot',
        'wifi': 'network',
        'webcam': 'display',
    }
    filtered_checks = []
    for key, label, func, timeout in checks:
        category_key = skip_map.get(key)
        if category_key and category_key in skip:
            results[key] = {'status': 'skipped'}
            _log(f"  {label}: skipped\n")
            continue
        filtered_checks.append((key, label, func, timeout))
    checks = filtered_checks

    # Skip idle CPU/GPU temp sampling only when both related categories are skipped.
    if {'cpu', 'gpu'}.issubset(skip):
        checks = [(k, l, f, t) for k, l, f, t in checks if k != 'temperatures']
        results['temperatures'] = {'status': 'skipped'}

    for key, label, func, timeout in checks:
        _log(f"  Checking {label}...")
        results[key] = _run_with_timeout(func, timeout, label)

    temps_result = results.get('temperatures')
    if isinstance(temps_result, dict) and temps_result.get('status') == 'ok':
        if 'cpu' in skip:
            temps_result['cpu_temp_c'] = None
            temps_result['cpu_sensor'] = None
            temps_result['cpu_sensor_source'] = None
        if 'gpu' in skip:
            temps_result['gpu'] = None

    # Disk speed test — skip if storage category skipped
    if 'storage' in skip:
        results['disk_speed'] = {'status': 'skipped'}
        _log("  Drive speed: skipped\n")
    else:
        # Disk speed test — runs directly with its own timeout to avoid hang
        _log("  Checking drive read/write speed...")
        try:
            import signal
            results['disk_speed'] = collect_disk_speed_test("C:\\", test_size_mb=32)
            write = results['disk_speed'].get('display_write_mb_s', results['disk_speed'].get('write_mb_s'))
            read = results['disk_speed'].get('display_read_mb_s', results['disk_speed'].get('read_mb_s'))
            if write and read:
                suffix = " (cached read corrected)" if results['disk_speed'].get('cached_read_likely') else ""
                _log(f"  Drive speed: Read {read:.0f} MB/s, Write {write:.0f} MB/s{suffix}\n")
            else:
                _log(f"  Drive speed: unavailable\n")
        except Exception as e:
            logging.warning(f"Disk speed test failed: {e}")
            results['disk_speed'] = {"status": "unavailable", "reason": str(e)}

    # CPU load temp — skip if cpu category skipped
    if 'cpu' in skip:
        results['cpu_load_temp'] = {'status': 'skipped'}
        _log("  CPU stress test: skipped\n")
    else:
        # CPU load temp runs directly (not via _run_with_timeout) because
        # multiprocessing.Process cannot be spawned from a nested thread on Windows.
        _log("  Checking CPU temperature under load...")
        try:
            results['cpu_load_temp'] = collect_cpu_temp_under_load(
                duration_sec=20,
                thermal_limit_c=100.0,
                log_callback=log_callback,
                started_callback=stress_started_callback,
                temp_callback=stress_temp_callback,
                finished_callback=stress_finished_callback,
                cancel_requested_callback=stress_cancel_requested_callback,
            )
        except Exception as e:
            logging.warning(f"CPU load temp failed: {e}")
            results['cpu_load_temp'] = {"status": "unavailable", "reason": str(e)}

    # Memory temp — DDR5 exposes sensors via LHM, DDR4 usually doesn't
    if 'ram' in skip:
        results['memory_temp_c'] = {'status': 'skipped'}
        _log("  Memory temp: skipped\n")
    else:
        _log("  Checking memory temperature...")
        try:
            mem_temp = _collect_memory_temp_lhm()
            results['memory_temp_c'] = mem_temp  # None if unavailable
            if mem_temp:
                _log(f"  Memory temp: {mem_temp:.0f}\u00b0C\n")
            else:
                _log("  Memory temp: not available (DDR4 or sensor not exposed)\n")
        except Exception as e:
            logging.warning(f"Memory temp failed: {e}")
            results['memory_temp_c'] = None

    # GPU stress test — skip if gpu category skipped
    if 'gpu' in skip:
        results['gpu_load_temp'] = {'status': 'skipped'}
        _log("  GPU stress test: skipped\n")
    else:
        # GPU stress test — only if dedicated GPU detected
        # NOTE: We do NOT use the temperature result to detect the GPU — pynvml may not be
        # installed on the USB, causing gpu_temp to be None even when a GPU is present.
        # Instead we query nvidia-smi directly (fast, always available on NVIDIA systems).
        _log("  Checking for dedicated GPU...")
        try:
            gpu_name = None

            # Method 1: nvidia-smi (NVIDIA)
            try:
                import subprocess
                smi = subprocess.run(
                    ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )
                if smi.returncode == 0:
                    name = smi.stdout.strip().splitlines()[0].strip()
                    if name:
                        gpu_name = name
            except Exception:
                pass

            # Method 2: WMI fallback (catches AMD / any GPU missed by nvidia-smi)
            if gpu_name is None:
                try:
                    import wmi
                    c = wmi.WMI()
                    for gpu in c.Win32_VideoController():
                        name = (gpu.Name or '').strip()
                        desc = (gpu.Description or '').lower()
                        # Skip integrated Intel/AMD graphics
                        if name and not any(x in desc for x in ('microsoft', 'basic', 'virtual', 'remote')):
                            if 'intel' not in desc or 'arc' in desc:
                                gpu_name = name
                                break
                except Exception:
                    pass

            if gpu_name:
                _log(f"  Dedicated GPU detected ({gpu_name}) — running stress test...\n")
                results['gpu_load_temp'] = collect_gpu_temp_under_load(
                    duration_sec=20,
                    thermal_limit_c=100.0,
                    ramp_sec=15,
                    log_callback=log_callback,
                    temp_callback=stress_temp_callback,
                    finished_callback=None,
                )
            else:
                _log("  No dedicated GPU detected — skipping GPU stress\n")
                results['gpu_load_temp'] = {"status": "unavailable", "reason": "No dedicated GPU detected"}
        except Exception as e:
            logging.warning(f"GPU stress test failed: {e}")
            results['gpu_load_temp'] = {"status": "unavailable", "reason": str(e)}

    # Bridge device manager errors (detailed dicts) from AdvancedHealth
    dm = results.get('device_manager', {})
    if dm.get('status') == 'ok' and dm.get('devices'):
        results['_device_manager_errors'] = dm['devices']

    # Count successful checks
    successful = sum(1 for v in results.values() if isinstance(v, dict) and v.get("status") == "ok")
    total = len(checks)

    _log(f"  Advanced health: {successful}/{total} checks completed")

    return results
