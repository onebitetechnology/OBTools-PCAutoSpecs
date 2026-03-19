"""
PC AutoSpec — Automated PC diagnostics for RepairDesk repair shops.
PySide6 GUI: MainWindow, entry point, wiring.

Version 1.0
"""

import sys
import os
import tempfile

# ── Vendor path injection ──────────────────────────────────────────────
# Adds the USB's vendor/ folder to sys.path so bundled packages (wmi, etc.)
# are available without requiring pip install on the customer's machine.
def _inject_vendor_path():
    if getattr(sys, 'frozen', False):
        # Frozen exe: vendor/ sits next to the exe
        base = os.path.dirname(sys.executable)
    else:
        # Source mode: vendor/ sits at project root (one level above src/)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vendor = os.path.join(base, 'vendor')
    if os.path.isdir(vendor) and vendor not in sys.path:
        sys.path.insert(0, vendor)

_inject_vendor_path()
# ──────────────────────────────────────────────────────────────────────
import logging
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QLockFile
from PySide6.QtGui import QPalette, QColor
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSplitter, QStatusBar, QFrame, QMessageBox,
)

from theme import COLORS, build_stylesheet
from settings import (
    APP_NAME, APP_VERSION, load_settings, is_configured, is_first_run_setup_complete,
    get_app_dir, get_assets_dir, get_window_title, get_lhm_path,
    get_active_api_key, DEFAULT_UPLOAD_SCOPE,
)
from repairdesk_api import RepairDeskAPI
from report_formatter import ReportFormatter
from panels import SystemInfoPanel, ActivityLogPanel
from dialogs import SettingsDialog, ReportPreviewDialog, WelcomeDialog, StressTestDialog, ScanSummaryDialog, StartupDialog
from workers import SpecCollectorWorker, UploadWorker, GpuMonitorWorker


_APP_INSTANCE_LOCK = None


# ─── Logging ──────────────────────────────────────────────────────────

def setup_logging():
    """Set up file + console logging. Logs save to a logs/ folder next to the exe on the USB."""
    log_dir = Path(get_app_dir()) / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / (
        f"AutoSpecUploader_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    detailed = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | '
        '[%(name)s:%(funcName)s:%(lineno)d] | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    console = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S')

    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(detailed)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(console)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(ch)

    # Suppress urllib3 connection logs — they contain full URLs including API keys
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    logging.info(f"Logging initialized - Log file: {log_file}")
    return log_file


def check_admin_privileges():
    if sys.platform != 'win32':
        return True
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def acquire_single_instance_lock():
    """Prevent multiple copies of the app from running at the same time."""
    global _APP_INSTANCE_LOCK

    lock_root = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir())
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / "PCAutoSpec.lock"

    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        return None

    _APP_INSTANCE_LOCK = lock
    return lock


# ─── Main Window ──────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self, log_file=None):
        super().__init__()
        logging.info("Initializing AutoSpecUploaderGUI")

        self._log_file = log_file
        logging.debug("__init__: loading settings")
        self._settings = load_settings()
        logging.debug("__init__: init API")
        self._api = RepairDeskAPI()
        self._system_specs = {}
        self._gpu_worker = None
        self._lhm_process = None  # LibreHardwareMonitor subprocess
        self._lhm_launched = False
        self._scan_in_progress = False

        # Job context — set by StartupDialog, carried into scan + report
        self._job_tech_name     = self._settings.get('last_tech_name', '')
        self._job_ticket_id     = ''
        self._job_ticket_info   = {}
        self._job_report_type   = ''
        self._job_upload_scope  = DEFAULT_UPLOAD_SCOPE
        self._job_tech_notes    = ''
        self._job_skip_cats     = set()   # category keys to skip

        self.setWindowTitle(get_window_title())
        self.resize(1500, 900)
        self.setMinimumSize(1300, 750)

        # Window icon
        assets = Path(get_assets_dir())
        for icon_name in ('icon.ico', 'icon.png'):
            icon_path = assets / icon_name
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
                break

        # ── Central widget ────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(64)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(25, 8, 25, 8)
        h_layout.setSpacing(0)

        # Logo
        logo_label = QLabel()
        logo_path = assets / 'bw-logo.jpg'
        if logo_path.exists():
            from PySide6.QtGui import QPixmap
            pixmap = QPixmap(str(logo_path))
            logo_label.setPixmap(
                pixmap.scaledToHeight(48, Qt.SmoothTransformation))
        logo_label.setContentsMargins(0, 0, 12, 0)
        h_layout.addWidget(logo_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_lbl = QLabel(f"{APP_NAME}  —  v{APP_VERSION}")
        title_lbl.setStyleSheet(
            f"color: {COLORS['header_text']}; font-size: 15px; "
            f"font-weight: bold;")
        title_col.addWidget(title_lbl)

        subtitle = QLabel("Automated PC diagnostics and RepairDesk upload")
        subtitle.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 11px;")
        title_col.addWidget(subtitle)
        h_layout.addLayout(title_col)

        h_layout.addStretch()

        settings_btn = QPushButton("\u2699")
        settings_btn.setObjectName("flat")
        settings_btn.setFixedSize(40, 40)
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(self._open_settings)
        h_layout.addWidget(settings_btn)

        root_layout.addWidget(header)

        # ── Notification bar slot ─────────────────────────────────
        self._notification_slot = QVBoxLayout()
        self._notification_slot.setContentsMargins(0, 0, 0, 0)
        root_layout.addLayout(self._notification_slot)

        # ── Content area (splitter) ───────────────────────────────
        content = QWidget()
        content.setStyleSheet(f"background-color: {COLORS['bg_panel']};")
        c_layout = QHBoxLayout(content)
        c_layout.setContentsMargins(20, 20, 20, 20)
        c_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        self._info_panel = SystemInfoPanel()
        self._log_panel = ActivityLogPanel()

        splitter.addWidget(self._info_panel)
        splitter.addWidget(self._log_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([800, 640])

        c_layout.addWidget(splitter)
        root_layout.addWidget(content, 1)

        # ── Status bar ────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        # ── Signal wiring ─────────────────────────────────────────
        self._info_panel.preview_requested.connect(self._on_preview)
        self._info_panel.job_setup_requested.connect(self._open_job_setup)


        # ── Defer all startup work until after event loop begins ────
        QTimer.singleShot(100, self._deferred_startup)

    # ── Deferred Startup ─────────────────────────────────────────

    def _deferred_startup(self):
        """
        Run after the event loop starts and window is visible.
        Keeps __init__ clean so Qt doesn't crash on pre-show widget calls.
        """
        try:
            self._start_lhm()
            self._ensure_wifi()
            if self._lhm_launched or self._is_lhm_web_server_available() or self._count_running_lhm_processes() > 0:
                self._check_temp_sensor()
            else:
                logging.info("LHM not launched — skipping temp sensor wait")
            self._show_startup_dialog()
        except Exception as e:
            logging.error(f"Deferred startup error: {e}", exc_info=True)
            self._show_startup_dialog()

    # ── WiFi Auto-Connect ─────────────────────────────────────────

    def _ensure_wifi(self):
        """Connect to shop WiFi if no internet is available."""
        from config import load_settings
        from wifi_connect import ensure_internet
        s = load_settings()
        if not s.get('wifi_auto_connect', True):
            return
        ssid = s.get('wifi_ssid', '').strip()
        password = s.get('wifi_password', '').strip()
        if not ssid:
            return

        def _log(msg):
            logging.info(msg)

        result = ensure_internet(ssid, password, log_callback=_log)
        if not result['connected']:
            logging.warning(f"WiFi auto-connect failed: {result['message']}")

    # ── LibreHardwareMonitor ──────────────────────────────────────

    def _write_lhm_config(self):
        """
        Ensure LHM config at %AppData%/LibreHardwareMonitor/LibreHardwareMonitor.config
        has the web server enabled on port 8085.
        Only modifies web server keys — leaves all other LHM settings untouched.
        """
        try:
            import xml.etree.ElementTree as ET
            appdata = os.environ.get('APPDATA', '')
            if not appdata:
                return
            lhm_dir = os.path.join(appdata, 'LibreHardwareMonitor')
            os.makedirs(lhm_dir, exist_ok=True)
            config_path = os.path.join(lhm_dir, 'LibreHardwareMonitor.config')

            # Load existing config or create minimal one
            if os.path.isfile(config_path):
                try:
                    tree = ET.parse(config_path)
                    root = tree.getroot()
                except ET.ParseError:
                    # Corrupted config — start fresh
                    root = ET.fromstring('<configuration><appSettings></appSettings></configuration>')
                    tree = ET.ElementTree(root)
            else:
                root = ET.fromstring('<configuration><appSettings></appSettings></configuration>')
                tree = ET.ElementTree(root)

            app_settings = root.find('appSettings')
            if app_settings is None:
                app_settings = ET.SubElement(root, 'appSettings')

            # Only patch the web server keys
            web_keys = {
                'webServerActive': 'true',
                'webServerPort': '8085',
                'webServerListenerPrefix': 'http://localhost:8085/',
            }
            for key, value in web_keys.items():
                elem = app_settings.find(f".//add[@key='{key}']")
                if elem is not None:
                    elem.set('value', value)
                else:
                    ET.SubElement(app_settings, 'add', key=key, value=value)

            tree.write(config_path, encoding='utf-8', xml_declaration=True)
            logging.info(f"LHM config patched (web server enabled) at: {config_path}")
        except Exception as e:
            logging.warning(f"Could not patch LHM config: {e}")

    def _start_lhm(self):
        """Launch LibreHardwareMonitor silently for CPU temperature reading."""
        lhm_path = get_lhm_path()
        if not os.path.isfile(lhm_path):
            self._log_panel.append(
                "  ⚠ LibreHardwareMonitor.exe not found in assets — "
                "CPU temperature unavailable\n", 'warning')
            logging.warning(f"LHM not found at: {lhm_path}")
            return

        # If LHM is already serving data or already running, reuse it.
        if self._is_lhm_web_server_available():
            logging.info("LibreHardwareMonitor web server already available — reusing existing instance")
            self._lhm_process = None
            self._lhm_launched = False
            return

        running_count = self._count_running_lhm_processes()
        if running_count > 0:
            logging.info(
                f"LibreHardwareMonitor already running ({running_count} instance(s)) — "
                "reusing existing process"
            )
            self._lhm_process = None
            self._lhm_launched = False
            return

        # Write config first so web server is enabled when LHM launches
        self._write_lhm_config()
        try:
            import subprocess
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 2  # SW_SHOWMINNOACTIVE — minimized, not focused
            self._lhm_process = subprocess.Popen(
                [lhm_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=si,
            )
            logging.info(f"LibreHardwareMonitor launched minimized (PID {self._lhm_process.pid})")
            self._lhm_launched = True
        except Exception as e:
            self._log_panel.append(
                f"  ⚠ Could not launch LibreHardwareMonitor — "
                f"CPU temperature unavailable ({e})\n", 'warning')
            logging.warning(f"Failed to launch LHM: {e}")
            self._lhm_process  = None
            self._lhm_launched = False

    def _is_lhm_web_server_available(self):
        """True when LHM's local web server is already serving sensor data."""
        try:
            import json
            import urllib.request

            req = urllib.request.Request(
                "http://localhost:8085/data.json",
                headers={"User-Agent": "PCAutoSpec"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            return bool(data.get('Children'))
        except Exception:
            return False

    def _count_running_lhm_processes(self):
        """Count running LibreHardwareMonitor processes without shelling out."""
        try:
            import psutil

            count = 0
            for proc in psutil.process_iter(['name', 'exe']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    exe = (proc.info.get('exe') or '').lower()
                    if 'librehardwaremonitor' in name or 'librehardwaremonitor' in exe:
                        count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return count
        except Exception as e:
            logging.debug(f"Could not count running LHM processes: {e}")
            return 0

    def _stop_lhm(self):
        """Terminate LibreHardwareMonitor if we launched it."""
        if self._lhm_process:
            try:
                self._lhm_process.terminate()
                self._lhm_process.wait(timeout=3)
                logging.info("LibreHardwareMonitor terminated")
            except Exception as e:
                logging.debug(f"LHM termination issue: {e}")
                try:
                    self._lhm_process.kill()
                except Exception:
                    pass
            self._lhm_process = None

    def _eject_usb(self):
        """
        Attempt to safely eject the USB drive the app is running from.
        Uses PowerShell ShellApplication.Namespace().Self.InvokeVerb("Eject")
        — same as right-clicking the drive in Explorer and choosing Eject.
        Only runs if the app is on a removable drive.
        """
        import subprocess
        try:
            app_dir = get_app_dir()
            drive = os.path.splitdrive(app_dir)[0]  # e.g. "E:"
            if not drive:
                logging.info("USB eject: could not determine drive letter — skipping")
                return

            # Check drive type: 2 = Removable (USB), 3 = Fixed (HDD/SSD)
            ps_check = f"""
            $drive = Get-PSDrive -Name '{drive[0]}' -ErrorAction SilentlyContinue
            if ($drive) {{
                $type = (Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='{drive}'").DriveType
                Write-Output $type
            }}
            """
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_check],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            drive_type = result.stdout.strip()
            if drive_type != '2':
                logging.info(f"USB eject: drive {drive} is type {drive_type} (not removable) — skipping")
                return

            # Issue eject via Shell.Application
            ps_eject = f"""
            $shell = New-Object -ComObject Shell.Application
            $drive = $shell.Namespace('{drive}\\')
            if ($drive) {{ $drive.Self.InvokeVerb('Eject') }}
            """
            subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_eject],
                capture_output=True, text=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            logging.info(f"USB eject command issued for drive {drive}")

        except Exception as e:
            logging.warning(f"USB eject failed: {e}")

    def _check_temp_sensor(self):
        """
        Wait for LHM web server to come up before proceeding.
        Polls for up to 90 seconds — processes Qt events each cycle
        to keep the UI responsive. Only warns if LHM never responds.
        """
        import time
        import urllib.request
        from PySide6.QtWidgets import QApplication

        logging.info("Waiting for LHM web server...")
        deadline = time.monotonic() + 90  # generous timeout for first-run installs

        while time.monotonic() < deadline:
            QApplication.processEvents()  # keep UI alive during polling
            try:
                req = urllib.request.Request(
                    "http://localhost:8085/data.json",
                    headers={"User-Agent": "PCAutoSpec"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    import json
                    data = json.loads(resp.read().decode('utf-8'))
                    if data.get('Children'):
                        logging.info("LHM web server responding — CPU temp available")
                        return  # Good to go
            except Exception:
                pass
            time.sleep(2)

        # Still not up after 90s — warn and continue anyway
        logging.warning("LHM web server unavailable — CPU temp will be unreliable")
        QMessageBox.warning(
            self,
            "Temperature Sensor Limited",
            "LibreHardwareMonitor did not start correctly.\n\n"
            "CPU temperature under load may be inaccurate or unavailable.\n\n"
            "The scan will continue normally."
        )

    def _show_startup_dialog(self):
        """Show the job setup startup dialog, then begin scan."""
        try:
            dlg = StartupDialog(parent=self)
        except Exception as e:
            logging.error(f"StartupDialog failed to create: {e}", exc_info=True)
            self._start_spec_collection()
            return
        result = dlg.exec()

        # Collect whatever was entered before deciding whether to scan.
        self._job_tech_name   = dlg.tech_name   or self._job_tech_name
        self._job_ticket_id   = dlg.ticket_id
        self._job_ticket_info = dlg.ticket_info
        self._job_report_type = dlg.report_type
        self._job_upload_scope = dlg.upload_scope
        self._job_tech_notes  = dlg.tech_notes
        self._job_skip_cats   = dlg.skip_categories

        # Update header subtitle to show tech + ticket if available
        self._update_header_context()

        if result != StartupDialog.Accepted:
            if dlg.skip_scan_requested:
                logging.info("Job setup skipped — not starting a scan")
                self._system_specs = {}
                self._info_panel.show_scan_skipped()
                self._info_panel.set_button_enabled(False)
                self._info_panel.set_button_text("Scan Summary / Upload")
                self._status_bar.showMessage("Scan skipped — use Job Setup to start later")
                self._log_panel.append(
                    "  Scan skipped — use Job Setup to enter details and start later.\n",
                    'warning')
            else:
                logging.info("Job setup dismissed — not starting a scan")
            return

        self._start_spec_collection()

    def _update_header_context(self):
        """Update the subtitle label in the header bar to show current job context."""
        parts = []
        if self._job_tech_name:
            parts.append(f"Tech: {self._job_tech_name}")
        if self._job_ticket_id:
            parts.append(f"Ticket: T-{self._job_ticket_id}")
        if self._job_report_type:
            short = "Initial Report" if "Initial" in self._job_report_type else "Final Report"
            parts.append(short)
        if parts:
            # Find the subtitle label and update it
            for child in self.findChildren(QLabel):
                if child.text() == "Automated PC diagnostics and RepairDesk upload":
                    child.setText("  ·  ".join(parts))
                    break

    def _start_spec_collection(self):
        logging.info("Starting system specifications collection")
        self._scan_in_progress = True
        self._info_panel.set_job_button_enabled(False)
        if self._gpu_worker:
            self._gpu_worker.stop()
            self._gpu_worker = None
        self._info_panel.set_button_enabled(False)
        self._info_panel.set_button_text("⏳ Scanning...")
        self._info_panel.spinner.start()

        self._spec_worker = SpecCollectorWorker(
            skip_categories=self._job_skip_cats)
        self._spec_worker.progress.connect(
            self._info_panel.spinner.set_phase)
        self._spec_worker.log_message.connect(self._on_log_message)
        self._spec_worker.spec_update.connect(
            self._info_panel.update_from_specs)
        self._spec_worker.finished.connect(self._on_specs_collected)
        self._spec_worker.error.connect(self._on_specs_error)
        self._spec_worker.stress_test_started.connect(self._on_stress_started)
        self._spec_worker.stress_test_temp.connect(self._on_stress_temp)
        self._spec_worker.stress_test_finished.connect(self._on_stress_finished)
        self._spec_worker.start()

    def _on_stress_started(self):
        """Show the stress test progress dialog."""
        self._stress_dialog = StressTestDialog(duration_sec=20, ramp_sec=60, parent=self)
        self._stress_dialog.show()

    def _on_stress_temp(self, temp_c: float):
        """Forward live temp sample to the dialog."""
        if hasattr(self, '_stress_dialog') and self._stress_dialog:
            self._stress_dialog.update_temp(temp_c)

    def _on_stress_finished(self):
        """Close the stress test dialog."""
        if hasattr(self, '_stress_dialog') and self._stress_dialog:
            self._stress_dialog.finish()
            self._stress_dialog = None

    def _on_log_message(self, msg):
        """Feed log messages to both the activity log and the spinner."""
        self._log_panel.append(msg)
        # Update spinner with primary lines only (not indented sub-details)
        stripped = msg.strip()
        if stripped and not msg.startswith('  '):
            self._info_panel.spinner.set_phase(stripped)

    def _on_specs_collected(self, specs):
        self._scan_in_progress = False
        self._info_panel.set_job_button_enabled(True)
        self._system_specs = specs
        self._info_panel.spinner.stop()
        self._info_panel.update_from_specs(specs)
        self._info_panel.set_button_enabled(True)
        self._info_panel.set_button_text("Scan Summary / Upload")
        self._log_panel.append(
            "  \u2713 System specifications collected successfully\n\n",
            'success')
        self._status_bar.showMessage("Scan complete")
        logging.info("System specs collected successfully")

        # Start GPU monitoring if metrics available
        gpu_metrics = specs.get('GPUMetrics', {})
        if gpu_metrics:
            gpu_full = specs.get('GPU', '')
            import re
            m = re.match(r'^(.*?)\s*(?:\(|-).*', gpu_full)
            gpu_name = m.group(1).strip() if m else gpu_full
            self._start_gpu_monitor(gpu_name)

        # Auto-open the results/upload dialog after a short delay
        # (delay lets the UI repaint and GPU monitor start first)
        QTimer.singleShot(600, self._on_preview)

    def _on_specs_error(self, error_msg):
        self._scan_in_progress = False
        self._info_panel.set_job_button_enabled(True)
        self._info_panel.spinner.stop()
        self._info_panel.set_button_enabled(False)
        self._info_panel.set_button_text("Scan Summary / Upload")
        self._log_panel.append(
            f"  \u2717 Error collecting specs: {error_msg}\n\n", 'error')
        self._status_bar.showMessage("Error collecting specs — use Job Setup to retry")

    # ── GPU monitoring ────────────────────────────────────────────

    def _start_gpu_monitor(self, gpu_name):
        self._gpu_worker = GpuMonitorWorker(gpu_name)
        self._gpu_worker.metrics_updated.connect(
            self._info_panel.update_gpu_metrics)
        self._gpu_worker.start()
        logging.info("GPU monitoring thread started")

    # ── Preview / Upload ──────────────────────────────────────────

    def _on_preview(self):
        """Handle Upload to RepairDesk button click — show preview, get ticket, upload."""
        if not self._system_specs:
            QMessageBox.warning(
                self, "System Specs Not Ready",
                "System specifications are still being collected. "
                "Please wait.")
            return

        # Generate HTML report — inject job context into specs
        specs_with_context = dict(self._system_specs)
        specs_with_context['_job_tech_name']   = self._job_tech_name
        specs_with_context['_job_ticket_id']   = self._job_ticket_id
        specs_with_context['_job_report_type'] = self._job_report_type
        specs_with_context['_job_tech_notes']  = self._job_tech_notes
        specs_with_context['_job_skip_cats']   = self._job_skip_cats

        formatter = ReportFormatter()
        issues = formatter._get_critical_issues_list(specs_with_context)

        logging.info("Generating report preview")

        # Show scan summary / upload dialog
        dlg = ReportPreviewDialog(
            specs_with_context, formatter, issues=issues, parent=self,
            prefill_ticket=self._job_ticket_id,
            prefill_tech_name=self._job_tech_name,
            prefill_notes=self._job_tech_notes,
            ticket_already_confirmed=bool(self._job_ticket_info),
            initial_upload_scope=self._job_upload_scope,
        )
        if dlg.exec() != ReportPreviewDialog.Accepted:
            logging.info("User closed preview without uploading")
            return

        ticket_id = dlg.ticket_id
        edited_note = dlg.edited_html
        self._job_tech_name = dlg.tech_name or self._job_tech_name
        self._job_tech_notes = dlg.tech_notes
        self._job_upload_scope = dlg.upload_scope
        self._update_header_context()

        # Refresh API — use tech's own API key if configured
        _upload_key, _upload_source = get_active_api_key(self._job_tech_name)
        self._api = RepairDeskAPI(api_key=_upload_key)
        if _upload_source != 'global':
            logging.info(f"Upload using API key for tech: {_upload_source}")
        else:
            logging.info("Upload using global API key")
        self._current_ticket_id = ticket_id  # store for success messages
        logging.info(f"User confirmed upload for ticket: {ticket_id}")

        self._info_panel.set_button_enabled(False)
        self._status_bar.showMessage("Uploading...")
        self._log_panel.append("\n")
        self._log_panel.append("  Uploading to RepairDesk...\n")

        # If ticket was confirmed at startup, skip the re-confirmation dialog
        already_confirmed = bool(self._job_ticket_info) and ticket_id == self._job_ticket_id
        self._upload_worker = UploadWorker(
            self._api, ticket_id, edited_note,
            skip_confirmation=already_confirmed)
        self._upload_worker.progress.connect(self._on_upload_progress)
        self._upload_worker.finished.connect(self._on_upload_finished)
        self._upload_worker.confirm_customer.connect(self._on_confirm_customer)
        self._upload_worker.start()

    def _on_confirm_customer(self, ticket_info):
        """Show confirmation dialog with customer name before upload proceeds."""
        customer = ticket_info.get('customer_name', 'Unknown')
        device = ticket_info.get('device', '')
        ticket_num = ticket_info.get('ticket_number', '')

        msg = "Upload diagnostic report to this ticket?\n\n"
        msg += f"Ticket:    T-{ticket_num}\n"
        msg += f"Customer:  {customer}\n"
        if device:
            msg += f"Device:    {device}\n"

        # No-parent QMessageBox — avoids inheriting the dark app stylesheet on Windows
        _box = QMessageBox()
        _box.setWindowTitle("Confirm Upload")
        _box.setText(msg)
        _box.setWindowModality(Qt.WindowModality.ApplicationModal)
        geo = self.geometry()
        _box.move(geo.x() + (geo.width() - 460) // 2, geo.y() + (geo.height() - 180) // 2)
        _box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        _box.setDefaultButton(QMessageBox.StandardButton.Yes)
        _pal = QPalette()
        _pal.setColor(QPalette.ColorRole.Window,     QColor("#f5f5f5"))
        _pal.setColor(QPalette.ColorRole.WindowText, QColor("#1a1a1a"))
        _pal.setColor(QPalette.ColorRole.Base,       QColor("#f5f5f5"))
        _pal.setColor(QPalette.ColorRole.Text,       QColor("#1a1a1a"))
        _pal.setColor(QPalette.ColorRole.Button,     QColor("#10B981"))
        _pal.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
        _box.setPalette(_pal)
        _box.setStyleSheet("""
            QMessageBox { background-color: #f5f5f5; color: #1a1a1a; }
            QMessageBox QLabel { color: #1a1a1a; font-size: 10pt; min-width: 320px; }
            QMessageBox QPushButton {
                background-color: #10B981; color: white; border: none;
                border-radius: 5px; padding: 6px 20px; font-size: 10pt; min-width: 80px;
            }
            QMessageBox QPushButton:hover { background-color: #059669; }
        """)
        reply = _box.exec()

        self._upload_worker.set_confirmed(
            reply == int(QMessageBox.StandardButton.Yes))

    def _on_upload_progress(self, message, tag):
        self._log_panel.append(f"  {message}\n", tag or None)

    def _on_upload_finished(self, success, message):
        self._info_panel.set_button_enabled(True)
        if success:
            ticket_display = f"T-{self._current_ticket_id}"
            self._log_panel.append(
                f"  \u2713 Diagnostic note added to ticket {ticket_display}\n\n",
                'success')
            self._status_bar.showMessage(
                f"Upload complete \u2014 ticket {ticket_display}")
            QMessageBox.information(
                self, "Upload Complete",
                f"Diagnostic note successfully added to "
                f"ticket {ticket_display}.")
        else:
            self._log_panel.append(
                f"  \u2717 Upload failed: {message}\n\n", 'error')
            self._status_bar.showMessage("Upload failed")
            QMessageBox.critical(
                self, "Upload Failed",
                f"Failed to upload diagnostic note:\n\n{message}")

    # ── Settings ──────────────────────────────────────────────────

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec() == SettingsDialog.Accepted and dlg.saved_settings:
            self._settings = dlg.saved_settings
            self._api = RepairDeskAPI()

    def _open_job_setup(self):
        """Re-open the Job Setup dialog when the app is idle."""
        if self._scan_in_progress:
            self._status_bar.showMessage("Scan already running")
            return
        self._show_startup_dialog()

    # ── Cleanup ───────────────────────────────────────────────────

    def closeEvent(self, event):
        logging.info("Window close event triggered")
        if self._gpu_worker:
            self._gpu_worker.stop()
            logging.info("GPU monitoring thread stopped")
        self._stop_lhm()

        # Offer to eject the USB before closing
        self._prompt_eject()

        event.accept()
        logging.info("Application closed")

    def _prompt_eject(self):
        """Ask tech if they want to eject the USB, then do it."""
        import subprocess
        try:
            app_dir = get_app_dir()
            drive = os.path.splitdrive(app_dir)[0]
            if not drive:
                return

            # Only prompt if on a removable drive
            ps_check = f"""
            $type = (Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='{drive}'").DriveType
            Write-Output $type
            """
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_check],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.stdout.strip() != '2':
                return  # Not a removable drive — skip silently

            box = QMessageBox()
            box.setWindowTitle("Eject USB?")
            box.setText(
                f"Safely eject the USB drive ({drive}) before removing it?"
            )
            box.setWindowModality(Qt.WindowModality.ApplicationModal)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            _ep = QPalette()
            _ep.setColor(QPalette.ColorRole.Window,     QColor("#f5f5f5"))
            _ep.setColor(QPalette.ColorRole.WindowText, QColor("#1a1a1a"))
            _ep.setColor(QPalette.ColorRole.Button,     QColor("#10B981"))
            _ep.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
            box.setPalette(_ep)
            box.setStyleSheet(
                "QMessageBox { background-color: #f5f5f5; color: #1a1a1a; }"
                "QLabel { color: #1a1a1a; font-size: 10pt; }"
                "QPushButton { background-color: #10B981; color: white; "
                "border-radius: 4px; padding: 6px 16px; font-size: 9pt; }"
            )

            if box.exec() == QMessageBox.Yes.value:
                ps_eject = f"""
                $shell = New-Object -ComObject Shell.Application
                $d = $shell.Namespace('{drive}\\')
                if ($d) {{ $d.Self.InvokeVerb('Eject') }}
                """
                subprocess.run(
                    ['powershell', '-NoProfile', '-Command', ps_eject],
                    capture_output=True, text=True, timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                logging.info(f"USB eject command issued for {drive}")
            else:
                logging.info("USB eject declined by user")

        except Exception as e:
            logging.warning(f"USB eject prompt failed: {e}")


# ─── Entry point ──────────────────────────────────────────────────────

def main():
    """Main entry point — called from run.py."""
    app_start = datetime.now()
    log_file = setup_logging()

    # Session header
    logging.info("=" * 70)
    logging.info(f"=== {APP_NAME} v{APP_VERSION} ===")

    if hasattr(sys, '_MEIPASS'):
        logging.info("Running Mode: Bundled executable (PyInstaller)")
    else:
        logging.info("Running Mode: Source (Python interpreter)")

    machine = os.environ.get('COMPUTERNAME', 'Unknown')
    user = os.environ.get('USERNAME', 'Unknown')
    logging.info(f"Machine: {machine}")
    logging.info(f"Windows User: {user}")

    is_admin = check_admin_privileges()
    mode = 'Full Diagnostics (Admin)' if is_admin else 'Standard Mode (Not Admin)'
    logging.info(f"Run Mode: {mode}")

    if sys.platform == 'win32':
        wv = sys.getwindowsversion()
        logging.info(f"Platform: Windows {wv.major}.{wv.minor} "
                     f"Build {wv.build}")
    logging.info(f"Python: {sys.version.split()[0]}")
    logging.info(f"Session Start: "
                 f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 70)

    # Create Qt application
    app = QApplication(sys.argv)
    app.setStyleSheet(build_stylesheet())

    if not acquire_single_instance_lock():
        logging.warning("Another PC AutoSpec instance is already running; exiting duplicate launch")
        QMessageBox.warning(
            None,
            "Already Running",
            "PC AutoSpec is already open on this machine.\n\n"
            "Close the existing window before starting another copy."
        )
        sys.exit(0)

    # First use — require initial setup before loading the main window
    if not is_first_run_setup_complete():
        welcome = WelcomeDialog()
        if welcome.exec() != WelcomeDialog.Accepted:
            logging.info("First-use setup cancelled by user")
            sys.exit(0)

    try:
        window = MainWindow(log_file)
    except Exception as e:
        logging.critical(f"MainWindow failed to initialize: {e}", exc_info=True)
        QMessageBox.critical(None, "Startup Error",
            f"AutoSpec failed to start:\n\n{e}\n\nCheck the log file for details.")
        sys.exit(1)
    window.showMaximized()

    init_elapsed = (datetime.now() - app_start).total_seconds()
    logging.info(f"Application initialized in {init_elapsed:.2f} seconds, "
                 f"starting main loop")

    exit_code = app.exec()

    runtime = (datetime.now() - app_start).total_seconds()
    logging.info(f"Application main loop exited after "
                 f"{runtime:.2f} seconds")
    logging.info("=" * 70)
    logging.info(f"Session End: "
                 f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Duration: {str(datetime.now() - app_start).split('.')[0]}")
    logging.info("Final Status: SUCCESS (Diagnostics completed)")
    logging.info("=" * 70)

    sys.exit(exit_code)
