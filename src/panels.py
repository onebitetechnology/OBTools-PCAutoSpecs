"""
PC AutoSpec — Main content panels (PySide6).
SystemInfoPanel: scrollable spec display with sections.
ActivityLogPanel: colored log output with context menu.
"""

import re
import logging
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCharFormat, QColor, QAction
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QTextEdit, QFrame, QApplication,
)
from theme import COLORS
from widgets import RoundedCard, InfoSection, SpinnerWidget
from dialogs import DetailDialog


# ─── Activity Log Panel ──────────────────────────────────────────────

class ActivityLogPanel(QWidget):
    """Right-side activity log with colored text and context menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 0, 0, 0)
        layout.setSpacing(0)

        card = RoundedCard()
        layout.addWidget(card)

        # Header
        header = QLabel("Activity Log")
        header.setObjectName("sectionHeader")
        header.setContentsMargins(0, 0, 0, 8)
        card.inner_layout.addWidget(header)

        # Text area (no word wrap — long values stay on one line)
        self._log = QTextEdit()
        self._log.setObjectName("activityLog")
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        card.inner_layout.addWidget(self._log, 1)

        # Context menu
        self._log.setContextMenuPolicy(Qt.CustomContextMenu)
        self._log.customContextMenuRequested.connect(self._show_context_menu)

        # Tag → color mapping
        self._tag_colors = {
            'success': QColor(COLORS['console_success']),
            'error': QColor(COLORS['console_error']),
            'warning': QColor(COLORS['console_warning']),
            'info': QColor(COLORS['console_info']),
        }

    def append(self, text, tag=None):
        """Append text with optional color tag.

        Lines matching "Label... Value" are split: label bold, value colored.
        A blank line separates each detection group.
        """
        cursor = self._log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        plain = QTextCharFormat()

        if tag and tag in self._tag_colors:
            # Fully colored line (success/error/warning)
            fmt = QTextCharFormat()
            fmt.setForeground(self._tag_colors[tag])
            cursor.insertText(text, fmt)
        elif '...' in text and not text.startswith(' '):
            # Blank line before each new detection entry (if log has content)
            if self._log.toPlainText().strip():
                cursor.insertText('\n', plain)

            # Split "Detecting CPU... Intel i7" into bold label + colored value
            parts = text.split('...', 1)
            label = parts[0] + '...'
            value = parts[1] if len(parts) > 1 else ''

            label_fmt = QTextCharFormat()
            label_fmt.setFontWeight(700)
            label_fmt.setForeground(QColor(COLORS['console_text']))
            cursor.insertText(label, label_fmt)

            if value.strip():
                val_fmt = QTextCharFormat()
                val_fmt.setForeground(QColor(COLORS['console_info']))
                cursor.insertText(f" {value.strip()}", val_fmt)
            cursor.insertText('\n', plain)
        else:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(COLORS['console_text']))
            # Indent sub-lines (e.g. "  2 drive(s)")
            clean = text.rstrip('\n')
            if clean.strip():
                cursor.insertText(f"  {clean.strip()}\n", fmt)

        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()

    def _show_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self._log)

        copy_act = QAction("Copy Selection", menu)
        copy_act.triggered.connect(self._log.copy)
        copy_act.setEnabled(self._log.textCursor().hasSelection())
        menu.addAction(copy_act)

        copy_all = QAction("Copy All", menu)
        copy_all.triggered.connect(self._copy_all)
        menu.addAction(copy_all)

        menu.addSeparator()

        select_all = QAction("Select All", menu)
        select_all.triggered.connect(self._log.selectAll)
        menu.addAction(select_all)

        clear_act = QAction("Clear", menu)
        clear_act.triggered.connect(self._log.clear)
        menu.addAction(clear_act)

        menu.exec(self._log.mapToGlobal(pos))

    def _copy_all(self):
        text = self._log.toPlainText()
        QApplication.clipboard().setText(text)


# ─── System Info Panel ────────────────────────────────────────────────

class SystemInfoPanel(QWidget):
    """Left-side panel: hardware sections and action button."""

    preview_requested = Signal()           # user clicked "Upload to RepairDesk"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._specs = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 16, 0)
        outer.setSpacing(8)

        # ── Spinner (shown during collection) ─────────────────────
        self._spinner = SpinnerWidget()
        self._spinner.hide()
        outer.addWidget(self._spinner)

        # ── Scrollable sections ───────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        self._sections_layout = QVBoxLayout(scroll_content)
        self._sections_layout.setContentsMargins(0, 0, 4, 0)
        self._sections_layout.setSpacing(4)

        self._build_sections()

        self._sections_layout.addStretch()
        scroll.setWidget(scroll_content)
        outer.addWidget(scroll, 1)

        # ── Action button ─────────────────────────────────────────
        self._action_btn = QPushButton("Scan Summary / Upload")
        self._action_btn.setObjectName("primary")
        self._action_btn.setEnabled(False)
        self._action_btn.setCursor(Qt.PointingHandCursor)
        self._action_btn.setFixedHeight(48)
        self._action_btn.clicked.connect(self.preview_requested.emit)
        self._action_btn.setContentsMargins(0, 8, 0, 0)
        outer.addSpacing(12)
        outer.addWidget(self._action_btn)

    # ── Public API ────────────────────────────────────────────────

    @property
    def spinner(self):
        return self._spinner

    def set_button_text(self, text):
        self._action_btn.setText(text)

    def set_button_enabled(self, enabled):
        self._action_btn.setEnabled(enabled)
        btn_base = ("border: none; border-radius: 8px;"
                    "font-weight: bold; font-size: 13px;")
        if enabled:
            self._action_btn.setText("Scan Summary / Upload")
            self._action_btn.setStyleSheet(
                f"background-color: {COLORS['primary']}; color: #FFFFFF;"
                f"{btn_base}")
        else:
            self._action_btn.setStyleSheet(
                f"background-color: #444444; color: #888888;"
                f"{btn_base}")

    def show_scan_skipped(self):
        """Render all sections in a clear idle state after the tech skips scanning."""
        self._specs = {}
        self._spinner.stop()
        self._spinner.hide()

        static_sections = (
            self._sec_overview,
            self._sec_cpu,
            self._sec_ram,
            self._sec_gpu,
            self._sec_mobo,
            self._sec_battery,
            self._sec_adv,
        )
        for section in static_sections:
            section.clear_dynamic()
            for row_name in section._static_rows:
                section.set_row_value(row_name, "Test skipped", COLORS['warning'])

        dynamic_sections = (
            self._sec_storage,
            self._sec_network,
            self._sec_monitors,
        )
        for section in dynamic_sections:
            section.clear_dynamic()
            section.add_info_row('Status', 'Test skipped', bold=True,
                                 color=COLORS['warning'])

    # Map spec keys → section updater methods
    _SECTION_KEYS = {
        '_update_system_overview': {
            'OS', 'SystemType', 'LaptopModel', 'DesktopType', 'SerialNumber',
            'WindowsDetails', 'SystemHealth', 'HPSpecific', 'ScreenSize'},
        '_update_cpu': {'CPU', 'CPUDetails', 'AdvancedHealth', 'cpu_load_temp'},
        '_update_ram': {'RAM', 'RAMDetails', 'RAMCompatibilityWarnings'},
        '_update_gpu': {'GPU', 'GPUDetails', 'GPUMetrics'},
        '_update_mobo': {
            'Motherboard', 'Chipset', 'ChipsetSpecs', 'MotherboardSpecs',
            'BIOS', 'BIOSDetails'},
        '_update_storage': {'Storage', 'StorageHealth'},
        '_update_network': {'Network'},
        '_update_monitors': {'Display', 'PanelDetails', 'AdvancedHealth'},
        '_update_battery': {'Battery', 'BatteryDetails'},
        '_update_advanced': {
            'AdvancedDiagnostics', 'AdvancedHealth', 'DeviceManagerErrors'},
    }

    _ALL_UPDATERS = (
        '_update_system_overview', '_update_cpu', '_update_ram',
        '_update_gpu', '_update_mobo', '_update_storage',
        '_update_network', '_update_monitors', '_update_battery',
        '_update_advanced',
    )

    def update_from_specs(self, specs):
        """Populate sections from the collected specs dict.

        Only rebuilds sections whose relevant keys have changed since the
        last call, avoiding flicker from redundant clear/rebuild cycles.
        """
        old = self._specs or {}
        new_keys = set(specs.keys())
        changed_keys = {k for k in new_keys if specs.get(k) != old.get(k)}

        self._specs = specs

        # On first call or if nothing to compare, update everything
        if not old or not changed_keys:
            for m in self._ALL_UPDATERS:
                getattr(self, m)(specs)
            return

        # Only rebuild sections whose data actually changed
        for method_name, keys in self._SECTION_KEYS.items():
            if changed_keys & keys:
                getattr(self, method_name)(specs)

    def update_gpu_metrics(self, metrics):
        """Update GPU live metrics from monitor thread."""
        sec = self._sec_gpu
        if metrics.get('temperature'):
            temp = metrics['temperature']
            color = COLORS['error'] if temp > 80 else (
                COLORS['warning'] if temp > 70 else None)
            sec.set_row_value('gpu_temp', f"{temp}\u00b0C", color)
        if metrics.get('core_clock'):
            sec.set_row_value('gpu_core_clock',
                              f"{metrics['core_clock']} MHz")
        if metrics.get('memory_clock'):
            sec.set_row_value('gpu_mem_clock',
                              f"{metrics['memory_clock']} MHz")
        if metrics.get('power_draw'):
            sec.set_row_value('gpu_power',
                              f"{metrics['power_draw']:.1f} W")
        if metrics.get('utilization') is not None:
            sec.set_row_value('gpu_util',
                              f"{metrics['utilization']}%")

    # ── Section builders ──────────────────────────────────────────

    def _build_sections(self):
        L = self._sections_layout

        # Section 1: System Overview
        self._sec_overview = InfoSection("System Overview")
        self._sec_overview.add_static_row('system_type', 'System Type', bold=True,
                                          color=COLORS['info'])
        self._sec_overview.add_static_row('model', 'Model', bold=True)
        self._sec_overview.add_static_row('serial', 'Serial Number', bold=True)
        self._sec_overview.add_static_row('windows', 'Windows', bold=True)
        self._sec_overview.add_static_row('health', 'Runtime Health', bold=True)
        L.addWidget(self._sec_overview)

        # Section 2: CPU
        self._sec_cpu = InfoSection("CPU")
        self._sec_cpu.add_static_row('cpu', 'Processor', bold=True)
        L.addWidget(self._sec_cpu)

        # Section 3: RAM
        self._sec_ram = InfoSection("RAM")
        self._sec_ram.add_static_row('ram', 'Memory', bold=True)
        L.addWidget(self._sec_ram)

        # Section 4: GPU
        self._sec_gpu = InfoSection("GPU")
        self._sec_gpu.add_static_row('gpu', 'Graphics', bold=True)
        L.addWidget(self._sec_gpu)

        # Section 5: Motherboard & BIOS
        self._sec_mobo = InfoSection("Motherboard & BIOS")
        self._sec_mobo.add_static_row('mobo', 'Motherboard', bold=True)
        self._sec_mobo.add_static_row('bios', 'BIOS', bold=True)
        L.addWidget(self._sec_mobo)

        # Storage (fully dynamic)
        self._sec_storage = InfoSection("Storage")
        L.addWidget(self._sec_storage)

        # Network (fully dynamic)
        self._sec_network = InfoSection("Network & Peripherals")
        L.addWidget(self._sec_network)

        # Monitors (fully dynamic — populated after scan)
        self._sec_monitors = InfoSection("Monitors")
        L.addWidget(self._sec_monitors)

        # Battery
        self._sec_battery = InfoSection("Battery")
        self._sec_battery.add_static_row('battery_status', 'Status', bold=True)
        L.addWidget(self._sec_battery)

        # Advanced Diagnostics
        self._sec_adv = InfoSection("Advanced Diagnostics")
        self._sec_adv.add_static_row('event_log', 'Event Log (7 days)')
        self._sec_adv.add_static_row('win_update', 'Windows Update')
        self._sec_adv.add_static_row('defender', 'Defender')
        self._sec_adv.add_static_row('startup', 'Startup Items')
        self._sec_adv.add_static_row('dev_mgr', 'Device Manager')
        self._sec_adv.add_static_row('power_plan', 'Active Power Plan')
        self._sec_adv.add_static_row('boot_time', 'Uptime')
        L.addWidget(self._sec_adv)

    # ── Section updaters ──────────────────────────────────────────

    def _update_system_overview(self, specs):
        sec = self._sec_overview
        sec.clear_dynamic()

        # System Type
        sec.set_row_value('system_type', specs.get('SystemType', 'Unknown'),
                          COLORS['info'])

        # Model
        system_model = specs.get('LaptopModel', 'Not Available')
        screen_size = specs.get('ScreenSize')
        desktop_type = specs.get('DesktopType')

        if system_model != 'Not Available':
            model_text = system_model
            if screen_size:
                model_text += f" ({screen_size})"
            sec.set_row_value('model', model_text)
        elif desktop_type == 'Custom Build':
            sec.set_row_value('model', 'Custom Build', COLORS['primary'])
        else:
            sec.set_row_value('model', 'N/A', COLORS['text_tertiary'])

        # OEM details (Product Family / SKU)
        hp_specific = specs.get('HPSpecific', {})
        if hp_specific:
            family = hp_specific.get('system_family', '').strip()
            if family:
                sec.add_info_row('Product Family', family)
            sku = hp_specific.get('system_sku', '').strip()
            if sku and not re.match(r'^[0\s]+$', sku):
                sec.add_info_row('Product SKU', sku)

        # Serial Number
        serial = specs.get('SerialNumber', 'N/A')
        sec.set_row_value('serial', serial if serial and serial != 'N/A'
                          else 'Not detected')

        # Windows
        windows_details = specs.get('WindowsDetails', 'Unknown')
        if isinstance(windows_details, str) and windows_details not in (
                'Unknown', 'Windows details unavailable'):
            details_dict = {}
            os_name = 'Windows'
            for part in windows_details.split(', '):
                if ':' in part:
                    k, v = part.split(':', 1)
                    details_dict[k.strip()] = v.strip()
                    if k.strip() == 'Edition':
                        os_name = v.strip()
            sec.set_row_value('windows', os_name)
            for key in ('Version', 'Build', 'Installed', 'Last Boot'):
                if key in details_dict:
                    sec.add_info_row(key, details_dict[key])
        else:
            sec.set_row_value('windows', str(windows_details) if windows_details
                              else 'Unknown')

        # Runtime Health
        system_health = specs.get('SystemHealth', 'Unknown')
        if isinstance(system_health, list) and system_health:
            sec.set_row_value('health', system_health[0])
            for metric in system_health[1:]:
                sec.add_detail_row(metric)
        else:
            sec.set_row_value('health', str(system_health) if system_health
                              else 'Unknown')

    def _update_cpu(self, specs):
        sec = self._sec_cpu
        sec.clear_dynamic()

        cpu_full = specs.get('CPU', 'Unknown')
        cpu_name_match = re.match(r'^(.*?)\s*(?:\|\s*Base:|$)', cpu_full)
        cpu_name = cpu_name_match.group(1).strip() if cpu_name_match else cpu_full
        sec.set_row_value('cpu', cpu_name)

        # Clock speeds
        base_match = re.search(r'Base:\s*([\d.]+)\s*GHz', cpu_full)
        boost_match = (re.search(r'Boost:\s*([\d.]+)\s*GHz', cpu_full) or
                       re.search(r'Turbo:\s*([\d.]+)\s*GHz', cpu_full))
        base_clock = f"{base_match.group(1)} GHz" if base_match else None
        boost_clock = f"{boost_match.group(1)} GHz" if boost_match else None

        if not base_clock and not boost_clock:
            pair = re.search(r'([\d.]+)\s*GHz\s*/\s*([\d.]+)\s*GHz', cpu_full)
            if pair:
                base_clock = f"{pair.group(1)} GHz"
                boost_clock = f"{pair.group(2)} GHz"

        if base_clock or boost_clock:
            parts = [p for p in (base_clock, boost_clock) if p]
            sec.add_info_row('Base / Boost Clock', ' / '.join(parts))

        # Cores / Threads
        ct = re.search(r'\((\d+)C/(\d+)T\)', cpu_full)
        if ct:
            sec.add_info_row('Cores / Threads',
                             f"{ct.group(1)} Cores / {ct.group(2)} Threads")

        # CPU extended details
        cpu_details = specs.get('CPUDetails')
        if cpu_details:
            if cpu_details.get('generation') and cpu_details.get('architecture'):
                gen_text = f"{cpu_details['generation']} ({cpu_details['architecture']})"
                if cpu_details.get('year') and cpu_details.get('age_years') is not None:
                    gen_text += f" - Released {cpu_details['year']}"
                    age = cpu_details['age_years']
                    if age > 0:
                        gen_text += f" ({age} year{'s' if age != 1 else ''} old)"
                sec.add_info_row('Generation', gen_text)

            if cpu_details.get('socket'):
                sec.add_info_row('Socket', cpu_details['socket'])
            if cpu_details.get('tdp'):
                sec.add_info_row('TDP', f"{cpu_details['tdp']}W")

            # CPU Temperature — idle
            advanced = specs.get('AdvancedHealth', {})
            temps = advanced.get('temperatures', {})
            if temps.get('status') == 'ok' and temps.get('cpu_temp_c'):
                cpu_temp = temps['cpu_temp_c']
                if cpu_temp < 60:
                    color, label = COLORS['success'], '(Normal)'
                elif cpu_temp < 80:
                    color, label = COLORS['warning'], '(Warm)'
                else:
                    color, label = COLORS['error'], '(Hot)'
                sec.add_info_row('Temp — Idle', f"{cpu_temp:.0f}\u00b0C {label}",
                                 color=color)

            # CPU Temperature — under load
            load_temp = advanced.get('cpu_load_temp', {})
            if load_temp.get('status') == 'ok' and load_temp.get('peak_temp_c'):
                peak = load_temp['peak_temp_c']
                aborted = load_temp.get('aborted', False)
                if peak < 75:
                    color, label = COLORS['success'], '(Normal)'
                elif peak < 90:
                    color, label = COLORS['warning'], '(Warm)'
                else:
                    color, label = COLORS['error'], '(Hot)'
                suffix = ' — thermal limit hit!' if aborted else ''
                sec.add_info_row('Temp — Load',
                                 f"{peak:.0f}\u00b0C {label}{suffix}",
                                 color=color)

            if cpu_details.get('windows_compatibility'):
                wc = cpu_details['windows_compatibility']
                color = COLORS['success'] if 'Windows 11' in wc else COLORS['warning']
                sec.add_info_row('Windows Compat', wc, color=color)

            if cpu_details.get('upgrade_path'):
                upgrades = ', '.join(cpu_details['upgrade_path'][:3])
                sec.add_info_row('Upgrade options', upgrades)

    def _update_ram(self, specs):
        sec = self._sec_ram
        sec.clear_dynamic()

        sec.set_row_value('ram', specs.get('RAM', 'Unknown'))

        for module in specs.get('RAMDetails', []):
            slot = module.get('slot', 'Unknown')
            size = int(module.get('size_gb', 0))
            speed = module.get('configured_speed', 0)
            mfr = module.get('manufacturer', 'Unknown')
            pn = module.get('part_number', '')
            val = f"{size}GB @ {speed}MHz - {mfr}"
            if pn and pn != mfr:
                val += f" {pn}"
            sec.add_info_row(slot, val)

        # RAM Compatibility Warnings
        for obs in specs.get('RAMCompatibilityWarnings', []):
            level = obs.get('level', 'INFO')
            color = (COLORS['error'] if level == 'CRITICAL' else
                     COLORS['warning'] if level == 'WARNING' else None)
            label = obs.get('label', '')
            value = obs.get('value', '')
            message = obs.get('message', '')
            if label and value:
                sec.add_info_row(label, value, color=color)
            elif message:
                sec.add_detail_row(message, color=color)

    def _update_gpu(self, specs):
        sec = self._sec_gpu
        sec.clear_dynamic()

        gpu_full = specs.get('GPU', 'Unknown')
        gpu_name_match = re.match(r'^(.*?)\s*(?:\(|-).*', gpu_full)
        gpu_name = gpu_name_match.group(1).strip() if gpu_name_match else gpu_full
        sec.set_row_value('gpu', gpu_name)

        vram_match = re.search(r'\(([\d.]+)\s*GB\s*VRAM\)', gpu_full)
        if vram_match:
            sec.add_info_row('VRAM', f"{vram_match.group(1)} GB")

        driver_match = re.search(r'Driver:\s*([\d.]+)', gpu_full)
        driver_date_match = re.search(r'\((\d{2}/\d{2}/\d{4})\)', gpu_full)
        if driver_match:
            drv = driver_match.group(1)
            if driver_date_match:
                drv += f" ({driver_date_match.group(1)})"
            sec.add_info_row('Driver Version', drv)

        # GPU live metrics — add static rows once, then just update values
        gpu_metrics = specs.get('GPUMetrics', {})
        if gpu_metrics:
            if 'gpu_temp' not in sec._static_rows:
                sec.add_static_row('gpu_temp', 'Temperature')
                sec.add_static_row('gpu_core_clock', 'Core Clock')
                sec.add_static_row('gpu_mem_clock', 'Memory Clock')
                sec.add_static_row('gpu_power', 'Power Draw')
                sec.add_static_row('gpu_util', 'GPU Load')

            if gpu_metrics.get('temperature'):
                temp = gpu_metrics['temperature']
                color = (COLORS['error'] if temp > 80 else
                         COLORS['warning'] if temp > 70 else None)
                sec.set_row_value('gpu_temp', f"{temp}\u00b0C", color)
            if gpu_metrics.get('core_clock'):
                sec.set_row_value('gpu_core_clock',
                                  f"{gpu_metrics['core_clock']} MHz")
            if gpu_metrics.get('memory_clock'):
                sec.set_row_value('gpu_mem_clock',
                                  f"{gpu_metrics['memory_clock']} MHz")
            if gpu_metrics.get('power_draw'):
                sec.set_row_value('gpu_power',
                                  f"{gpu_metrics['power_draw']:.1f} W")
            if gpu_metrics.get('utilization') is not None:
                sec.set_row_value('gpu_util',
                                  f"{gpu_metrics['utilization']}%")

    def _update_mobo(self, specs):
        sec = self._sec_mobo
        sec.clear_dynamic()

        sec.set_row_value('mobo', specs.get('Motherboard', 'Unknown'))

        chipset = specs.get('Chipset', '')
        mobo_specs = specs.get('MotherboardSpecs')
        if chipset:
            sec.add_info_row('Chipset', chipset)

        if mobo_specs:
            max_ram = (f"{mobo_specs['max_ram']} ({mobo_specs['max_per_dimm']} "
                       f"per slot \u00d7 {mobo_specs['dimm_slots']} slots)")
            sec.add_info_row('Max RAM', max_ram, color=COLORS['success'])
            sec.add_info_row('Speeds', mobo_specs['supported_speeds'])
        elif chipset:
            chipset_specs = specs.get('ChipsetSpecs')
            if chipset_specs:
                sec.add_detail_row(
                    f"Platform: {chipset_specs.get('memory_type', 'N/A')} "
                    f"({chipset_specs.get('channels', 'N/A')})")
                sec.add_detail_row(
                    "Max RAM: Unknown (board not in database "
                    "- verify manufacturer specs)",
                    color=COLORS['warning'])

        # BIOS
        sec.set_row_value('bios', specs.get('BIOS', 'Unknown'))
        for detail_line in specs.get('BIOSDetails', []):
            if ':' in detail_line:
                k, v = detail_line.split(':', 1)
                sec.add_info_row(k.strip(), v.strip())

    @staticmethod
    def _detect_drive_type(model, health_data):
        """Determine drive type from model name and SMART data."""
        m = model.upper()
        # NVMe — model name or SMART attributes
        if 'NVME' in m or 'NVM' in m:
            return 'NVMe SSD'
        if health_data and health_data.get('available_spare') is not None:
            return 'NVMe SSD'
        # USB / Removable
        if 'USB' in m:
            return 'USB'
        if health_data and health_data.get('status') == 'N/A':
            return 'USB'
        # Explicit SSD in name
        if 'SSD' in m:
            return 'SATA SSD'
        # Known SSD model substrings (WMI often prepends manufacturer)
        ssd_patterns = (
            'WDS',     # WD Blue/Green SSD
            'MZVL', 'MZ7', 'MZ-',  # Samsung SSD
            'SSDPE', 'SSDSC',  # Intel SSD
            'SA400', 'A2000', 'SNV',  # Kingston SSD
            'SPCC',    # Silicon Power SSD
        )
        for pattern in ssd_patterns:
            if pattern in m:
                return 'SATA SSD'
        # SMART: has percentage_used → SSD
        if health_data and health_data.get('percentage_used') is not None:
            return 'SSD'
        # Has reallocated sectors → SATA (could be HDD or SSD)
        if health_data and health_data.get('reallocated_sectors') is not None:
            # Could be HDD or SATA SSD — check read speed if available
            return 'SATA'
        return None

    def _update_storage(self, specs):
        sec = self._sec_storage
        sec.clear_dynamic()

        storage_info = specs.get('Storage', 'Unknown')
        storage_health = specs.get('StorageHealth', [])

        # Parse storage lines
        storage_lines = []
        if storage_info and isinstance(storage_info, str):
            storage_lines = [l.strip() for l in storage_info.split('\n')
                             if l.strip()]

        # Map drive letters to info
        drive_mapping = {}
        for line in storage_lines:
            m = re.match(r'Drive\s+([A-Z]):', line, re.IGNORECASE)
            if m:
                letter = m.group(1).upper()
                info = re.sub(r'^Drive\s+[A-Z]:\s*', '', line,
                              flags=re.IGNORECASE).strip()
                drive_mapping[letter] = {'info': info, 'health': None}

        # Match health to drives
        for health_item in storage_health:
            model = health_item.get('model', '')
            for letter, data in drive_mapping.items():
                if model and model in data['info']:
                    data['health'] = health_item
                    break

        # Display each drive
        for i, (letter, data) in enumerate(sorted(drive_mapping.items())):
            if i > 0:
                sec.add_group_gap()

            info = data['info']
            health = data['health']

            # Drive name — strip WMI media type, detect real type
            model_match = re.search(r'-\s*(.+)$', info)
            raw_name = model_match.group(1).strip() if model_match else info
            # Strip "(Fixed hard disk media)", "(Removable Media)", etc.
            clean_name = re.sub(r'\s*\([^)]*media[^)]*\)', '', raw_name,
                                flags=re.IGNORECASE).strip()
            drive_type = self._detect_drive_type(clean_name, health)
            # Don't append type if it's already obvious from the name
            if drive_type and drive_type.upper() not in clean_name.upper():
                display_name = f"{clean_name} ({drive_type})"
            else:
                display_name = clean_name
            sec.add_info_row(f'Drive {letter}', display_name, bold=True)

            # Capacity
            cap = info.split(' - ')[0] if ' - ' in info else info
            sec.add_info_row('Capacity', cap)

            # Health
            if health:
                hp = health.get('health_percent')
                interp = health.get('interpretation', {})
                if hp is not None:
                    score = interp.get('score', hp)
                    grade = interp.get('grade', 'N/A')
                    sec.add_info_row('Health', f"{score}/100 ({grade})")
                elif health.get('status') == 'N/A':
                    sec.add_info_row('Health', 'N/A (USB)',
                                     color=COLORS['text_tertiary'])

                # SMART data
                self._display_smart_rows(sec, health)

            # Disk speed (C: only)
            if letter == 'C':
                adv = specs.get('AdvancedHealth', {})
                ds = adv.get('disk_speed', {})
                if ds.get('status') == 'ok':
                    rd = ds.get('read_mb_s', 0)
                    wr = ds.get('write_mb_s', 0)
                    if rd > 2000:
                        cat, col = 'NVMe', COLORS['success']
                    elif rd > 400:
                        cat, col = 'SATA SSD', COLORS['info']
                    else:
                        cat, col = 'HDD/Slow', COLORS['warning']
                    sec.add_info_row(
                        'Disk Speed',
                        f"{rd:.0f}MB/s read, {wr:.0f}MB/s write ({cat})",
                        color=col)

    def _display_smart_rows(self, sec, health_info):
        """Add SMART attribute rows to a section."""
        if not health_info or health_info.get('status') == 'N/A':
            return

        def _row(label, value, color=None):
            sec.add_info_row(label, value, color=color)

        # Partition style
        ps = health_info.get('partition_style')
        if ps:
            style_note = {
                'GPT': 'GPT',
                'MBR': 'MBR — legacy (no Secure Boot)',
                'RAW': 'RAW — unformatted',
            }.get(ps, ps)
            _row('Partition Style', style_note,
                 COLORS['warning'] if ps == 'MBR' else
                 COLORS['error'] if ps == 'RAW' else None)

        poh = health_info.get('power_on_hours')
        if poh is not None:
            _row('Runtime', f"{poh:,}h")

        pc = health_info.get('power_cycles')
        if pc is not None:
            _row('Power Cycles', f"{pc:,}")

        temp = health_info.get('temperature')
        if temp is not None:
            _row('Temperature', f"{temp}\u00b0C")

        pu = health_info.get('percentage_used')
        if pu is not None:
            _row('Wear Level', f"{pu}%")

        avs = health_info.get('available_spare')
        if avs is not None:
            _row('Available Spare', f"{avs}%")

        rs = health_info.get('reallocated_sectors')
        if rs is not None:
            _row('Reallocated Sectors', str(rs),
                 COLORS['warning'] if rs > 0 else None)

        ps = health_info.get('pending_sectors')
        if ps and ps > 0:
            _row('Pending Sectors', f"\u26a0 {ps}", COLORS['warning'])

        ue = health_info.get('uncorrectable_errors')
        if ue and ue > 0:
            _row('Uncorrectable Errors', f"\u26a0 {ue}", COLORS['warning'])

        dur = health_info.get('data_units_read')
        if dur is not None:
            _row('Data Read', f"{dur:.1f} TB")

        duw = health_info.get('data_units_written')
        if duw is not None:
            _row('Data Written', f"{duw:.1f} TB")

        us = health_info.get('unsafe_shutdowns')
        if us and us > 0:
            _row('Unsafe Shutdowns', f"{us:,}", COLORS['warning'])

        me = health_info.get('media_errors')
        if me and me > 0:
            _row('Media Errors', f"\u26a0 {me}", COLORS['warning'])

    def _update_network(self, specs):
        sec = self._sec_network
        sec.clear_dynamic()

        network_info = specs.get('Network', 'Unknown')
        if not isinstance(network_info, str) or network_info in (
                'Unknown', 'No network adapters found',
                'Network information unavailable'):
            sec.add_detail_row(str(network_info))
        else:
            adapters = [l.strip() for l in network_info.split('\n') if l.strip()]
            for i, line in enumerate(adapters):
                if i > 0:
                    sec.add_group_gap()

                parts = line.split(' - MAC: ')
                adapter_name = parts[0].strip()
                mac_and_rest = parts[1].strip() if len(parts) > 1 else None

                sec.add_info_row('Adapter', adapter_name, bold=True)

                mac_address = speed_info = driver_info = None
                if mac_and_rest:
                    if ' | Driver: ' in mac_and_rest:
                        mac_and_speed, driver_info = mac_and_rest.split(
                            ' | Driver: ', 1)
                    else:
                        mac_and_speed = mac_and_rest

                    if '(' in mac_and_speed:
                        mac_address = mac_and_speed.split('(')[0].strip()
                        speed_info = mac_and_speed.split('(')[1].rstrip(')')
                    else:
                        mac_address = mac_and_speed

                if mac_address:
                    sec.add_info_row('MAC Address', mac_address)
                if speed_info:
                    sec.add_info_row('Link Speed', speed_info)
                if driver_info:
                    sec.add_info_row('Driver', driver_info.strip())

        # WiFi diagnostics (from AdvancedHealth)
        advanced = specs.get('AdvancedHealth', {})
        wifi = advanced.get('wifi', {})
        wifi_status = wifi.get('status')
        if wifi_status == 'ok':
            sec.add_group_gap()
            adapter = wifi.get('adapter_name', '')
            gen = wifi.get('wifi_gen', wifi.get('radio_type', ''))
            signal = wifi.get('signal_pct')
            rx = wifi.get('rx_rate_mbps')
            tx = wifi.get('tx_rate_mbps')
            ssid = wifi.get('ssid', '')
            if adapter:
                sec.add_info_row('WiFi Adapter', adapter, bold=True)
            if gen:
                sec.add_info_row('WiFi Standard', gen)
            if ssid:
                sec.add_info_row('SSID', ssid)
            if signal is not None:
                if signal < 20:
                    sig_col, sig_label = COLORS['error'], f'{signal}% — ⚠ Very Weak'
                elif signal < 40:
                    sig_col, sig_label = COLORS['warning'], f'{signal}% — ⚠ Weak'
                else:
                    sig_col, sig_label = COLORS['success'], f'{signal}%'
                sec.add_info_row('WiFi Signal', sig_label, color=sig_col)
            if rx and tx:
                sec.add_info_row('Link Speed', f'{rx:.0f} / {tx:.0f} Mbps (RX/TX)')
        elif wifi_status == 'disconnected':
            sec.add_group_gap()
            sec.add_info_row('WiFi', '⚠ Adapter present but not connected',
                             color=COLORS['warning'])
        elif wifi_status == 'permission_required':
            sec.add_group_gap()
            sec.add_info_row('WiFi', 'Windows Location permission required to read WiFi details',
                             color=COLORS['warning'])
        elif wifi_status == 'no_adapter' and specs.get('SystemType') == 'Laptop':
            sec.add_group_gap()
            sec.add_info_row('WiFi', 'No wireless adapter detected',
                             color=COLORS['warning'])

    def _update_monitors(self, specs):
        sec = self._sec_monitors
        sec.clear_dynamic()

        display_info = specs.get('Display', 'Unknown')
        system_type = specs.get('SystemType', 'Unknown')
        screen_size = specs.get('ScreenSize')
        panel_details = specs.get('PanelDetails')
        is_laptop = system_type == 'Laptop'

        # Parse external monitors
        external = []
        if isinstance(display_info, str) and display_info not in (
                'Unknown', 'Display information unavailable'):
            external = [l.strip() for l in display_info.split('\n')
                        if l.strip()]

        if is_laptop:
            # Built-in panel
            if panel_details and isinstance(panel_details, dict):
                disp_text = ''
                if panel_details.get('size_inches'):
                    disp_text = f"{panel_details['size_inches']}\""
                if panel_details.get('resolution_v'):
                    rv = panel_details['resolution_v']
                    if rv >= 2160:
                        disp_text += ' 4K UHD'
                    elif rv >= 1440:
                        disp_text += ' QHD'
                    elif rv >= 1080:
                        disp_text += ' FHD'
                    elif rv >= 768:
                        disp_text += ' HD'
                if panel_details.get('is_touch') is True:
                    disp_text += ' (Touch)'
                sec.add_info_row('Built-in Panel', disp_text or screen_size or 'Unknown', bold=True)
            else:
                sec.add_info_row('Built-in Panel', screen_size or 'Unknown', bold=True)

            # Panel detail rows
            if panel_details and isinstance(panel_details, dict):
                if panel_details.get('manufacturer'):
                    sec.add_info_row('Panel Manufacturer',
                                     panel_details['manufacturer'])
                if panel_details.get('model_code'):
                    sec.add_info_row('Panel Model',
                                     panel_details['model_code'])

                rh = panel_details.get('resolution_h')
                rv = panel_details.get('resolution_v')
                if rh and rv:
                    res = f"{rh}x{rv}"
                    if rv >= 2160:
                        res += ' (4K UHD)'
                    elif rv >= 1440:
                        res += ' (QHD)'
                    elif rv >= 1080:
                        res += ' (Full HD)'
                    elif rv >= 768:
                        res += ' (HD)'
                    sec.add_info_row('Native Resolution', res)

                if 'is_touch' in panel_details:
                    it = panel_details.get('is_touch')
                    if it is True:
                        txt, col = 'Yes (Hardware detected)', COLORS['success']
                    elif it is False:
                        txt, col = 'No (Standard LCD)', None
                    else:
                        txt, col = 'Unknown', COLORS['warning']
                    sec.add_info_row('Touch Screen', txt, color=col)

                ch = panel_details.get('size_cm_h')
                cv = panel_details.get('size_cm_v')
                if ch and cv:
                    size = f"{ch}cm \u00d7 {cv}cm"
                    si_exact = panel_details.get('size_inches_exact')
                    si = panel_details.get('size_inches')
                    if si_exact and si and si_exact != si:
                        size += f" ({si_exact}\" exact)"
                    sec.add_info_row('Physical Size', size)

                if panel_details.get('connection_type'):
                    sec.add_info_row('Connection',
                                     panel_details['connection_type'])

                if panel_details.get('manufacture_year'):
                    mfg = str(panel_details['manufacture_year'])
                    if panel_details.get('manufacture_week'):
                        mfg += f" (Week {panel_details['manufacture_week']})"
                    sec.add_info_row('Manufactured', mfg)

            # External monitors (laptop)
            if external:
                sec.add_group_gap()
                for i, mon in enumerate(external):
                    txt = re.sub(r'^Display\s+\d+\s*', '', mon).strip()
                    sec.add_info_row(f'External {i + 1}', txt, bold=True)
        else:
            # Desktop — show all connected displays
            if external:
                for i, mon in enumerate(external):
                    if i > 0:
                        sec.add_group_gap()
                    txt = re.sub(r'^Display\s+\d+\s*', '', mon).strip()
                    sec.add_info_row(f'Display {i + 1}', txt, bold=True)
            else:
                sec.add_info_row('Display', 'No display detected')

        # Webcam — shown in display section for all machine types
        advanced = specs.get('AdvancedHealth', {})
        webcam = advanced.get('webcam', {})
        webcam_status = webcam.get('status')
        if webcam_status:
            sec.add_group_gap()
            cams = webcam.get('cameras', [])
            cam_name = cams[0].get('name', 'Unknown') if cams else 'Unknown'
            if webcam_status == 'ok':
                res = webcam.get('resolution_test', '')
                label = f"✓ Functional — {cam_name}" + (f" ({res})" if res else "")
                sec.add_info_row('Webcam', label, color=COLORS['success'])
            elif webcam_status == 'detected_not_functional':
                sec.add_info_row('Webcam', f"⚠ Not responding — {cam_name}", color=COLORS['warning'])
            elif webcam_status == 'detected':
                sec.add_info_row('Webcam', f"Detected — {cam_name}", color=COLORS['text_secondary'])
            elif webcam_status == 'none':
                sec.add_info_row('Webcam', 'No camera detected', color=COLORS['text_secondary'])

    def _update_battery(self, specs):
        sec = self._sec_battery
        sec.clear_dynamic()

        sec.set_row_value('battery_status',
                          specs.get('Battery', 'Unknown'))

        bd = specs.get('BatteryDetails')
        if not bd or not isinstance(bd, dict):
            return

        # Model
        model_name = (bd.get('model_name', '') or '').strip()
        manufacturer = (bd.get('manufacturer', '') or '').strip()
        generic = ('Primary', 'Microsoft', 'Unknown', '-', 'N/A',
                   'Not Available')
        if model_name in generic:
            model_name = ''

        if model_name or manufacturer:
            bat_id = model_name
            if manufacturer:
                bat_id += f" by {manufacturer}" if bat_id else manufacturer
            if not model_name and manufacturer:
                bat_id = f"Battery by {manufacturer}"
            sec.add_info_row('Model', bat_id)

        # Serial
        serial = (bd.get('serial_number', '') or '').strip()
        if serial and serial not in ('-', '', 'N/A', 'Unknown', '0',
                                     'Not Available'):
            sec.add_info_row('Serial Number', serial)

        if bd.get('chemistry'):
            sec.add_info_row('Chemistry', bd['chemistry'])

        # Capacities
        if bd.get('design_capacity_wh'):
            wh = bd['design_capacity_wh']
            mah = bd.get('design_capacity_mah', 0)
            sec.add_info_row('Design Capacity', f"{wh}Wh ({mah:,} mAh)")

        if bd.get('full_charge_capacity_wh'):
            fwh = bd['full_charge_capacity_wh']
            fmah = bd.get('full_charge_capacity_mah', 0)
            dwh = bd.get('design_capacity_wh')
            txt = f"{fwh}Wh ({fmah:,} mAh)"
            if dwh and abs(fwh - dwh) > 0.1:
                txt += f" - {dwh}Wh when new"
            sec.add_info_row('Full Charge Capacity', txt)

        # Current status
        if bd.get('status') or bd.get('charge_percent') is not None:
            status_text = ''
            color = None
            if bd.get('charge_percent') is not None:
                status_text = f"{bd['charge_percent']}%"
            if bd.get('status'):
                s = bd['status']
                if status_text:
                    status_text += f" - {s}"
                else:
                    status_text = s
                if s in ('Charging', 'Fully Charged'):
                    color = COLORS['success']
                elif s in ('Low', 'Critical'):
                    color = COLORS['error']
                elif s == 'Not Charging':
                    color = COLORS['warning']
            sec.add_info_row('Current Status', status_text, color=color)

        # Power source & real-time readings
        if bd.get('ac_power') is not None:
            source = "AC Power (plugged in)" if bd['ac_power'] else "On Battery"
            src_color = COLORS['success'] if bd['ac_power'] else COLORS['warning']
            sec.add_info_row('Power Source', source, color=src_color)

        if bd.get('charge_rate_w'):
            sec.add_info_row('Charge Rate', f"{bd['charge_rate_w']}W",
                             color=COLORS['success'])
        elif bd.get('discharge_rate_w'):
            sec.add_info_row('Discharge Rate', f"{bd['discharge_rate_w']}W (from battery)",
                             color=COLORS['warning'])

        if bd.get('voltage_v'):
            sec.add_info_row('Voltage', f"{bd['voltage_v']}V")

        if bd.get('estimated_runtime_min') and not bd.get('ac_power'):
            rt = bd['estimated_runtime_min']
            hrs, mins = divmod(rt, 60)
            rt_str = f"{hrs}h {mins}min" if hrs else f"{mins}min"
            col = COLORS['error'] if rt < 30 else (COLORS['warning'] if rt < 60 else None)
            sec.add_info_row('Est. Runtime', f"~{rt_str} remaining", color=col)

        # Health
        if bd.get('health_percent'):
            hp = bd['health_percent']
            if hp >= 85:
                lbl, col = 'Excellent', COLORS['success']
            elif hp >= 70:
                lbl, col = 'Good', COLORS['warning']
            elif hp >= 50:
                lbl, col = 'Fair', COLORS['warning']
            else:
                lbl, col = 'Poor', COLORS['error']
            sec.add_info_row('Battery Health', f"{hp}% ({lbl})", color=col)

        if bd.get('wear_percent'):
            sec.add_info_row('Wear Level', f"{bd['wear_percent']}%")

        # Cycles
        if bd.get('cycle_count'):
            cycles = bd['cycle_count']
            if cycles < 100:
                age = '~3-6 months old'
            elif cycles < 300:
                age = '~6-12 months old'
            elif cycles < 500:
                age = '~1-2 years old'
            elif cycles < 800:
                age = '~2-3 years old'
            elif cycles < 1000:
                age = '~3-4 years old'
            else:
                age = '>4 years old'
            sec.add_info_row('Charge Cycles', f"{cycles} ({age})")

        # Assessment
        if bd.get('health_percent'):
            try:
                from assessments import assess_battery_health
                health = bd['health_percent']
                cycles = bd.get('cycle_count', 0)
                assessment = assess_battery_health(health, cycles)
                text = f"{assessment['label']} - {assessment['description']}"
                color_map = {
                    'success': COLORS['success'],
                    'warning': COLORS['warning'],
                    'error': COLORS['error'],
                    'info': None,
                }
                col = color_map.get(assessment['color_category'])
                sec.add_info_row('Assessment', text, color=col)
            except Exception:
                pass

    def _update_advanced(self, specs):
        sec = self._sec_adv
        advanced = specs.get('AdvancedDiagnostics', {})
        health = specs.get('AdvancedHealth', {})

        color_map = {
            'ok': COLORS['success'],
            'warning': COLORS['warning'],
            'critical': COLORS['error'],
            'unknown': COLORS['text_secondary'],
        }

        # Map AdvancedDiagnostics keys → row names → AdvancedHealth keys
        row_map = (
            ('EventLog',      'event_log',  'event_viewer'),
            ('WindowsUpdate', 'win_update', 'windows_update'),
            ('Defender',      'defender',   'defender'),
            ('StartupItems',  'startup',    'startup_impact'),
            ('DeviceManager', 'dev_mgr',    'device_manager'),
            ('PowerPlan',     'power_plan', 'power_plan'),
            ('BootTime',      'boot_time',  'boot_time'),
        )

        for diag_key, row_name, health_key in row_map:
            data = advanced.get(diag_key, ('Check unavailable', 'unknown'))
            text = data[0] if isinstance(data, (list, tuple)) else str(data)
            status = data[1] if isinstance(data, (list, tuple)) and len(data) > 1 else 'unknown'
            sec.set_row_value(row_name, text, color_map.get(status))

            # Attach detail popup if AdvancedHealth has data for this check
            detail_data = health.get(health_key, {})
            if detail_data and detail_data.get('status') in ('ok', 'warning'):
                row = sec._static_rows.get(row_name)
                if row:
                    row.set_click_handler(
                        lambda hk=health_key, dd=detail_data: self._show_detail(hk, dd))

    def _show_detail(self, health_key, data):
        """Open a detail popup for a specific advanced health check."""
        builders = {
            'event_viewer':   self._detail_event_viewer,
            'windows_update': self._detail_windows_update,
            'defender':       self._detail_defender,
            'startup_impact': self._detail_startup,
            'device_manager': self._detail_device_manager,
            'power_plan':     self._detail_power_plan,
            'boot_time':      self._detail_boot_time,
        }
        builder = builders.get(health_key)
        if builder:
            builder(data)

    # ── Detail popup builders ────────────────────────────────────

    # Known event source interpretations — what they mean for a tech
    _EVENT_SOURCE_INFO = {
        'Application Error': (
            'An application crashed. Common causes: outdated software, '
            'bad drivers, or insufficient RAM.'),
        'Application Hang': (
            'An application froze and stopped responding. Check for '
            'resource exhaustion or software conflicts.'),
        'Windows Error Reporting': (
            'Crash reports were generated. Usually a side-effect of other '
            'errors — check the actual crash sources.'),
        'Microsoft-Windows-DistributedCOM': (
            'DCOM permission errors. Very common and usually harmless — '
            'Windows services failing to communicate internally. '
            'Rarely needs action.'),
        'Microsoft-Windows-Defrag': (
            'Disk defragmentation failed or had issues. On SSDs this can '
            'indicate TRIM problems. On HDDs, check drive health.'),
        'Microsoft-Windows-WindowsUpdateClient': (
            'Windows Update had failures. May indicate pending updates '
            'that need manual intervention or corrupt update cache.'),
        'Microsoft-Windows-Kernel-Power': (
            'Unexpected shutdown or sleep failure. Often caused by power '
            'issues, overheating, or faulty drivers. Check for BSODs.'),
        'Microsoft-Windows-WHEA-Logger': (
            'Hardware error logged by Windows Hardware Error Architecture. '
            'Could indicate CPU, RAM, or PCIe issues. Investigate further.'),
        'Netwtw10': (
            'Intel Wi-Fi driver error. Common with older drivers — '
            'update the Intel wireless driver.'),
        'Netwtw08': (
            'Intel Wi-Fi driver error (older adapter). '
            'Update the Intel wireless driver.'),
        'Ntfs': (
            'NTFS filesystem error. Could indicate disk corruption — '
            'run chkdsk and check SMART data.'),
        'disk': (
            'Low-level disk error. The drive may be failing — '
            'check SMART health urgently.'),
        'Service Control Manager': (
            'A Windows service failed to start. Can be normal '
            '(disabled services) or indicate a broken dependency.'),
        'Schannel': (
            'SSL/TLS protocol error. Usually harmless — caused by '
            'certificate negotiation failures with remote servers.'),
        'ESENT': (
            'Windows database engine error. Common with Windows Search '
            'or Edge. Usually resolved by rebuilding the search index.'),
        'VSS': (
            'Volume Shadow Copy failed. Backup and restore point creation '
            'may be broken. Check disk space and VSS writers.'),
    }

    def _detail_event_viewer(self, data):
        dlg = DetailDialog("Event Log (7 days)", parent=self.window())
        total = data.get('total_events', 0)
        color = COLORS['error'] if total > 10 else (
            COLORS['warning'] if total > 0 else COLORS['success'])
        dlg.add_row("Total Events", str(total), color=color)
        dlg.add_row("Lookback Period", f"{data.get('days_lookback', 7)} days")

        sources = data.get('top_sources', [])
        if sources:
            for src in sources:
                name = src.get('name', 'Unknown')
                count = src.get('count', 0)
                s = '' if count == 1 else 's'
                dlg.add_heading(f"{name} \u2014 {count} event{s}")

                # Show per-event detail lines
                recent = src.get('recent', [])
                if recent:
                    for evt in recent:
                        line = self._format_event_line(evt, name)
                        dlg.add_text(line)
                else:
                    # Fallback: just the interpretation
                    interpretation = self._EVENT_SOURCE_INFO.get(name)
                    if interpretation:
                        dlg.add_text(interpretation)

                # Always show interpretation under the events
                if recent:
                    interpretation = self._EVENT_SOURCE_INFO.get(name)
                    if interpretation:
                        dlg.add_text(interpretation,
                                     color=COLORS['text_secondary'])

        crit = data.get('latest_critical')
        if crit:
            dlg.add_separator()
            dlg.add_heading("Latest Critical Event")
            dlg.add_row("Source", crit.get('source', 'Unknown'))
            if crit.get('timestamp'):
                dlg.add_row("Time", str(crit['timestamp']))
            if crit.get('message'):
                dlg.add_text(crit['message'])
        elif total > 0:
            dlg.add_separator()
            dlg.add_text("No critical-level events \u2014 all are error-level.",
                         color=COLORS['text_secondary'])

        dlg.exec()

    @staticmethod
    def _format_event_line(evt, source_name):
        """Format a single event into a readable detail line."""
        time_str = evt.get('time', '')
        event_id = evt.get('id', '')
        level = evt.get('level', 'Error')

        # Application Error — show which app crashed and why
        if evt.get('app') and source_name == 'Application Error':
            app = evt['app']
            module = evt.get('module', '')
            exc = evt.get('exception', '')
            parts = [f"\u25B8 {app} crashed"]
            if module:
                parts[0] += f" ({module})"
            if exc:
                parts.append(f"Exception {exc}")
            if time_str:
                parts.append(time_str)
            return " \u2014 ".join(parts)

        # Application Hang — show which app froze
        if evt.get('app') and source_name == 'Application Hang':
            line = f"\u25B8 {evt['app']} stopped responding"
            if time_str:
                line += f" \u2014 {time_str}"
            return line

        # Kernel-Power 41 — unexpected shutdown
        if evt.get('description') == 'Unexpected shutdown':
            line = f"\u25B8 Unexpected shutdown"
            if evt.get('bugcheck'):
                line += f" (bugcheck {evt['bugcheck']})"
            if time_str:
                line += f" \u2014 {time_str}"
            return line

        # Everything else — show the description or event ID
        desc = evt.get('description', '')
        if desc:
            line = f"\u25B8 {desc}"
        elif event_id:
            line = f"\u25B8 Event {event_id} ({level})"
        else:
            line = f"\u25B8 {level} event"
        if time_str:
            line += f" \u2014 {time_str}"
        return line

    def _detail_windows_update(self, data):
        dlg = DetailDialog("Windows Update", parent=self.window())
        if data.get('status') == 'unavailable':
            dlg.add_text(
                "Windows Update check requires the Update COM object which "
                "may not be available on all systems.",
                color=COLORS['text_secondary'])
            dlg.exec()
            return

        last = data.get('last_update') or {}
        if last:
            dlg.add_row("Last Update", last.get('hotfix_id', 'Unknown'))
            if last.get('description'):
                dlg.add_row("Description", last['description'])
            if last.get('installed_on'):
                dlg.add_row("Installed On", last['installed_on'])

        failed = data.get('failed_updates_last_30_days', 0)
        s = '' if failed == 1 else 's'
        color = COLORS['warning'] if failed > 0 else COLORS['success']
        dlg.add_row("Failed Updates (30d)",
                     f"{failed} update{s}" if failed > 0 else "None",
                     color=color)
        if failed > 0:
            dlg.add_text(
                "Failed updates may need manual intervention. Try running "
                "Windows Update manually or check for stuck updates.",
                color=COLORS['warning'])

        pending = data.get('pending_reboot', False)
        dlg.add_row("Pending Reboot",
                     "Yes — restart recommended" if pending else "No",
                     color=COLORS['warning'] if pending else None)
        dlg.exec()

    def _detail_defender(self, data):
        dlg = DetailDialog("Microsoft Defender", parent=self.window())
        dlg.add_row("Real-time Protection",
                     "Enabled" if data.get('realtime_enabled') else "Disabled",
                     color=COLORS['success'] if data.get('realtime_enabled')
                     else COLORS['error'])
        dlg.add_row("Antispyware",
                     "Enabled" if data.get('antispyware_enabled') else "Disabled",
                     color=COLORS['success'] if data.get('antispyware_enabled')
                     else COLORS['warning'])
        if data.get('signature_last_updated'):
            age = data.get('signature_age_days')
            val = data['signature_last_updated']
            if age is not None:
                age_text = "today" if age == 0 else (
                    "yesterday" if age == 1 else f"{age} days ago")
                val += f" ({age_text})"
            color = (COLORS['error'] if age and age > 7 else
                     COLORS['warning'] if age and age > 3 else None)
            dlg.add_row("Definitions Updated", val, color=color)
        if data.get('last_quick_scan'):
            age = data.get('quick_scan_age_days')
            val = data['last_quick_scan']
            if age is not None:
                age_text = "today" if age == 0 else (
                    "yesterday" if age == 1 else f"{age} days ago")
                val += f" ({age_text})"
            dlg.add_row("Last Quick Scan", val)
        if data.get('last_full_scan'):
            age = data.get('full_scan_age_days')
            val = data['last_full_scan']
            if age is not None:
                age_text = "today" if age == 0 else (
                    "yesterday" if age == 1 else f"{age} days ago")
                val += f" ({age_text})"
            dlg.add_row("Last Full Scan", val)
        dlg.exec()

    def _detail_startup(self, data):
        dlg = DetailDialog("Startup Items", parent=self.window())
        count = data.get('startup_item_count', 0)
        color = (COLORS['warning'] if count > 10 else
                 COLORS['success'] if count <= 5 else None)
        dlg.add_row("Total Items", str(count), color=color)
        if count > 10:
            dlg.add_text(
                "High startup count slows boot time. Review and disable "
                "unnecessary items via Task Manager > Startup.",
                color=COLORS['warning'])

        items = data.get('items', [])
        if items:
            dlg.add_heading("Programs")
            for item in items:
                name = item.get('Name', 'Unknown')
                source = item.get('Source', '')
                cmd = item.get('Command', '')
                val = cmd or 'N/A'
                if source:
                    val = f"[{source}] {val}"
                dlg.add_row(name, val)
        dlg.exec()

    # Common device manager error code actions
    _DEVMGR_ACTIONS = {
        1: "Restart the computer. If it persists, update the driver.",
        3: "Driver may be corrupt — reinstall from manufacturer.",
        10: "Device failed to start — may need driver update or is faulty.",
        12: "Not enough resources — IRQ or memory conflict. Try another slot.",
        22: "Device was disabled. Re-enable in Device Manager if needed.",
        28: "No driver installed. Download from the manufacturer's website.",
        31: "Device not working. Try uninstall + scan for hardware changes.",
        39: "Driver couldn't load. Reinstall driver from manufacturer.",
        43: "Hardware reported a failure. Device may be physically faulty.",
    }

    def _detail_device_manager(self, data):
        dlg = DetailDialog("Device Manager", parent=self.window())
        err_count = data.get('error_count', 0)
        color = COLORS['warning'] if err_count > 0 else COLORS['success']
        dlg.add_row("Devices with Errors", str(err_count), color=color)

        devices = data.get('devices', [])
        if devices:
            dlg.add_heading("Problem Devices")
            for dev in devices:
                name = dev.get('name', 'Unknown')
                code = dev.get('error_code', '?')
                desc = dev.get('error_description', 'Unknown error')
                dlg.add_row(name, f"Code {code}: {desc}",
                            color=COLORS['warning'])
                action = self._DEVMGR_ACTIONS.get(code)
                if action:
                    dlg.add_text(f"Action: {action}")
        else:
            dlg.add_text("No device errors detected.", color=COLORS['success'])
        dlg.exec()

    def _detail_power_plan(self, data):
        dlg = DetailDialog("Power Plan", parent=self.window())
        dlg.add_row("Active Plan", data.get('plan_name', 'Unknown'))
        if data.get('plan_guid'):
            dlg.add_row("GUID", data['plan_guid'])
        dlg.exec()

    def _detail_boot_time(self, data):
        dlg = DetailDialog("Boot & Uptime", parent=self.window())
        if data.get('last_boot'):
            dlg.add_row("Last Boot", data['last_boot'])

        boot_sec = data.get('boot_time_seconds')
        if boot_sec is not None:
            if boot_sec < 15:
                rating, color = 'Excellent (NVMe SSD)', COLORS['success']
            elif boot_sec < 30:
                rating, color = 'Good (SATA SSD)', COLORS['success']
            elif boot_sec < 60:
                rating, color = 'Fair (slow SSD or HDD)', COLORS['warning']
            else:
                rating, color = 'Slow (investigate)', COLORS['error']
            dlg.add_row("Boot Speed", f"{boot_sec:.1f} seconds — {rating}",
                         color=color)
        dlg.exec()
