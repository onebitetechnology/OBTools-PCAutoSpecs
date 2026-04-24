"""
RepairDesk HTML diagnostic note generator.

Generates HTML-formatted diagnostic reports for RepairDesk ticket notes.
Standalone formatting layer — no GUI dependency.

Usage:
    from report_formatter import ReportFormatter

    formatter = ReportFormatter(scan_history_path=Path('scan_history.json'))
    html = formatter.format_diagnostic_note(specs)
"""

import re
from datetime import datetime
from pathlib import Path

from assessments import assess_smart_status
from settings import APP_VERSION, get_report_title


class ReportFormatter:
    """
    Formats system spec dictionaries into HTML diagnostic notes for RepairDesk,
    and provides clipboard-friendly HTML output for "Copy All System Information".
    """

    def __init__(self, scan_history_path=None):
        """
        Args:
            scan_history_path: Optional Path to the scan_history.json file
        """
        self.scan_history_path = Path(scan_history_path) if scan_history_path else None

    # ────────────────────────────────────────────────────────────────────
    # Main public API — RepairDesk diagnostic note
    # ────────────────────────────────────────────────────────────────────


    def format_diagnostic_note(self, specs, upload_mode='full'):
        """
        Transform system specs dictionary into HTML diagnostic note for RepairDesk.
        """
        if upload_mode == 'overview':
            sections = [self._format_overview_only_note(specs)]
        else:
            sections = [self._format_system_overview_section(specs)]

            critical = self._format_critical_issues(specs)
            if critical:
                sections.append(critical)

            sections.append(self._format_cpu_section(specs))
            sections.append(self._format_ram_section(specs))
            sections.append(self._format_gpu_section(specs))
            sections.append(self._format_motherboard_bios_section(specs))

            battery = self._format_battery_config(specs)

            storage = self._format_storage_health_comprehensive(specs)

            network = self._format_network_hardware(specs)
            monitors = self._format_display_config(specs)

            health = self._format_advanced_health(specs)
            if storage:
                sections.append(storage)
            if network:
                sections.append(network)
            if monitors:
                sections.append(monitors)
            if battery:
                sections.append(battery)
            if health:
                sections.append(health)

        # ── Build styled HTML output ──────────────────────────────
        # Section emoji map — matches the header strings used in each _format_* method
        # HTML numeric character references — pure ASCII, survive any encoding pipeline,
        # decoded to emoji by every HTML renderer (including RepairDesk's note viewer)
        SECTION_EMOJI = {
            'System Overview':          '&#x1F5A5;',  # 🖥  desktop computer
            'Critical Issues':          '&#x1F6A8;',  # 🚨  rotating light
            'CPU':                      '&#x2699;',   # ⚙
            'RAM':                      '&#x1F9E0;',  # 🧠
            'GPU':                      '&#x1F3AE;',  # 🎮
            'Motherboard & BIOS':       '&#x1F4BB;',  # 💻
            'Storage':                  '&#x1F4BE;',  # 💾
            'Network & Peripherals':    '&#x1F310;',  # 🌐
            'Monitors':                 '&#x1F4FA;',  # 📺
            'Battery':                  '&#x1F50B;',  # 🔋  battery
            'Advanced Diagnostics':     '&#x1FA7A;',  # 🩺
        }

        def _style_line(line, is_first_section=False):
            """
            Convert a plain <strong>Section Name</strong> header line into a
            styled section header with emoji and a top rule.
            Returns the original line unchanged if it isn't a section header.
            """
            import re
            # Match bare section header: <strong>Some Title</strong> with nothing else
            m = re.fullmatch(r'<strong>([^<]+)</strong>', line.strip())
            if not m:
                return line
            title = m.group(1)
            # Only style known top-level section names (not inline bold labels)
            if title not in SECTION_EMOJI:
                return line
            emoji = SECTION_EMOJI[title]
            return (
                (
                    "" if is_first_section else
                    "<div style='margin:6px 0 2px 0;padding-top:4px;"
                    "border-top:1px solid #CBD5E1;'></div>"
                )
                + f"<strong style='font-size:10.5pt;color:#1E3A5F;'>"
                f"{emoji}&nbsp;&nbsp;{title}</strong>"
            )

        output_lines = []
        for section in sections:
            if not section:
                continue
            output_lines.extend(line for line in section if line)

        # Apply section header styling
        styled = []
        first_section_seen = False
        for line in output_lines:
            stripped = line.strip()
            is_section = bool(re.fullmatch(r'<strong>([^<]+)</strong>', stripped))
            if line.startswith("  - "):
                line = f"&nbsp;&nbsp;&bull; {line[4:]}"
            elif line.startswith("    • "):
                line = f"&nbsp;&nbsp;&nbsp;&nbsp;&bull; {line[6:]}"

            styled.append(_style_line(line, is_first_section=is_section and not first_section_seen))
            if is_section:
                title = re.fullmatch(r'<strong>([^<]+)</strong>', stripped).group(1)
                if title in SECTION_EMOJI:
                    first_section_seen = True

        # Skipped tests fine print
        skip_cats = specs.get('_job_skip_cats', set())
        if skip_cats:
            from settings import SCAN_CATEGORIES
            skip_labels = [label for key, label in SCAN_CATEGORIES if key in skip_cats]
            if skip_labels:
                styled.append(
                    f"<em style='color:#888;font-size:9pt;'>"
                    f"Tests not performed in this report: {', '.join(skip_labels)}"
                    f"</em>"
                )

        return (
            "<div style='font-family:Segoe UI, Arial, sans-serif;"
            "font-size:10pt;line-height:1.28;color:#1F2937;'>"
            + "<br>".join(styled) +
            "</div>"
        )

    @staticmethod
    def _build_ram_slot_labels(ram_details):
        """Guarantee unique RAM slot labels in the HTML report."""
        labels = []
        used_labels = set()

        for index, module in enumerate(ram_details):
            raw_label = str(module.get('slot') or '').strip()
            if not raw_label or raw_label.lower() == 'unknown':
                raw_label = f"DIMM {chr(65 + index)}"

            label = raw_label
            if label in used_labels:
                label = f"DIMM {chr(65 + index)}"
                suffix = 2
                while label in used_labels:
                    label = f"DIMM {chr(65 + index)}-{suffix}"
                    suffix += 1

            used_labels.add(label)
            labels.append(label)

        return labels

    def _get_report_meta_lines(self, specs, upload_title):
        """Shared header lines for both overview and full uploads."""
        lines = []
        tech_name = specs.get('_job_tech_name', '')
        if upload_title:
            summary_line = f"<strong>{upload_title}</strong>: PC AutoSpec Version {APP_VERSION}"
            if tech_name:
                summary_line += f". Uploaded by: {tech_name}"
            lines.append(summary_line)
        elif tech_name:
            lines.append(f"<strong>Uploaded by:</strong> {tech_name}")

        return lines

    def _format_overview_only_note(self, specs):
        """Short upload for routine tickets."""
        lines = self._get_report_meta_lines(specs, "System Overview")
        lines.extend(self._build_compact_system_overview_lines(specs))
        lines.append("")
        return lines

    def _format_current_os_version(self, specs):
        os_name = specs.get('OS', '')
        if os_name and os_name != 'Unknown':
            return os_name

        windows_details = specs.get('WindowsDetails', '')
        if isinstance(windows_details, str) and windows_details:
            details = {}
            for part in windows_details.split(', '):
                if ':' in part:
                    key, value = part.split(':', 1)
                    details[key.strip()] = value.strip()

            if details.get('Edition'):
                return details['Edition']

        return specs.get('OS', 'Unknown')

    def _build_compact_system_overview_lines(self, specs):
        """Compact top summary used by both overview-only and full reports."""
        lines = ["<strong>System Overview</strong>"]
        lines.append(f"<strong>Model:</strong> {self._extract_model_display(specs)}")
        lines.append(f"<strong>Serial Number:</strong> {self._extract_serial_display(specs)}")
        lines.append(
            f"<strong>Current OS Version:</strong> {self._format_current_os_version(specs)}"
        )
        lines.append(f"<strong>CPU:</strong> {self._extract_cpu_model(specs)}")
        lines.append(f"<strong>RAM:</strong> {self._summarize_ram_overview(specs)}")
        lines.append(f"<strong>Drive Information:</strong> {self._build_storage_overview(specs)}")
        return lines

    def _extract_model_display(self, specs):
        laptop_model = specs.get('LaptopModel', 'Not Available')
        screen_size = specs.get('ScreenSize')
        desktop_type = specs.get('DesktopType', '')
        system_type = specs.get('SystemType', 'Unknown')

        if laptop_model and laptop_model != 'Not Available':
            model_text = laptop_model
            if screen_size:
                model_text += f" ({screen_size})"
            return model_text
        if desktop_type and desktop_type != 'None':
            return desktop_type
        if system_type == 'Desktop':
            return 'Custom Build'
        return 'Not detected'

    def _extract_serial_display(self, specs):
        serial = specs.get('SerialNumber', '')
        return serial if serial and serial != 'N/A' else 'Not detected'

    def _extract_cpu_model(self, specs):
        cpu_raw = specs.get('CPU', 'Unknown CPU')
        if cpu_raw == 'Test skipped':
            return 'Test skipped'
        cpu_model = cpu_raw.split(' | ')[0] if ' | ' in cpu_raw else cpu_raw
        cpu_model = cpu_model.replace('Intel(R)', 'Intel').replace('(TM)', '')
        cpu_model = cpu_model.replace('(R)', '').replace('CPU @', '').strip()
        return cpu_model or 'Unknown CPU'

    def _summarize_ram_overview(self, specs):
        ram_raw = specs.get('RAM', 'Unknown')
        if ram_raw == 'Test skipped':
            return 'Test skipped'

        total_match = re.search(r'([\d.]+)\s*GB', ram_raw)
        mem_type_match = re.search(r'\b(DDR\d+|LPDDR\dX?|Unified Memory)\b', ram_raw, re.IGNORECASE)

        if total_match:
            try:
                rounded_total = int(round(float(total_match.group(1))))
                summary = f"{rounded_total}GB"
            except ValueError:
                summary = total_match.group(0).replace(' ', '')
        else:
            return ram_raw

        if mem_type_match:
            mem_type = mem_type_match.group(1).upper()
            return f"{summary} of {mem_type}"
        return summary

    def _build_storage_overview(self, specs):
        if specs.get('Storage') == 'Test skipped':
            return 'Test skipped'

        storage_health = specs.get('StorageHealth', []) or []
        storage_text = specs.get('Storage', '')
        if storage_text and storage_text != 'Storage information unavailable':
            usage_lines = []
            for line in storage_text.split('\n'):
                line = line.strip()
                if line:
                    summary_line = self._summarize_storage_line(line, storage_health)
                    if summary_line:
                        usage_lines.append(summary_line)
            if usage_lines:
                return '; '.join(usage_lines)

        drive_types = []
        for drive in storage_health:
            if drive.get('status') == 'N/A':
                continue
            dtype = self._classify_drive_type(drive)
            if dtype:
                drive_types.append(dtype)

        if drive_types:
            type_counts = []
            for dtype in sorted(set(drive_types), key=drive_types.index):
                count = drive_types.count(dtype)
                type_counts.append(f"{count}× {dtype}" if count > 1 else dtype)
            return ', '.join(type_counts)

        return 'Unknown'

    def _summarize_storage_line(self, line, storage_health):
        match = re.match(
            r'Drive\s+([A-Z]):\s*([\d.]+)\s*(GB|TB)\s+total,\s*([\d.]+)\s*GB\s+free\s*\(([\d.]+)%\s+used\)\s*(?:-\s*(.+))?$',
            line,
            re.IGNORECASE,
        )
        if not match:
            return line

        drive_letter = match.group(1).upper()
        total_value = float(match.group(2))
        total_unit = match.group(3).upper()
        free_gb = float(match.group(4))
        descriptor = (match.group(6) or "").strip()

        total_gb = total_value * 1000 if total_unit == 'TB' else total_value
        used_gb = max(0.0, total_gb - free_gb)

        drive_type = None if self._line_already_has_drive_type(descriptor) else self._classify_drive_type_from_storage_health(
            line, storage_health
        )
        if not drive_type and not self._line_already_has_drive_type(descriptor):
            drive_type = self._classify_drive_type_from_line(descriptor)
        if drive_type:
            descriptor = f"{descriptor} ({drive_type})" if descriptor else drive_type

        total_display = self._format_friendly_drive_total(total_gb, descriptor)
        used_display = self._format_capacity_value(used_gb)
        if descriptor:
            return f"{drive_letter}: {total_display}/{used_display} Used - {descriptor}"
        return f"{drive_letter}: {total_display}/{used_display} Used"

    def _format_friendly_drive_total(self, total_gb, descriptor=""):
        descriptor = descriptor or ""
        size_match = re.search(r'(\d+(?:\.\d+)?)\s*(TB|GB)\b', descriptor, re.IGNORECASE)
        if size_match:
            value = float(size_match.group(1))
            unit = size_match.group(2).upper()
            if unit == 'TB':
                return f"{self._trim_decimal(value)}TB"
            return f"{self._trim_decimal(value)}GB"

        observed_to_marketed = [
            (14.9, "16GB"),
            (29.8, "32GB"),
            (59.6, "64GB"),
            (111.8, "120GB"),
            (119.2, "128GB"),
            (149.0, "160GB"),
            (167.6, "180GB"),
            (223.5, "240GB"),
            (232.8, "250GB"),
            (238.5, "256GB"),
            (447.1, "480GB"),
            (465.7, "500GB"),
            (476.9, "512GB"),
            (698.6, "750GB"),
            (931.5, "1TB"),
            (1863.0, "2TB"),
            (2794.0, "3TB"),
            (3726.0, "4TB"),
            (7452.0, "8TB"),
        ]
        closest_actual, closest_label = min(
            observed_to_marketed,
            key=lambda item: abs(item[0] - total_gb),
        )
        if abs(closest_actual - total_gb) <= max(4.0, closest_actual * 0.12):
            return closest_label

        return f"{self._trim_decimal(total_gb)}GB"

    def _format_capacity_value(self, amount_gb):
        if amount_gb >= 1000:
            return f"{self._trim_decimal(amount_gb / 1000)}TB"
        return f"{self._trim_decimal(amount_gb, keep_two_decimals=True)}GB"

    @staticmethod
    def _trim_decimal(value, keep_two_decimals=False):
        if keep_two_decimals:
            text = f"{value:.2f}"
        else:
            text = f"{value:.1f}"
        return text.rstrip('0').rstrip('.')

    @staticmethod
    def _classify_drive_type(drive):
        friendly_type = drive.get('friendly_type')
        if friendly_type:
            return friendly_type
        bus_type = (drive.get('bus_type') or '').upper()
        model = (drive.get('model') or '').upper()
        media = (drive.get('media_type') or '').upper()
        if bus_type == 'NVME':
            return 'NVMe SSD'
        if bus_type == 'SATA' and ('SSD' in model or 'SSD' in media or 'SOLID STATE' in media):
            return 'SATA SSD'
        if bus_type == 'SATA' and ('HDD' in media or 'HARD DISK' in media):
            return 'HDD'
        if bus_type == 'USB':
            return 'USB'
        if 'NVME' in model or 'NVM' in model or drive.get('available_spare') is not None:
            return 'NVMe SSD'
        if 'USB' in model or drive.get('status') == 'N/A':
            return 'USB'
        if 'SSD' in model or 'SSD' in media or 'SOLID STATE' in media:
            return 'SATA SSD'
        if 'HDD' in media or 'HARD DISK' in media:
            return 'HDD'
        return None

    @staticmethod
    def _extract_drive_letter(line):
        match = re.match(r'Drive\s+([A-Z]):', line or '', re.IGNORECASE)
        if not match:
            return None
        return f"{match.group(1).upper()}:\\"

    def _classify_drive_type_from_line(self, line):
        upper = (line or '').upper()
        if 'NVME' in upper or 'NVM EXPRESS' in upper:
            return 'NVMe SSD'
        if 'SSD' in upper:
            return 'SATA SSD'
        if 'HDD' in upper:
            return 'HDD'
        return None

    def _classify_drive_type_from_storage_health(self, storage_line, storage_health):
        storage_upper = (storage_line or '').upper()
        for drive in storage_health or []:
            model = (drive.get('model') or '').strip()
            if model and model.upper() in storage_upper:
                return self._classify_drive_type(drive)
        return None

    @staticmethod
    def _line_already_has_drive_type(line):
        upper = (line or '').upper()
        return any(label in upper for label in ('NVME SSD', 'SATA SSD', 'USB', 'HDD'))

    # ────────────────────────────────────────────────────────────────────
    # RepairDesk note sections
    # ────────────────────────────────────────────────────────────────────

    def _format_system_overview_section(self, specs):
        """Format the top system overview section with a compact summary first."""
        lines = self._get_report_meta_lines(specs, "Full Diagnostic Results")
        lines.extend(self._build_compact_system_overview_lines(specs))

        system_type = specs.get('SystemType', 'Unknown')
        if system_type and system_type != 'Unknown':
            lines.append(f"<strong>System Type:</strong> {system_type}")

        hp_specific = specs.get('HPSpecific', {}) or {}
        family = hp_specific.get('system_family', '').strip()
        if family:
            lines.append(f"<strong>Product Family:</strong> {family}")
        sku = hp_specific.get('system_sku', '').strip()
        if sku and not re.match(r'^[0\s]+$', sku):
            lines.append(f"<strong>Product SKU:</strong> {sku}")

        windows_details = specs.get('WindowsDetails', '')
        if isinstance(windows_details, str) and windows_details not in ('Unknown', 'Windows details unavailable'):
            details_dict = {}
            for part in windows_details.split(', '):
                if ':' in part:
                    key, value = part.split(':', 1)
                    details_dict[key.strip()] = value.strip()

            for key in ('Version', 'Build', 'Installed', 'Last Boot'):
                value = details_dict.get(key)
                if value:
                    lines.append(f"<strong>{key}:</strong> {value}")

        system_health = specs.get('SystemHealth', '')
        if isinstance(system_health, list) and system_health:
            lines.append(f"<strong>Runtime Health:</strong> {system_health[0]}")
            for metric in system_health[1:]:
                lines.append(metric)
        elif system_health and system_health != 'Unknown':
            lines.append(f"<strong>Runtime Health:</strong> {system_health}")

        return lines

    def _get_critical_issues_list(self, specs) -> list:
        """Return just the list of critical issue strings — used by the scan summary popup."""
        issues = []
        skip_cats = specs.get('_job_skip_cats', set())
        ethernet_connected = bool(specs.get('_ethernet_connected'))

        # Storage
        if 'storage' not in skip_cats:
            storage_health = specs.get('StorageHealth', [])
            for drive in storage_health:
                model = drive.get('model', 'Unknown Drive')
                if 'USB' in model.upper():
                    continue
                smart_assessment = assess_smart_status(drive, model)
                if smart_assessment['severity'] in ['WARN', 'CAUTION', 'CRITICAL']:
                    status_label = smart_assessment['label']
                    if drive.get('health_percent'):
                        issues.append(f"Storage Health: {model} — {status_label} ({drive['health_percent']:.0f}% health)")
                    else:
                        issues.append(f"Storage Health: {model} — {status_label}")

        # Device Manager
        device_errors = specs.get('DeviceManagerErrors', [])
        if 'device_manager' not in skip_cats and device_errors:
            issues.append(f"Device Manager: {len(device_errors)} devices with driver errors")

        wu_health = specs.get('AdvancedHealth', {}).get('windows_update', {})
        if 'windows_update' not in skip_cats and wu_health.get('status') == 'ok':
            driver_count = int(wu_health.get('driver_updates_count', 0) or 0)
            optional_count = int(wu_health.get('optional_updates_count', 0) or 0)
            if driver_count or optional_count:
                bits = []
                if driver_count:
                    bits.append(f"{driver_count} driver")
                if optional_count:
                    bits.append(f"{optional_count} optional")
                issues.append(
                    f"Windows Update: {', '.join(bits)} update(s) available — install updates and re-run scan"
                )

        manufacturer_tools = specs.get('ManufacturerUpdateTools', {}) or {}
        if 'manufacturer_updates' not in skip_cats and manufacturer_tools.get('status') == 'warning':
            vendor = manufacturer_tools.get('vendor') or manufacturer_tools.get('manufacturer') or 'OEM'
            issues.append(
                f"Manufacturer Update Tools: {vendor} support app missing — install it and check OEM driver / BIOS updates"
            )

        # CPU/RAM usage
        system_health = specs.get('SystemHealth', '')
        if system_health:
            cpu_match = re.search(r'CPU Usage[:\s]+([\d.]+)%', system_health, re.IGNORECASE)
            if 'cpu' not in skip_cats and cpu_match and float(cpu_match.group(1)) > 25:
                issues.append(f"CPU Usage: HIGH ({float(cpu_match.group(1)):.0f}% at idle)")
            ram_match = re.search(r'Memory Usage[:\s]+([\d.]+)%', system_health, re.IGNORECASE)
            if 'ram' not in skip_cats and ram_match and float(ram_match.group(1)) > 85:
                issues.append(f"RAM Usage: HIGH ({float(ram_match.group(1)):.0f}%)")

        # Temperatures
        advanced = specs.get('AdvancedHealth', {})
        temps = advanced.get('temperatures', {})
        load = advanced.get('cpu_load_temp', {})
        idle_temp = temps.get('cpu_temp_c') if temps.get('status') == 'ok' else None
        if 'cpu' not in skip_cats and idle_temp and idle_temp > 60:
            issues.append(f"CPU Temp (Idle): HIGH ({idle_temp:.0f}\u00b0C \u2014 check thermal paste/cooling)")
        if 'cpu' not in skip_cats and load.get('status') == 'ok' and load.get('peak_temp_c'):
            peak = load['peak_temp_c']
            if load.get('aborted'):
                issues.append(f"CPU Temp (Load): CRITICAL \u2014 thermal limit hit at {peak:.0f}\u00b0C")
            elif load.get('throttling_detected'):
                issues.append(f"CPU Temp (Load): {peak:.0f}\u00b0C \u2014 thermal throttling detected (cooling service recommended)")
            elif peak > 90:
                issues.append(f"CPU Temp (Load): HIGH ({peak:.0f}\u00b0C \u2014 cooling service recommended)")

        # GPU load temp
        gpu_load = advanced.get('gpu_load_temp', {})
        if 'gpu' not in skip_cats and gpu_load.get('status') == 'ok' and gpu_load.get('peak_temp_c'):
            gpu_peak = gpu_load['peak_temp_c']
            gpu_name = gpu_load.get('gpu_name', 'GPU')
            if gpu_load.get('aborted'):
                issues.append(f"GPU Temp (Load): CRITICAL \u2014 thermal limit hit at {gpu_peak:.0f}\u00b0C ({gpu_name})")
            elif gpu_peak > 90:
                issues.append(f"GPU Temp (Load): HIGH ({gpu_peak:.0f}\u00b0C \u2014 cooling service recommended)")

        # Memory temp
        mem_temp = advanced.get('memory_temp_c')
        if 'ram' not in skip_cats and isinstance(mem_temp, (int, float)) and mem_temp > 50:
            issues.append(f"Memory Temp: HIGH ({mem_temp:.0f}\u00b0C \u2014 check airflow/cooling)")

        # MBR partition style warning (on boot drives only)
        if 'storage' not in skip_cats:
            storage_health = specs.get('StorageHealth', [])
            for i, drive in enumerate(storage_health):
                if drive.get('status') == 'N/A':
                    continue
                if drive.get('partition_style') == 'MBR':
                    model = drive.get('model', f'Drive {i+1}')
                    issues.append(
                        f"Partition Style: {model} uses MBR — "
                        f"legacy format, incompatible with Secure Boot and modern reinstalls"
                    )
                elif drive.get('partition_style') == 'RAW':
                    model = drive.get('model', f'Drive {i+1}')
                    issues.append(f"Partition Style: {model} is RAW (unformatted or unrecognized)")

        # Drive speed
        disk_speed = advanced.get('disk_speed', {})
        if 'storage' not in skip_cats and disk_speed.get('status') == 'ok':
            read_mb = disk_speed.get('display_read_mb_s', disk_speed.get('read_mb_s', 0))
            write_mb = disk_speed.get('display_write_mb_s', disk_speed.get('write_mb_s', 0))
            if read_mb < 80 or write_mb < 50:
                issues.append(
                    f"Drive Speed: VERY SLOW (Read {read_mb:.0f} MB/s, Write {write_mb:.0f} MB/s"
                    " \u2014 possible drive failure or HDD)")
            elif read_mb < 200 or write_mb < 100:
                issues.append(
                    f"Drive Speed: SLOW (Read {read_mb:.0f} MB/s, Write {write_mb:.0f} MB/s"
                    " \u2014 consider SSD upgrade)")

        # Extended HDD self-test
        if 'storage' not in skip_cats:
            drive_tests = specs.get('DriveExtendedTests', {}) or {}
            for drive in specs.get('StorageHealth', []) or []:
                if str(drive.get('friendly_type') or '').upper() != 'HDD':
                    continue
                disk_index = drive.get('disk_index')
                result = drive_tests.get(str(disk_index), {})
                if result.get('status') == 'failed':
                    issues.append(
                        f"Extended HDD Test: {drive.get('model', 'Unknown Drive')} — "
                        f"{result.get('summary', 'Failed')}"
                    )

        keyboard = advanced.get('keyboard_test', {})
        if 'keyboard' not in skip_cats and keyboard:
            if keyboard.get('status') == 'critical':
                issues.append(
                    f"Keyboard Test: {keyboard.get('summary', 'Issue - Some Keys not registered')}"
                )
            elif keyboard.get('status') == 'warning':
                issues.append(
                    f"Keyboard Test: {keyboard.get('summary', 'Issue detected')}"
                )

        # WiFi diagnostics
        wifi = advanced.get('wifi', {})
        wifi_status = wifi.get('status')
        is_laptop = str(specs.get('SystemType', '')).lower() in ('laptop', 'notebook', 'tablet')
        if 'network' in skip_cats:
            pass
        elif wifi_status == 'no_adapter':
            if is_laptop:
                issues.append("WiFi: No wireless adapter detected — unusual for a laptop")
        elif wifi_status == 'disconnected':
            if not ethernet_connected:
                issues.append("WiFi: Adapter present but not connected")
        elif wifi_status == 'permission_required':
            pass
        elif wifi_status == 'ok':
            signal  = wifi.get('signal_pct')
            rx_rate = wifi.get('rx_rate_mbps')
            gen     = wifi.get('radio_type', '')
            if signal is not None and signal < 20:
                issues.append(f"WiFi Signal: VERY WEAK ({signal}%) \u2014 likely causing dropouts/disconnections")
            elif signal is not None and signal < 40:
                issues.append(f"WiFi Signal: WEAK ({signal}%) \u2014 poor connection quality")
            if rx_rate is not None and rx_rate < 54 and gen.lower() not in ('802.11g', '802.11b'):
                issues.append(f"WiFi Link Speed: VERY LOW ({rx_rate:.0f} Mbps) \u2014 possible adapter issue or heavy interference")
            if gen and gen.lower() in ('802.11g', '802.11b'):
                issues.append(f"WiFi Adapter: Outdated standard ({gen}) \u2014 consider USB WiFi adapter upgrade")

        # Webcam issues
        webcam = advanced.get('webcam', {})
        if 'display' not in skip_cats and webcam.get('status') == 'detected_not_functional':
            cams = webcam.get('cameras', [])
            cam_name = cams[0].get('name', 'Unknown') if cams else 'Unknown'
            issues.append(f"Webcam: Device detected but not responding — {cam_name}")

        return issues

    def _format_critical_issues(self, specs):
        """Detect and format critical issues - FIRST priority section using unified assessments"""
        lines = []
        issues = []
        skip_cats = specs.get('_job_skip_cats', set())
        ethernet_connected = bool(specs.get('_ethernet_connected'))

        # Check storage health using unified assessment
        if 'storage' not in skip_cats:
            storage_health = specs.get('StorageHealth', [])
            for drive in storage_health:
                model = drive.get('model', 'Unknown Drive')

                # Skip USB drives
                if 'USB' in model.upper():
                    continue

                # Use unified SMART assessment
                smart_assessment = assess_smart_status(drive, model)

                # Flag WARN/CAUTION/CRITICAL severity issues
                if smart_assessment['severity'] in ['WARN', 'CAUTION', 'CRITICAL']:
                    # Build consistent status line
                    status_label = smart_assessment['label']

                    # For "Failed" SMART reads, clarify
                    if status_label in ['Failed', 'Unknown']:
                        status_desc = f"{status_label} SMART read"
                    else:
                        # For Caution/Critical, include health %
                        if drive.get('health_percent'):
                            status_desc = f"{status_label} ({drive['health_percent']:.0f}% health)"
                        else:
                            status_desc = status_label

                    issues.append(f"Storage Health: {model} - {status_desc}")

        # Check for Device Manager errors
        device_errors = specs.get('DeviceManagerErrors', [])
        if 'device_manager' not in skip_cats and device_errors:
            issues.append(f"Device Manager: {len(device_errors)} devices with driver errors")

        wu_health = specs.get('AdvancedHealth', {}).get('windows_update', {})
        if 'windows_update' not in skip_cats and wu_health.get('status') == 'ok':
            driver_count = int(wu_health.get('driver_updates_count', 0) or 0)
            optional_count = int(wu_health.get('optional_updates_count', 0) or 0)
            if driver_count or optional_count:
                bits = []
                if driver_count:
                    bits.append(f"{driver_count} driver")
                if optional_count:
                    bits.append(f"{optional_count} optional")
                issues.append(
                    f"Windows Update: {', '.join(bits)} update(s) available - install updates and re-run scan"
                )

        manufacturer_tools = specs.get('ManufacturerUpdateTools', {}) or {}
        if 'manufacturer_updates' not in skip_cats and manufacturer_tools.get('status') == 'warning':
            vendor = manufacturer_tools.get('vendor') or manufacturer_tools.get('manufacturer') or 'OEM'
            issues.append(
                f"Manufacturer Update Tools: {vendor} support app missing - install it and check OEM driver / BIOS updates"
            )

        # Check for high CPU usage - SystemHealth is a string, parse it
        system_health = specs.get('SystemHealth', '')
        if system_health:
            cpu_match = re.search(r'CPU Usage[:\s]+([\d.]+)%', system_health, re.IGNORECASE)
            if cpu_match:
                cpu_usage = float(cpu_match.group(1))
                if 'cpu' not in skip_cats and cpu_usage > 25:
                    issues.append(f"CPU Usage: HIGH ({cpu_usage}% at idle - investigate malware)")

            # Check for high RAM usage
            ram_match = re.search(r'Memory Usage[:\s]+([\d.]+)%', system_health, re.IGNORECASE)
            if ram_match:
                ram_usage = float(ram_match.group(1))
                if 'ram' not in skip_cats and ram_usage > 85:
                    issues.append(f"RAM Usage: HIGH ({ram_usage}% - low available memory)")

        # Check CPU temperatures
        advanced = specs.get('AdvancedHealth', {})
        temps = advanced.get('temperatures', {})
        load = advanced.get('cpu_load_temp', {})

        # Idle temp warning: >60°C is concerning at idle
        idle_temp = temps.get('cpu_temp_c') if temps.get('status') == 'ok' else None
        if 'cpu' not in skip_cats and idle_temp and idle_temp > 60:
            issues.append(f"CPU Temp (Idle): HIGH ({idle_temp:.0f}°C — check thermal paste/cooling)")

        # Load temp: >90°C is concerning, thermal abort means it hit 100°C
        if 'cpu' not in skip_cats and load.get('status') == 'ok' and load.get('peak_temp_c'):
            peak = load['peak_temp_c']
            if load.get('aborted'):
                issues.append(f"CPU Temp (Load): CRITICAL — thermal limit hit at {peak:.0f}°C (urgent cooling service needed)")
            elif peak > 90:
                issues.append(f"CPU Temp (Load): HIGH ({peak:.0f}°C — cooling service recommended)")

        # Only show critical issues if there ARE any — no "all clear" filler
        wifi = advanced.get('wifi', {})
        wifi_status = wifi.get('status')
        is_laptop = str(specs.get('SystemType', '')).lower() in ('laptop', 'notebook', 'tablet')
        if 'network' not in skip_cats:
            if wifi_status == 'no_adapter' and is_laptop:
                issues.append("WiFi: No wireless adapter detected — unusual for a laptop")
            elif wifi_status == 'disconnected' and not ethernet_connected:
                issues.append("WiFi: Adapter present but not connected")

        drive_tests = specs.get('DriveExtendedTests', {}) or {}
        if 'storage' not in skip_cats:
            for drive in specs.get('StorageHealth', []) or []:
                if str(drive.get('friendly_type') or '').upper() != 'HDD':
                    continue
                disk_index = drive.get('disk_index')
                result = drive_tests.get(str(disk_index), {})
                if result.get('status') == 'failed':
                    issues.append(
                        f"Extended HDD Test: {drive.get('model', 'Unknown Drive')} — "
                        f"{result.get('summary', 'Failed')}"
                    )

        keyboard = advanced.get('keyboard_test', {})
        if 'keyboard' not in skip_cats and keyboard:
            if keyboard.get('status') == 'critical':
                issues.append(f"Keyboard Test: {keyboard.get('summary', 'Issue - Some Keys not registered')}")
            elif keyboard.get('status') == 'warning':
                issues.append(f"Keyboard Test: {keyboard.get('summary', 'Issue detected')}")

        if issues:
            lines.append("<strong>Critical Issues</strong>")
            for issue in issues:
                lines.append(f"- {issue}")

        return lines

    def _format_cpu_section(self, specs):
        """Format CPU section to match the UI CPU card."""
        lines = ["<strong>CPU</strong>"]

        cpu_raw = specs.get('CPU', 'Unknown CPU')
        cpu_details = specs.get('CPUDetails', {})

        if cpu_raw == 'Test skipped':
            lines.append("<strong>Processor:</strong> Test skipped")
            return lines

        cpu_model = cpu_raw.split(' | ')[0] if ' | ' in cpu_raw else cpu_raw
        cpu_model = cpu_model.replace('Intel(R)', 'Intel').replace('(TM)', '').replace('(R)', '')
        cpu_model = cpu_model.replace('CPU @', '').strip()
        lines.append(f"<strong>Processor:</strong> {cpu_model}")

        parts = []
        base_match = re.search(r'Base:\s*([\d.]+)\s*GHz', cpu_raw)
        boost_match = re.search(r'(?:Boost|Turbo):\s*([\d.]+)\s*GHz', cpu_raw)
        if base_match:
            parts.append(f"{base_match.group(1)} GHz")
        if boost_match:
            parts.append(f"{boost_match.group(1)} GHz")
        if parts:
            lines.append(f"<strong>Base / Boost Clock:</strong> {' / '.join(parts)}")

        ct = re.search(r'\((\d+)C/(\d+)T\)', cpu_raw)
        if ct:
            lines.append(f"<strong>Cores / Threads:</strong> {ct.group(1)} / {ct.group(2)}")

        if cpu_details.get('generation'):
            gen_text = cpu_details['generation']
            if cpu_details.get('year'):
                gen_text += f" ({cpu_details['year']})"
            lines.append(f"<strong>Generation:</strong> {gen_text}")
        if cpu_details.get('socket'):
            lines.append(f"<strong>Socket:</strong> {cpu_details['socket']}")
        if cpu_details.get('tdp'):
            lines.append(f"<strong>TDP:</strong> {cpu_details['tdp']}W")

        advanced = specs.get('AdvancedHealth', {})
        temps = advanced.get('temperatures', {})
        if temps.get('status') == 'ok' and temps.get('cpu_temp_c'):
            t = temps['cpu_temp_c']
            sensor = temps.get('cpu_sensor')
            t_label = '(Hot)' if t >= 80 else '(Warm)' if t >= 60 else '(Normal)'
            sensor_suffix = f" — {sensor}" if sensor else ""
            lines.append(f"<strong>Temp — Idle:</strong> {t:.0f}°C {t_label}{sensor_suffix}")

        load = advanced.get('cpu_load_temp', {})
        if load.get('status') == 'cancelled':
            lines.append("<strong>Temp — Load:</strong> Cancelled by tech")
        elif load.get('status') == 'ok' and load.get('peak_temp_c'):
            peak = load['peak_temp_c']
            aborted = load.get('aborted', False)
            sensor = load.get('sensor')
            p_label = '(Hot)' if peak >= 90 else '(Warm)' if peak >= 75 else '(Normal)'
            suffix = ' — thermal limit hit!' if aborted else ''
            if load.get('throttling_detected'):
                suffix += ' — throttling detected'
            if sensor:
                suffix += f" — {sensor}"
            lines.append(f"<strong>Temp — Load:</strong> {peak:.0f}°C {p_label}{suffix}")

        if cpu_details.get('windows_compatibility'):
            lines.append(f"<strong>Windows Compat:</strong> {cpu_details['windows_compatibility']}")
        if cpu_details.get('upgrade_path'):
            lines.append(f"<strong>Upgrade Options:</strong> {', '.join(cpu_details['upgrade_path'][:3])}")

        return lines

    def _format_ram_section(self, specs):
        """Format RAM section to match the UI RAM card."""
        lines = ["<strong>RAM</strong>"]

        ram_raw = specs.get('RAM', 'Unknown')
        ram_details = specs.get('RAMDetails', [])

        if ram_raw == 'Test skipped':
            lines.append("<strong>Memory:</strong> Test skipped")
            return lines

        lines.append(f"<strong>Memory:</strong> {ram_raw}")

        if ram_details:
            slot_labels = self._build_ram_slot_labels(ram_details)
            for module, slot in zip(ram_details, slot_labels):
                cap = module.get('size_gb', 0)
                speed = module.get('configured_speed', module.get('speed', 0))
                manufacturer = module.get('manufacturer', 'Unknown')
                part_number = module.get('part_number', '')
                value = f"{cap}GB @ {speed}MHz - {manufacturer}"
                if part_number and part_number != manufacturer:
                    value += f" {part_number}"
                lines.append(f"<strong>{slot}:</strong> {value}")

        for obs in specs.get('RAMCompatibilityWarnings', []):
            label = obs.get('label', '').rstrip(':')
            value = obs.get('value', '')
            message = obs.get('message', '')
            if label and value:
                lines.append(f"<strong>{label}:</strong> {value}")
            elif message:
                lines.append(message)

        return lines

    def _format_gpu_section(self, specs):
        """Format GPU section to match the UI GPU card."""
        lines = ["<strong>GPU</strong>"]

        gpu_raw = specs.get('GPU', 'Unknown')
        gpu_details = specs.get('GPUDetails', {})

        if gpu_raw == 'Test skipped':
            lines.append("<strong>Graphics:</strong> Test skipped")
            return lines

        gpu_parts = gpu_raw.split(' - Driver:')
        if len(gpu_parts) == 1:
            gpu_parts = gpu_raw.split(' | Driver:')
        gpu_model = gpu_parts[0].strip(' |')

        vram = None
        vram_match = re.search(r'\(([\d.]+)\s*GB\s+VRAM\)', gpu_model)
        if vram_match:
            vram = vram_match.group(1)
            gpu_model = gpu_model.replace(vram_match.group(0), '').strip()
        if not vram:
            vram_pipe = re.search(r'\|\s*([\d.]+)\s*GB\s+\w+', gpu_model)
            if vram_pipe:
                vram = vram_pipe.group(1)
                gpu_model = re.sub(r'\s*\|\s*[\d.]+\s*GB\s+\w+', '', gpu_model).strip()
        if not vram and gpu_details.get('vram'):
            vram_from_details = re.search(r'([\d.]+)', str(gpu_details['vram']))
            if vram_from_details:
                vram = vram_from_details.group(1)

        lines.append(f"<strong>Graphics:</strong> {gpu_model}")
        if vram:
            lines.append(f"<strong>VRAM:</strong> {vram} GB")

        driver_ver = gpu_details.get('driver_version', '')
        driver_date = gpu_details.get('driver_date', '')
        if driver_ver:
            driver_text = driver_ver + (f" ({driver_date})" if driver_date else "")
            lines.append(f"<strong>Driver Version:</strong> {driver_text}")
        elif len(gpu_parts) > 1:
            lines.append(f"<strong>Driver Version:</strong> {gpu_parts[1].strip()}")

        gpu_metrics = specs.get('GPUMetrics', {})
        if gpu_metrics.get('temperature') is not None:
            temp_text = f"{gpu_metrics['temperature']}°C"
            if gpu_metrics.get('temperature_sensor'):
                temp_text += f" — {gpu_metrics['temperature_sensor']}"
            lines.append(f"<strong>Temperature:</strong> {temp_text}")
        if gpu_metrics.get('core_clock'):
            lines.append(f"<strong>Core Clock:</strong> {gpu_metrics['core_clock']} MHz")
        if gpu_metrics.get('memory_clock'):
            lines.append(f"<strong>Memory Clock:</strong> {gpu_metrics['memory_clock']} MHz")
        if gpu_metrics.get('power_draw'):
            lines.append(f"<strong>Power Draw:</strong> {gpu_metrics['power_draw']:.1f} W")
        if gpu_metrics.get('utilization') is not None:
            lines.append(f"<strong>GPU Load:</strong> {gpu_metrics['utilization']}%")

        advanced = specs.get('AdvancedHealth', {})
        gpu_load = advanced.get('gpu_load_temp', {})
        if gpu_load.get('status') == 'ok' and gpu_load.get('peak_temp_c'):
            gpu_peak = gpu_load['peak_temp_c']
            gpu_sensor = gpu_load.get('sensor')
            gpu_suffix = ''
            if gpu_load.get('aborted'):
                gpu_suffix += ' — thermal limit hit!'
            if gpu_sensor:
                gpu_suffix += f" — {gpu_sensor}"
            lines.append(f"<strong>Temp — Load:</strong> {gpu_peak:.0f}°C{gpu_suffix}")

        return lines

    def _format_motherboard_bios_section(self, specs):
        """Format motherboard and BIOS section to match the UI card."""
        lines = ["<strong>Motherboard & BIOS</strong>"]

        motherboard = specs.get('Motherboard', 'Unknown')
        bios = specs.get('BIOS', 'Unknown')
        skip_cats = specs.get('_job_skip_cats', set())

        if 'motherboard' in skip_cats or motherboard == 'Test skipped' or bios == 'Test skipped':
            lines.append("<strong>Status:</strong> Test skipped")
            return lines

        lines.append(f"<strong>Motherboard:</strong> {motherboard}")

        chipset = specs.get('Chipset', '')
        if chipset:
            lines.append(f"<strong>Chipset:</strong> {chipset}")

        mobo_specs = specs.get('MotherboardSpecs')
        if mobo_specs:
            max_ram = (
                f"{mobo_specs['max_ram']} ({mobo_specs['max_per_dimm']} per slot × "
                f"{mobo_specs['dimm_slots']} slots)"
            )
            lines.append(f"<strong>Max RAM:</strong> {max_ram}")
            lines.append(f"<strong>Speeds:</strong> {mobo_specs['supported_speeds']}")

        lines.append(f"<strong>BIOS:</strong> {bios}")
        for detail_line in specs.get('BIOSDetails', []):
            if ':' in detail_line:
                key, value = detail_line.split(':', 1)
                lines.append(f"<strong>{key.strip()}:</strong> {value.strip()}")

        return lines

    def _format_hardware_config(self, specs):
        """Format hardware configuration section"""
        lines = []
        lines.append("<strong>Hardware Configuration</strong>")
        skip_cats = specs.get('_job_skip_cats', set())

        # CPU - Parse from combined string
        cpu_raw = specs.get('CPU', 'Unknown CPU')
        cpu_details = specs.get('CPUDetails', {})

        # Extract CPU model (before the " | " separator)
        cpu_model = cpu_raw.split(' | ')[0] if ' | ' in cpu_raw else cpu_raw
        cpu_model = cpu_model.replace('Intel(R)', 'Intel').replace('(TM)', '').replace('(R)', '')
        cpu_model = cpu_model.replace('CPU @', '').strip()

        lines.append(f"<strong>CPU:</strong> {cpu_model}")

        # Add CPU details if available
        if cpu_raw != 'Test skipped' and cpu_details:
            generation = cpu_details.get('generation', '')
            if generation:
                year = cpu_details.get('year', '')
                if year:
                    lines.append(f"<strong>Generation:</strong> {generation} - Released {year}")
                else:
                    lines.append(f"<strong>Generation:</strong> {generation}")

            # Parse cores/threads from CPU string
            ct = re.search(r'\((\d+)C/(\d+)T\)', cpu_raw)
            if ct:
                lines.append(f"<strong>Cores/Threads:</strong> {ct.group(1)} Physical / {ct.group(2)} Threads")

            # Parse clock speeds from CPU string
            if 'Base:' in cpu_raw and 'Boost:' in cpu_raw:
                base_match = re.search(r'Base:\s*([\d.]+)\s*GHz', cpu_raw)
                boost_match = re.search(r'Boost:\s*([\d.]+)\s*GHz', cpu_raw)
                if base_match and boost_match:
                    lines.append(f"<strong>Base/Boost Clock:</strong> {base_match.group(1)} GHz / {boost_match.group(1)} GHz")

            socket = cpu_details.get('socket', '')
            if socket:
                lines.append(f"<strong>Socket:</strong> {socket}")

            tdp = cpu_details.get('tdp', '')
            if tdp:
                tdp_str = str(tdp)
                if not tdp_str.upper().endswith('W'):
                    tdp_str += 'W'
                lines.append(f"<strong>TDP:</strong> {tdp_str}")

            # CPU Temperature
            advanced = specs.get('AdvancedHealth', {})
            temps = advanced.get('temperatures', {})
            if temps.get('status') == 'ok' and temps.get('cpu_temp_c'):
                t = temps['cpu_temp_c']
                sensor = temps.get('cpu_sensor')
                t_label = '(Hot)' if t >= 80 else '(Warm)' if t >= 60 else '(Normal)'
                sensor_suffix = f" — {sensor}" if sensor else ""
                lines.append(f"<strong>CPU Temp (Idle):</strong> {t:.0f}°C {t_label}{sensor_suffix}")
            load = advanced.get('cpu_load_temp', {})
            if load.get('status') == 'cancelled':
                lines.append("<strong>CPU Temp (Load):</strong> Cancelled by tech")
            if load.get('status') == 'ok' and load.get('peak_temp_c'):
                aborted = load.get('aborted', False)
                peak = load['peak_temp_c']
                sensor = load.get('sensor')
                p_label = '(Hot)' if peak >= 90 else '(Warm)' if peak >= 75 else '(Normal)'
                suffix = ' — thermal limit hit!' if aborted else (
                    ' — throttling detected' if load.get('throttling_detected') else '')
                if sensor:
                    suffix += f" — {sensor}"
                lines.append(f"<strong>CPU Temp (Load):</strong> {peak:.0f}°C {p_label}{suffix}")

            # Memory temp (DDR5 only — DDR4 usually not available)
            mem_temp = advanced.get('memory_temp_c')
            if isinstance(mem_temp, (int, float)):
                lines.append(f"<strong>Memory Temp:</strong> {mem_temp:.0f}\u00b0C")

            # GPU load temp
            gpu_load = advanced.get('gpu_load_temp', {})
            if gpu_load.get('status') == 'ok' and gpu_load.get('peak_temp_c'):
                gpu_name = gpu_load.get('gpu_name', 'GPU')
                gpu_peak = gpu_load['peak_temp_c']
                gpu_aborted = gpu_load.get('aborted', False)
                gpu_sensor = gpu_load.get('sensor')
                gpu_suffix = ' \u2014 thermal limit hit!' if gpu_aborted else ''
                if gpu_sensor:
                    gpu_suffix += f" \u2014 {gpu_sensor}"
                lines.append(f"<strong>GPU Temp (Load):</strong> {gpu_peak:.0f}\u00b0C{gpu_suffix} ({gpu_name})")

            # Windows compatibility
            if cpu_details.get('windows_compatibility'):
                lines.append(f"<strong>Windows Compatibility:</strong> {cpu_details['windows_compatibility']}")

        lines.append("")

        # RAM (Motherboard/BIOS moved to SYSTEM OVERVIEW to avoid duplication)
        ram_raw = specs.get('RAM', 'Unknown')
        ram_details = specs.get('RAMDetails', [])

        # Parse RAM capacity and speed from string
        # Example: "63.93 GB DDR4 @ 3600MHz (4 modules) - 19.4% used, 51.5 GB available"
        if ram_raw == 'Test skipped':
            lines.append("<strong>RAM:</strong> Test skipped")
        ram_match = re.search(r'([\d.]+)\s*GB\s+(DDR\d+)\s*@\s*([\d]+MHz)', ram_raw)
        if ram_match:
            capacity = ram_match.group(1)
            type_speed = f"{ram_match.group(2)}-{ram_match.group(3).replace('MHz', '')}"

            # Determine channel config
            module_count = len(ram_details) if ram_details else 0
            if module_count == 2:
                channel = "Dual Channel"
            elif module_count == 4:
                channel = "Quad Channel"
            elif module_count == 1:
                channel = "Single Channel"
            else:
                channel = f"{module_count} modules"

            lines.append(f"<strong>RAM:</strong> {capacity}GB {type_speed} ({channel})")

            # Add individual module details
            if ram_details:
                lines.append("<strong>Slot Population:</strong>")
                slot_labels = self._build_ram_slot_labels(ram_details)
                for module, slot in zip(ram_details, slot_labels):
                    cap = module.get('size_gb', 0)
                    speed = module.get('configured_speed', module.get('speed', 0))
                    manufacturer = module.get('manufacturer', 'Unknown')
                    part_number = module.get('part_number', '')
                    if part_number and part_number != manufacturer:
                        part_info = f"{manufacturer} {part_number}"
                    else:
                        part_info = manufacturer
                    lines.append(f"  - {slot}: {cap}GB @ {speed}MHz - {part_info}")

        # RAM compatibility warnings (skip Configuration — already in RAM header)
        for obs in specs.get('RAMCompatibilityWarnings', []):
            label = obs.get('label', '').rstrip(':')
            if label == 'Configuration':
                continue
            value = obs.get('value', '')
            message = obs.get('message', '')
            if label and value:
                lines.append(f"<strong>{label}:</strong> {value}")
            elif message:
                lines.append(message)

        lines.append("")

        # GPU
        gpu_raw = specs.get('GPU', 'Unknown')
        gpu_details = specs.get('GPUDetails', {})

        # Parse GPU model and details
        # Formats: "NVIDIA GeForce RTX 2080 SUPER (8.0 GB VRAM) - Driver: 32.0.15.8180 (10/29/2025)"
        #          "NVIDIA GeForce RTX 4070 Ti | 12 GB GDDR6X | Driver: 546.33 (12/14/2023)"
        #          "Intel Corporation HD Graphics 630 (rev 04)"
        gpu_parts = gpu_raw.split(' - Driver:')
        if len(gpu_parts) == 1:
            # Try pipe separator format
            gpu_parts = gpu_raw.split(' | Driver:')
        gpu_model = gpu_parts[0]

        # Extract VRAM from multiple formats
        vram = None
        # Format: "(8.0 GB VRAM)"
        vram_match = re.search(r'\(([\d.]+)\s*GB\s+VRAM\)', gpu_model)
        if vram_match:
            vram = vram_match.group(1)
            gpu_model = gpu_model.replace(vram_match.group(0), '').strip()
        # Format: "| 12 GB GDDR6X |" (pipe-separated)
        if not vram:
            vram_pipe = re.search(r'\|\s*([\d.]+)\s*GB\s+\w+', gpu_model)
            if vram_pipe:
                vram = vram_pipe.group(1)
                # Clean model: remove the VRAM segment
                gpu_model = re.sub(r'\s*\|\s*[\d.]+\s*GB\s+\w+', '', gpu_model).strip()
        # Fallback: GPUDetails dict
        if not vram and gpu_details.get('vram'):
            vram_from_details = re.search(r'([\d.]+)', str(gpu_details['vram']))
            if vram_from_details:
                vram = vram_from_details.group(1)

        # Clean trailing pipes from model
        gpu_model = gpu_model.strip(' |')

        lines.append(f"<strong>GPU:</strong> {gpu_model}")
        if gpu_raw == 'Test skipped':
            lines.append("")
            return lines
        if vram:
            lines.append(f"<strong>VRAM:</strong> {vram} GB")

        # GPU driver version (from the raw string) — stop before second GPU if present
        if len(gpu_parts) > 1:
            driver_info = gpu_parts[1].strip()
            # If the driver string contains a second GPU (comma-separated), split it
            if ', ' in driver_info:
                driver_only, second_gpu = driver_info.split(', ', 1)
                lines.append(f"<strong>GPU Driver:</strong> {driver_only.strip()}")
                # Show integrated GPU as a separate line
                second_gpu = second_gpu.strip()
                # Clean VRAM from second GPU name
                second_vram = re.search(r'\(([\d.]+)\s*GB\s+VRAM\)', second_gpu)
                if second_vram:
                    second_gpu = second_gpu.replace(second_vram.group(0), '').strip()
                lines.append(f"<strong>Integrated GPU:</strong> {second_gpu}")
            else:
                lines.append(f"<strong>GPU Driver:</strong> {driver_info}")

        # GPU temperature at idle — try GPUMetrics first, then AdvancedHealth
        gpu_metrics = specs.get('GPUMetrics', {})
        gpu_temp = gpu_metrics.get('temperature')
        gpu_sensor = gpu_metrics.get('temperature_sensor')
        if not gpu_temp:
            advanced = specs.get('AdvancedHealth', {})
            temps = advanced.get('temperatures', {})
            if temps.get('status') == 'ok' and temps.get('gpu'):
                gpu_temp = temps['gpu'].get('temp_c')
                gpu_sensor = temps['gpu'].get('sensor')
        if gpu_temp:
            sensor_suffix = f" — {gpu_sensor}" if gpu_sensor else ""
            lines.append(f"<strong>GPU Temperature:</strong> {gpu_temp}°C (idle){sensor_suffix}")

        lines.append("")

        return lines

    def _format_storage_health_comprehensive(self, specs):
        """Format comprehensive storage health for ALL drives - matches Activity Log counts"""
        lines = []
        lines.append("<strong>Storage</strong>")
        skip_cats = specs.get('_job_skip_cats', set())

        if 'storage' in skip_cats or specs.get('Storage') == 'Test skipped':
            lines.append("<strong>Status:</strong> Test skipped")
            lines.append("")
            return lines

        storage_health = specs.get('StorageHealth', [])
        if not storage_health:
            lines.append("<strong>Status:</strong> No storage health data available")
            lines.append("")
            return lines

        # Count drives by health status - using unified assessment with consistent labels
        healthy_drives = []
        failed_smart_drives = []  # "Failed" SMART reads (can't assess)
        degraded_drives = []  # Low health % but readable
        usb_drives = []

        for drive in storage_health:
            if drive.get('status') == 'N/A':
                usb_drives.append(drive)
                continue

            smart_assessment = assess_smart_status(drive, drive.get('model', 'Unknown'))

            # Classify by assessment label
            if smart_assessment['severity'] == 'OK':
                healthy_drives.append(drive)
            elif smart_assessment['label'] in ['Caution', 'Critical']:
                degraded_drives.append(drive)
            else:
                # SMART unavailable / unknown / failed — can't assess
                failed_smart_drives.append(drive)

        # Build specific summary line using assessment labels
        summary_parts = []
        if healthy_drives:
            summary_parts.append(f"{len(healthy_drives)} healthy")
        if degraded_drives:
            summary_parts.append(f"{len(degraded_drives)} degraded")
        if failed_smart_drives:
            summary_parts.append(f"{len(failed_smart_drives)} SMART unavailable")

        if degraded_drives:
            lines.append(f"<strong>Storage Health Summary:</strong> {', '.join(summary_parts)}")
        else:
            lines.append(f"<strong>Storage Health Summary:</strong> {', '.join(summary_parts)}")
        lines.append("")

        # Detail each drive (skip USB/external)
        drive_tests = specs.get('DriveExtendedTests', {}) or {}
        drive_num = 1
        for drive in storage_health:
            if drive.get('status') == 'N/A':
                continue  # Skip USB/external drives

            model = drive.get('model', 'Unknown Drive')
            size_gb = drive.get('size_gb', 0)

            drive_type = self._classify_drive_type(drive)
            model_upper = str(model).upper()

            drive_label = f"{model} ({drive_type})" if drive_type and drive_type.upper() not in model_upper else model
            lines.append(f"<strong>Drive {drive_num}: {drive_label}</strong>")
            drive_num += 1
            if size_gb:
                if size_gb >= 1000:
                    lines.append(f"<strong>Capacity:</strong> {size_gb/1024:.2f} TB ({size_gb:.0f} GB)")
                else:
                    lines.append(f"<strong>Capacity:</strong> {size_gb:.1f} GB")

            # Partition style (GPT / MBR)
            partition_style = drive.get('partition_style')
            if partition_style:
                style_note = {
                    'GPT': 'GPT',
                    'MBR': 'MBR — legacy partition style (max 2TB, no Secure Boot)',
                    'RAW': 'RAW — unformatted or unrecognized',
                }.get(partition_style, partition_style)
                lines.append(f"<strong>Partition Style:</strong> {style_note}")

            # Get assessment
            smart_assessment = assess_smart_status(drive, model)

            # Health status
            status_parts = [smart_assessment['label']]
            if drive.get('health_percent') is not None:
                status_parts.append(f"{drive['health_percent']:.1f}% health")
            lines.append(f"<strong>Status:</strong> {' - '.join(status_parts)}")

            # RST controller notice — explain limited data
            data_source = drive.get('data_source', '')
            if drive.get('rst'):
                if 'WINDOWS_HEALTH' in data_source:
                    lines.append("<strong>Note:</strong> NVMe behind Intel RST controller — SMART via Windows health API (limited detail)")
                elif 'CSMI' in data_source:
                    lines.append("<strong>Note:</strong> SATA behind Intel RST controller — SMART via CSMI passthrough")

            # Full SMART details — mirror everything the GUI shows
            if drive.get('power_on_hours'):
                hours = drive['power_on_hours']
                years = hours / 8760
                lines.append(f"<strong>Runtime:</strong> {hours:,}h (~{years:.1f} years)")

            if drive.get('power_cycles') is not None:
                lines.append(f"<strong>Power Cycles:</strong> {drive['power_cycles']:,}")

            if drive.get('temperature') is not None:
                lines.append(f"<strong>Temperature:</strong> {drive['temperature']}°C")

            if drive.get('percentage_used') is not None:
                lines.append(f"<strong>Wear Level:</strong> {drive['percentage_used']}%")

            if drive.get('available_spare') is not None:
                lines.append(f"<strong>Available Spare:</strong> {drive['available_spare']}%")

            if drive.get('reallocated_sectors') and drive['reallocated_sectors'] > 0:
                lines.append(f"<strong>Reallocated Sectors:</strong> {drive['reallocated_sectors']}")

            if drive.get('pending_sectors') and drive['pending_sectors'] > 0:
                lines.append(f"<strong>Pending Sectors:</strong> {drive['pending_sectors']}")

            if drive.get('uncorrectable_errors') and drive['uncorrectable_errors'] > 0:
                lines.append(f"<strong>Uncorrectable Errors:</strong> {drive['uncorrectable_errors']}")

            if drive.get('data_units_read') is not None:
                lines.append(f"<strong>Data Read:</strong> {drive['data_units_read']:.1f} TB")

            if drive.get('data_units_written') is not None:
                lines.append(f"<strong>Data Written:</strong> {drive['data_units_written']:.1f} TB")

            if drive.get('unsafe_shutdowns') and drive['unsafe_shutdowns'] > 0:
                lines.append(f"<strong>Unsafe Shutdowns:</strong> {drive['unsafe_shutdowns']:,}")

            if drive.get('media_errors') and drive['media_errors'] > 0:
                lines.append(f"<strong>Media Errors:</strong> {drive['media_errors']}")

            # Only show media type if it's actually known
            media_type = drive.get('media_type', '')
            if media_type and media_type.lower() != 'unknown':
                lines.append(f"<strong>Type:</strong> {media_type}")

            if str(drive.get('friendly_type') or '').upper() == 'HDD':
                disk_index = drive.get('disk_index')
                test_result = drive_tests.get(str(disk_index), {})
                if test_result:
                    lines.append(
                        f"<strong>Extended HDD Test:</strong> "
                        f"{test_result.get('summary', 'Status unavailable')}"
                    )

            # Issues from assessment
            if smart_assessment.get('issues'):
                lines.append(f"<strong>Issues:</strong>")
                for issue in smart_assessment['issues']:
                    lines.append(f"  - {issue}")

            lines.append("")

        # Disk usage — parse from SystemHealth string
        system_health = specs.get('SystemHealth', '')
        disk_usage_matches = re.findall(r'([A-Z]):\s*([\d.]+)%', system_health)
        if disk_usage_matches:
            usage_parts = [f"{letter}: {pct}% used" for letter, pct in disk_usage_matches]
            lines.append(f"<strong>Disk Usage:</strong> {', '.join(usage_parts)}")

        # Disk speed benchmark — belongs with storage
        advanced = specs.get('AdvancedHealth', {})
        disk_speed = advanced.get('disk_speed', {})
        if disk_speed.get('status') == 'ok':
            read_speed = disk_speed.get('display_read_mb_s', disk_speed.get('read_mb_s', 0))
            write_speed = disk_speed.get('display_write_mb_s', disk_speed.get('write_mb_s', 0))
            if read_speed > 2000:
                speed_cat = 'NVMe'
            elif read_speed > 400:
                speed_cat = 'SATA SSD'
            else:
                speed_cat = 'HDD/Slow'
            suffix = " (cached read corrected)" if disk_speed.get('cached_read_likely') else ""
            lines.append(f"<strong>Disk Speed (C:):</strong> {read_speed:.0f} MB/s read, {write_speed:.0f} MB/s write ({speed_cat}){suffix}")

        return lines

    def _format_network_hardware(self, specs):
        """Format network/peripheral details to mirror the UI section."""
        content = []
        skip_cats = specs.get('_job_skip_cats', set())

        network = specs.get('Network', '')
        if 'network' in skip_cats or network == 'Test skipped':
            return ["<strong>Network & Peripherals</strong>", "<strong>Status:</strong> Test skipped"]

        if network:
            if isinstance(network, str):
                network_adapters = [adapter.strip() for adapter in network.split('\n') if adapter.strip()]
            else:
                network_adapters = network

            for adapter in network_adapters:
                parts = adapter.split(' - MAC: ')
                adapter_name = parts[0].strip()
                mac_and_rest = parts[1].strip() if len(parts) > 1 else None
                if not adapter_name:
                    continue

                content.append(f"<strong>Adapter:</strong> {adapter_name}")

                if mac_and_rest:
                    driver_info = None
                    if ' | Driver: ' in mac_and_rest:
                        mac_and_speed, driver_info = mac_and_rest.split(' | Driver: ', 1)
                    else:
                        mac_and_speed = mac_and_rest

                    mac_address = mac_and_speed
                    speed_info = None
                    if '(' in mac_and_speed:
                        mac_address = mac_and_speed.split('(')[0].strip()
                        speed_info = mac_and_speed.split('(')[1].rstrip(')')

                    if mac_address:
                        content.append(f"  - MAC Address: {mac_address}")
                    if speed_info:
                        content.append(f"  - Link Speed: {speed_info}")
                    if driver_info:
                        content.append(f"  - Driver: {driver_info.strip()}")

        advanced = specs.get('AdvancedHealth', {})
        wifi = advanced.get('wifi', {})
        wifi_status = wifi.get('status')
        if wifi_status == 'ok':
            signal = wifi.get('signal_pct')
            rx_rate = wifi.get('rx_rate_mbps')
            tx_rate = wifi.get('tx_rate_mbps')
            gen = wifi.get('wifi_gen', wifi.get('radio_type', ''))
            ssid = wifi.get('ssid', '')
            adapter = wifi.get('adapter_name', '')
            if adapter:
                content.append(f"<strong>WiFi Adapter:</strong> {adapter}")
            if gen:
                content.append(f"<strong>WiFi Standard:</strong> {gen}")
            if ssid:
                content.append(f"<strong>SSID:</strong> {ssid}")
            if signal is not None:
                signal_label = (
                    f"{signal}% — Very Weak" if signal < 20 else
                    f"{signal}% — Weak" if signal < 40 else
                    f"{signal}%"
                )
                content.append(f"<strong>WiFi Signal:</strong> {signal_label}")
            if rx_rate and tx_rate:
                content.append(f"<strong>Link Speed:</strong> {rx_rate:.0f} / {tx_rate:.0f} Mbps (RX/TX)")
        elif wifi_status == 'disconnected':
            content.append("<strong>WiFi:</strong> Adapter present but not connected")
        elif wifi_status == 'permission_required':
            content.append("<strong>WiFi:</strong> Windows Location permission required to read WiFi details")
        elif wifi_status == 'no_adapter':
            content.append("<strong>WiFi:</strong> No wireless adapter detected")

        if content:
            lines = ["<strong>Network & Peripherals</strong>"]
            lines.extend(content)
            return lines
        return []

    def _format_display_config(self, specs):
        """Format display configuration section — only emits if display data exists"""
        content = []
        skip_cats = specs.get('_job_skip_cats', set())

        if 'display' in skip_cats or specs.get('Display') == 'Test skipped':
            return ["<strong>Monitors</strong>", "<strong>Status:</strong> Test skipped"]

        # Check if laptop (has panel details)
        panel_details = specs.get('PanelDetails', {})

        if panel_details and isinstance(panel_details, dict):
            # LAPTOP: Show detailed LCD panel information (for parts ordering)
            # Build a one-line summary for the header: size + manufacturer
            size_str = (str(panel_details['size_inches']) + '"') if panel_details.get('size_inches') else ''
            mfr_str  = panel_details.get('manufacturer', '')
            header_parts = [p for p in [size_str, mfr_str, 'Built-in LCD Panel'] if p]
            content.append(f"<strong>{' '.join(header_parts[:2])} Built-in LCD Panel:</strong>")
            content.append("")

            # Panel identification (critical for parts ordering)
            if panel_details.get('manufacturer'):
                content.append(f"<strong>Panel Manufacturer:</strong> {panel_details['manufacturer']}")

            if panel_details.get('model_code'):
                content.append(f"<strong>Part Number:</strong> {panel_details['model_code']} (use for LCD replacement ordering)")

            if panel_details.get('serial_number'):
                content.append(f"<strong>Panel Serial Number:</strong> {panel_details['serial_number']}")

            content.append("")

            # Display specifications
            # Size already shown in header — skip standalone line

            if panel_details.get('resolution_h') and panel_details.get('resolution_v'):
                res_h = panel_details['resolution_h']
                res_v = panel_details['resolution_v']
                res_type = ""
                if res_v >= 2160:
                    res_type = " (4K UHD)"
                elif res_v >= 1440:
                    res_type = " (QHD)"
                elif res_v >= 1080:
                    res_type = " (Full HD)"
                elif res_v >= 768:
                    res_type = " (HD)"

                content.append(f"<strong>Native Resolution:</strong> {res_h}x{res_v}{res_type}")

            # Touch screen capability (CRITICAL for correct replacement part ordering)
            if 'is_touch' in panel_details:
                is_touch = panel_details['is_touch']
                if is_touch is True:
                    touch_status = "Yes"
                elif is_touch is False:
                    touch_status = "No"
                else:
                    touch_status = "Unknown"
                content.append(f"<strong>Touch Screen:</strong> {touch_status}")

            if panel_details.get('connection_type'):
                content.append(f"<strong>Connection Type:</strong> {panel_details['connection_type']}")

            # Manufacturing details
            if panel_details.get('manufacture_year'):
                mfg_text = str(panel_details['manufacture_year'])
                if panel_details.get('manufacture_week'):
                    mfg_text += f" (Week {panel_details['manufacture_week']})"
                content.append(f"<strong>Panel Manufactured:</strong> {mfg_text}")

            content.append("")

        # Screen size — show when panel details not available (desktop/AIO)
        # or as confirmation line on laptops where PanelDetails succeeded
        if not panel_details:
            screen_size = specs.get('ScreenSize', '')
            if screen_size:
                content.append(f"<strong>Screen Size:</strong> {screen_size}")

        # External displays (for both laptops and desktops)
        displays = specs.get('Display', '')

        if displays:
            # Display is stored as a newline-separated string, split it
            if isinstance(displays, str):
                display_list = [d.strip() for d in displays.split('\n') if d.strip()]
            else:
                display_list = displays

            if panel_details:
                # Laptop with external displays
                content.append("<strong>External Displays:</strong>")

            for display in display_list:
                content.append(f"<strong>{display}</strong>")

        # Webcam — append to display section
        advanced = specs.get('AdvancedHealth', {})
        webcam = advanced.get('webcam', {})
        webcam_status = webcam.get('status')
        if webcam_status == 'ok':
            cams = webcam.get('cameras', [])
            cam_name = cams[0].get('name', 'Unknown') if cams else 'Unknown'
            resolution = webcam.get('resolution_test', '')
            content.append(
                f"<strong>Webcam:</strong> ✓ Functional — {cam_name}"
                + (f" (test frame: {resolution})" if resolution else "")
            )
        elif webcam_status == 'detected_not_functional':
            cams = webcam.get('cameras', [])
            cam_name = cams[0].get('name', 'Unknown') if cams else 'Unknown'
            content.append(f"<strong>Webcam:</strong> ⚠ Device detected but not responding — {cam_name}")
        elif webcam_status == 'detected':
            cams = webcam.get('cameras', [])
            cam_name = cams[0].get('name', 'Unknown') if cams else 'Unknown'
            content.append(f"<strong>Webcam:</strong> Detected — {cam_name} (functional test unavailable)")
        elif webcam_status == 'none':
            content.append("<strong>Webcam:</strong> No camera detected")

        # Only emit section if we have actual display data
        if content:
            lines = ["<strong>Monitors</strong>"]
            lines.extend(content)
            return lines
        return []

    def _format_battery_config(self, specs):
        """Format battery information section for laptops"""
        lines = []
        lines.append("<strong>Battery</strong>")
        skip_cats = specs.get('_job_skip_cats', set())

        battery_details = specs.get('BatteryDetails', {})
        battery_status = specs.get('Battery', 'Unknown')

        if 'battery' in skip_cats or battery_status == 'Test skipped':
            lines.append("<strong>Status:</strong> Test skipped")
            lines.append("")
            return lines

        if not battery_details:
            if battery_status and battery_status not in ('Unknown', 'Not Installed'):
                lines.append(f"<strong>Status:</strong> {battery_status}")
                lines.append("")
                return lines
            return []

        if battery_details:
            # Battery Model and Manufacturer
            if battery_details.get('model_name') or battery_details.get('manufacturer'):
                model = battery_details.get('model_name', 'Unknown')
                manufacturer = battery_details.get('manufacturer', '')
                if manufacturer:
                    lines.append(f"<strong>Battery:</strong> {model} ({manufacturer})")
                else:
                    lines.append(f"<strong>Battery:</strong> {model}")

            # Serial Number
            if battery_details.get('serial_number'):
                lines.append(f"<strong>Serial Number:</strong> {battery_details['serial_number']}")

            # Chemistry
            if battery_details.get('chemistry'):
                lines.append(f"<strong>Chemistry:</strong> {battery_details['chemistry']}")

            lines.append("")

            # Capacity Information
            design_capacity = self._format_battery_capacity(
                battery_details.get('design_capacity_mwh'),
                battery_details.get('design_capacity_wh'),
                battery_details.get('design_capacity_mah'),
            )
            if design_capacity:
                lines.append(f"<strong>Design Capacity:</strong> {design_capacity}")

            full_charge_capacity = self._format_battery_capacity(
                battery_details.get('full_charge_capacity_mwh'),
                battery_details.get('full_charge_capacity_wh'),
                battery_details.get('full_charge_capacity_mah'),
            )
            if full_charge_capacity:
                lines.append(f"<strong>Full Charge Capacity:</strong> {full_charge_capacity}")

            if battery_details.get('health_percent'):
                health = battery_details['health_percent']
                wear = battery_details.get('wear_percent', 0)
                lines.append(f"<strong>Battery Health:</strong> {health}% ({wear}% wear)")

            # Cycle Count
            if battery_details.get('cycle_count'):
                lines.append(f"<strong>Cycle Count:</strong> {battery_details['cycle_count']}")

            # Battery assessment (Excellent/Good/Fair/Poor) — mirrors GUI
            if battery_details.get('health_percent'):
                try:
                    from assessments import assess_battery_health
                    ba = assess_battery_health(
                        battery_details['health_percent'],
                        battery_details.get('cycle_count', 0)
                    )
                    lines.append(f"<strong>Battery Assessment:</strong> {ba['label']} — {ba['description']}")
                except Exception:
                    pass

            # ── Power Source & Real-Time Status ──────────────────
            lines.append("")
            lines.append("<strong>Power</strong>")

            # AC vs Battery
            if battery_details.get('ac_power') is not None:
                source = "AC Power (plugged in)" if battery_details['ac_power'] else "On Battery"
                lines.append(f"<strong>Power Source:</strong> {source}")

            # Charge % + status
            status_line = "<strong>Current Status:</strong> "
            if battery_details.get('charge_percent') is not None:
                status_line += f"{battery_details['charge_percent']}%"
            if battery_details.get('status'):
                sep = " — " if battery_details.get('charge_percent') is not None else ""
                status_line += f"{sep}{battery_details['status']}"
            elif battery_status:
                if battery_details.get('charge_percent') is None:
                    status_line += battery_status
            lines.append(status_line)

            # Charge/discharge rate
            if battery_details.get('charge_rate_w'):
                lines.append(f"<strong>Charge Rate:</strong> {battery_details['charge_rate_w']}W")
            elif battery_details.get('discharge_rate_w'):
                lines.append(f"<strong>Discharge Rate:</strong> {battery_details['discharge_rate_w']}W "
                             f"(drawing from battery)")

            # Voltage
            if battery_details.get('voltage_v'):
                lines.append(f"<strong>Voltage:</strong> {battery_details['voltage_v']}V")

            # Estimated runtime (only shown when on battery and available)
            if battery_details.get('estimated_runtime_min') and not battery_details.get('ac_power'):
                rt = battery_details['estimated_runtime_min']
                hrs, mins = divmod(rt, 60)
                rt_str = f"{hrs}h {mins}min" if hrs else f"{mins}min"
                lines.append(f"<strong>Estimated Runtime:</strong> ~{rt_str} remaining")

        lines.append("")
        return lines

    def _format_battery_capacity(self, capacity_mwh, capacity_wh, capacity_mah):
        """Prefer watt-hour based battery capacities when available."""
        if capacity_mwh:
            if capacity_wh:
                return f"{capacity_mwh:,} mWh ({capacity_wh} Wh)"
            return f"{capacity_mwh:,} mWh"
        if capacity_wh:
            return f"{capacity_wh} Wh"
        if capacity_mah:
            return f"{capacity_mah:,} mAh"
        return ""

    def _format_system_status(self, specs):
        """Format system status section with performance baselines"""
        lines = []
        lines.append("<strong>System Status</strong>")
        skip_cats = specs.get('_job_skip_cats', set())

        # OS
        os_name = specs.get('OS', 'Unknown')
        lines.append(f"<strong>OS:</strong> {os_name}")

        windows_details = specs.get('WindowsDetails', '')
        if windows_details:
            # Parse WindowsDetails string for installation date and last boot
            installed_match = re.search(r'Installed[:\s]+([^,]+)', windows_details, re.IGNORECASE)
            if installed_match:
                installed = installed_match.group(1).strip()
                lines.append(f"<strong>Installation Date:</strong> {installed}")

            last_boot_match = re.search(r'Last Boot[:\s]+([^,]+)', windows_details, re.IGNORECASE)
            if last_boot_match:
                last_boot = last_boot_match.group(1).strip()
                lines.append(f"<strong>Last Boot:</strong> {last_boot}")

        # Runtime Health - parse from string format
        system_health = specs.get('SystemHealth', '')
        if system_health:
            # Parse uptime from SystemHealth string
            uptime_match = re.search(r'Uptime[:\s]+([^,]+)', system_health, re.IGNORECASE)
            if uptime_match:
                uptime = uptime_match.group(1).strip()
                lines.append(f"<strong>System Uptime:</strong> {uptime}")

            lines.append("")

            lines.append("<strong>Idle Performance:</strong>")

            # Parse CPU usage from SystemHealth string
            cpu_match = re.search(r'CPU Usage[:\s]+([\d.]+)%', system_health, re.IGNORECASE)
            if cpu_match:
                cpu_usage = float(cpu_match.group(1))
                cpu_context = self._get_context_indicator(cpu_usage, 'cpu')
                lines.append(f"  <strong>CPU:</strong> {cpu_usage}% {cpu_context}")

            # Parse RAM usage from SystemHealth string
            ram_match = re.search(r'Memory Usage[:\s]+([\d.]+)%', system_health, re.IGNORECASE)
            if ram_match:
                ram_usage = float(ram_match.group(1))
                ram_context = self._get_context_indicator(ram_usage, 'ram')
                lines.append(f"  <strong>RAM:</strong> {ram_usage}% {ram_context}")

            # Disk usage shown in Storage Health section

        # Power plan
        power_plan = specs.get('ActivePowerPlan') or specs.get('PowerPlan')
        if 'power_boot' not in skip_cats and power_plan and power_plan != 'Test skipped':
            lines.append(f"<strong>Power Plan:</strong> {power_plan}")

        lines.append("")

        return lines

    @staticmethod
    def _get_context_indicator(percent, metric_type):
        """Return a brief context label for idle performance metrics."""
        if metric_type == 'cpu':
            if percent < 5:
                return '(idle)'
            elif percent < 15:
                return '(normal)'
            elif percent < 25:
                return '(elevated)'
            else:
                return '(HIGH — investigate)'
        else:  # ram
            if percent < 50:
                return '(light)'
            elif percent < 70:
                return '(normal)'
            elif percent < 85:
                return '(moderate)'
            else:
                return '(HIGH — low available memory)'

    @staticmethod
    def _parse_wmi_date(raw_date):
        """Parse WMI date format (YYYYMMDD...) to readable MM/DD/YYYY."""
        if not raw_date or raw_date == 'Unknown':
            return raw_date
        # WMI format: 20180802000000.******+*** or 20180802000000.000000+000
        m = re.match(r'(\d{4})(\d{2})(\d{2})', str(raw_date))
        if m:
            y, mo, d = m.group(1), m.group(2), m.group(3)
            # Skip obviously invalid dates (epoch/1968)
            if int(y) < 1990:
                return None
            return f"{mo}/{d}/{y}"
        return raw_date

    def _format_driver_status(self, specs):
        """Format driver status section"""
        lines = []
        lines.append("<strong>Drivers</strong>")
        skip_cats = specs.get('_job_skip_cats', set())

        # GPU Driver — extract actual driver info from GPU string
        gpu = specs.get('GPU', '')
        gpu_details = specs.get('GPUDetails', {})
        driver_ver = gpu_details.get('driver_version', '')
        driver_date = gpu_details.get('driver_date', '')

        if driver_ver:
            driver_text = driver_ver
            if driver_date:
                driver_text += f" ({driver_date})"

            # Driver age warning
            driver_age = gpu_details.get('driver_age_years')
            if driver_age and driver_age >= 2:
                age_int = int(driver_age)
                driver_text += f" — ~{age_int} year{'s' if age_int != 1 else ''} old"

            if 'NVIDIA' in gpu:
                lines.append(f"<strong>NVIDIA GPU Driver:</strong> {driver_text}")
            elif 'AMD' in gpu or 'Radeon' in gpu:
                lines.append(f"<strong>AMD GPU Driver:</strong> {driver_text}")
            else:
                lines.append(f"<strong>GPU Driver:</strong> {driver_text}")
        else:
            # Try extracting from GPU string: "... Driver: 546.33 (12/14/2023)"
            drv_match = re.search(r'Driver[:\s]+([\d.]+)(?:\s*\(([^)]+)\))?', gpu)
            if drv_match:
                drv_text = drv_match.group(1)
                if drv_match.group(2):
                    drv_text += f" ({drv_match.group(2)})"
                lines.append(f"<strong>GPU Driver:</strong> {drv_text}")
            elif 'Intel' in gpu:
                lines.append("<strong>GPU Driver:</strong> Intel integrated — using OS/Windows Update drivers")
            else:
                lines.append("<strong>GPU Driver:</strong> Not detected")

        # PCIe Generation
        pcie_gen = specs.get('PCIeGeneration', '')
        if pcie_gen:
            lines.append(f"<strong>PCIe Generation:</strong> {pcie_gen}")

        # Device Manager Errors
        device_errors = specs.get('DeviceManagerErrors', [])
        if 'device_manager' in skip_cats:
            lines.append("")
            lines.append("<strong>Device Manager Errors:</strong> Test skipped")
        elif device_errors:
            lines.append("")
            lines.append(f"<strong>Device Manager Errors:</strong> {len(device_errors)} devices with issues")
            for error in device_errors[:3]:  # Show first 3
                name = error.get('name', 'Unknown Device')
                desc = error.get('error_description', f"Error code {error.get('error_code', 'Unknown')}")
                lines.append(f"  - {name}: {desc}")
        else:
            lines.append("")
            lines.append("<strong>Device Manager Errors:</strong> None detected")

        lines.append("")

        return lines

    def _format_advanced_health(self, specs):
        """Format extended system health checks"""
        lines = []
        lines.append("<strong>Advanced Diagnostics</strong>")

        advanced = specs.get('AdvancedHealth', {})
        skip_cats = specs.get('_job_skip_cats', set())

        advanced_categories = (
            'event_logs', 'windows_update', 'manufacturer_updates', 'defender',
            'startup_items', 'device_manager', 'keyboard', 'power_boot',
        )
        if all(category in skip_cats for category in advanced_categories):
            lines.append("<strong>Status:</strong> Test skipped")
            return lines

        event_viewer = advanced.get('event_viewer', {})
        if 'event_logs' in skip_cats:
            lines.append("<strong>Event Log:</strong> Test skipped")
        elif event_viewer.get('status') == 'ok':
            total = event_viewer.get('total_events', 0)
            days = event_viewer.get('days_lookback', 7)
            if total == 0:
                lines.append(f"<strong>Event Log ({days} days):</strong> No errors or crashes detected")
            else:
                lines.append(f"<strong>Event Log ({days} days):</strong> {total} error/critical event(s)")
                for source in event_viewer.get('top_sources', [])[:5]:
                    lines.append(f"  - {source.get('name', 'Unknown')}: {source.get('count', 0)} event(s)")
                    for event in source.get('recent', [])[:2]:
                        event_line = self._format_event_viewer_line(event)
                        if event_line:
                            lines.append(f"    • {event_line}")
                latest_critical = event_viewer.get('latest_critical')
                if latest_critical:
                    crit_parts = [latest_critical.get('source', 'Unknown source')]
                    if latest_critical.get('timestamp'):
                        crit_parts.append(str(latest_critical['timestamp']))
                    if latest_critical.get('message'):
                        crit_parts.append(str(latest_critical['message']))
                    lines.append(f"  - Latest critical: {' — '.join(crit_parts)}")

        wu_health = advanced.get('windows_update', {})
        if 'windows_update' in skip_cats:
            lines.append("<strong>Windows Update:</strong> Test skipped")
        elif wu_health.get('status') == 'ok':
            last_update = wu_health.get('last_update') or {}
            if last_update:
                update_bits = [last_update.get('hotfix_id', 'Unknown')]
                if last_update.get('description'):
                    update_bits.append(last_update['description'])
                if last_update.get('installed_on'):
                    update_bits.append(last_update['installed_on'])
                lines.append(f"<strong>Windows Update:</strong> Last installed — {' — '.join(update_bits)}")
            else:
                lines.append("<strong>Windows Update:</strong> No recent installed update detected")
            failed = wu_health.get('failed_updates_last_30_days', 0)
            lines.append(
                f"  - Failed updates (30 days): {failed if failed else 'None'}"
            )
            lines.append(
                "  - Pending reboot: Yes — restart recommended"
                if wu_health.get('pending_reboot')
                else "  - Pending reboot: No"
            )
            driver_count = int(wu_health.get('driver_updates_count', 0) or 0)
            optional_count = int(wu_health.get('optional_updates_count', 0) or 0)
            if driver_count or optional_count:
                lines.append(
                    f"  - Additional updates available: {driver_count} driver, {optional_count} optional"
                )
                for title in (wu_health.get('driver_update_titles') or [])[:5]:
                    lines.append(f"    • Driver: {title}")
                for title in (wu_health.get('optional_update_titles') or [])[:5]:
                    lines.append(f"    • Optional: {title}")

        manufacturer_tools = specs.get('ManufacturerUpdateTools', {}) or {}
        if 'manufacturer_updates' in skip_cats:
            lines.append("<strong>Manufacturer Update Tools:</strong> Test skipped")
        elif manufacturer_tools:
            lines.append(
                f"<strong>Manufacturer Update Tools:</strong> "
                f"{manufacturer_tools.get('summary', 'Check unavailable')}"
            )
            found = manufacturer_tools.get('found_tools', []) or []
            if found:
                lines.append(f"  - Detected: {', '.join(found)}")
            recommended = manufacturer_tools.get('recommended_tools', []) or []
            if recommended and not found:
                lines.append(f"  - Recommended: {', '.join(recommended)}")
            note = manufacturer_tools.get('note')
            if note:
                lines.append(f"  - Note: {note}")

        defender = advanced.get('defender', {})
        if 'defender' in skip_cats:
            lines.append("<strong>Defender:</strong> Test skipped")
        elif defender.get('status') == 'ok':
            lines.append(
                "<strong>Defender:</strong> "
                + ("Real-time protection ON" if defender.get('realtime_enabled') else "Real-time protection OFF")
            )
            lines.append(
                "  - Antispyware: "
                + ("Enabled" if defender.get('antispyware_enabled') else "Disabled")
            )
            if defender.get('signature_last_updated'):
                sig_line = defender['signature_last_updated']
                if defender.get('signature_age_days') is not None:
                    sig_line += f" ({defender['signature_age_days']} day(s) old)"
                lines.append(f"  - Definitions updated: {sig_line}")
            if defender.get('last_quick_scan'):
                lines.append(f"  - Last quick scan: {defender['last_quick_scan']}")
            if defender.get('last_full_scan'):
                lines.append(f"  - Last full scan: {defender['last_full_scan']}")

        startup = advanced.get('startup_impact', {})
        if 'startup_items' in skip_cats:
            lines.append("<strong>Startup Items:</strong> Test skipped")
        elif startup.get('status') == 'ok':
            count = startup.get('startup_item_count', 0)
            lines.append(f"<strong>Startup Items:</strong> {count}")
            for item in startup.get('items', [])[:10]:
                name = item.get('Name', 'Unknown')
                source = item.get('Source', '')
                command = item.get('Command', '')
                detail = name
                if source:
                    detail += f" [{source}]"
                if command:
                    detail += f" — {command}"
                lines.append(f"  - {detail}")

        device_manager = advanced.get('device_manager', {})
        if 'device_manager' in skip_cats:
            lines.append("<strong>Device Manager:</strong> Test skipped")
        elif device_manager.get('status') == 'ok':
            count = device_manager.get('error_count', 0)
            lines.append(
                f"<strong>Device Manager:</strong> "
                + (f"{count} device(s) with errors" if count else "No device errors detected")
            )
            for device in device_manager.get('devices', [])[:10]:
                lines.append(
                    f"  - {device.get('name', 'Unknown')}: "
                    f"Code {device.get('error_code', '?')} — "
                    f"{device.get('error_description', 'Unknown error')}"
                )

        keyboard = advanced.get('keyboard_test', {})
        if 'keyboard' in skip_cats:
            lines.append("<strong>Keyboard Test:</strong> Test skipped")
        elif keyboard:
            lines.append(
                f"<strong>Keyboard Test:</strong> "
                f"{keyboard.get('summary', 'No keyboard result available')}"
            )

        power_plan = advanced.get('power_plan', {})
        boot_time = advanced.get('boot_time', {})
        if 'power_boot' in skip_cats:
            lines.append("<strong>Power & Boot:</strong> Test skipped")
        else:
            power_bits = []
            if power_plan.get('status') == 'ok':
                power_bits.append(power_plan.get('plan_name', 'Unknown'))
                if power_plan.get('performance_level'):
                    power_bits.append(power_plan['performance_level'].replace('_', ' '))
            if boot_time.get('status') == 'ok':
                power_bits.append(f"boot {boot_time.get('boot_time_seconds', 'Unknown')}s")
            if power_bits:
                lines.append(f"<strong>Power & Boot:</strong> {', '.join(power_bits)}")
            if power_plan.get('status') == 'ok' and power_plan.get('plan_guid'):
                lines.append(f"  - Active plan GUID: {power_plan['plan_guid']}")
            if boot_time.get('status') == 'ok':
                if boot_time.get('last_boot'):
                    lines.append(f"  - Last boot: {boot_time['last_boot']}")
                if boot_time.get('classification'):
                    lines.append(f"  - Boot assessment: {boot_time['classification']}")

        return lines

    @staticmethod
    def _format_event_viewer_line(event):
        """Condense event log detail rows for the upload note."""
        if not isinstance(event, dict):
            return str(event)

        app = event.get('app')
        description = event.get('description')
        event_id = event.get('id')
        event_time = event.get('time')

        if app and description:
            line = f"{app}: {description}"
        elif app:
            line = f"{app} crash"
        elif description:
            line = description
        elif event_id:
            line = f"Event {event_id}"
        else:
            line = "Unknown event"

        if event_time:
            line += f" ({event_time})"
        return line
