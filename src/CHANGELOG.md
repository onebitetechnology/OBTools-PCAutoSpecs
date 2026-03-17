# AutoSpec Uploader — Changelog

---

## v2.2.5 — 2026-03-13

### BIOS version + date now in report
- Report was only showing the BIOS manufacturer (first line of `BIOS` key)
- Now also pulls `Version` and `Date` from `BIOSDetails` and displays them inline:
  `BIOS: American Megatrends Inc.  ·  Version: 1.B0  ·  Date: 03/15/2023`
- Age estimate now calculated from the actual BIOS date when available

### CPU temperature fallback chain
- LHM HTTP (port 8085) was the only temperature method — if LHM wasn't running, CPU temp silently disappeared from the report
- Added two WMI fallbacks tried in order when LHM is unavailable:
  1. `MSAcpi_ThermalZoneTemperature` — available on most systems without extra software
  2. `Win32_TemperatureProbe` — last resort
- All three methods share consistent sanity checking (20–120°C) and debug logging

---

## v2.2.4 — 2026-03-12

### Fix: "Diagnosed by / Tech Notes" no longer appears twice
- Root cause: the upload dialog was **manually prepending** its own `Diagnosed by` + `Tech Notes` block before the report body, on top of what the formatter already outputs — resulting in both appearing at the very top of every uploaded note
- Fix: removed the manual prefix entirely from the upload dialog; the formatter owns the header

### Fix: Emoji as HTML numeric character references
- Raw UTF-8 emoji were being corrupted by RepairDesk's backend regardless of charset headers
- Switched all section emoji to HTML numeric character references (e.g. `&#x1F6A8;`) — pure ASCII strings that survive any encoding pipeline and are decoded to emoji by RepairDesk's HTML renderer

---

## v2.2.3 — 2026-03-12

### Fix: Full emoji now render correctly in RepairDesk
- Root cause: `requests` was posting with `Content-Type: application/json` (no charset), causing RepairDesk's API to misinterpret 4-byte UTF-8 sequences as `????`
- Fix: API POST now uses `data=json.dumps(..., ensure_ascii=False).encode('utf-8')` with explicit `Content-Type: application/json; charset=utf-8` header — emoji transmit cleanly
- Restored proper emoji in section headers (using Unicode escape sequences in source to avoid any file-encoding ambiguity):
  🖥 Overview · 🚨 Critical · ⚙ Hardware · 🌐 Network · 📺 Display · 🔋 Battery · 💾 Storage · 📊 Status · 🔧 Drivers · 🩺 Health

---

## v2.2.2 — 2026-03-12

### Report header cleanup
- Report type (e.g. "Final Device Report") is now the **first line of the note body**, rendered at 14pt bold — clearly visible at a glance
- "Diagnosed by: [tech]" follows on the next line — no ticket number (RepairDesk already shows the ticket in its UI)
- Removed: timestamp, "One Bite Technology — System Device Report" title, and tech notes from the note body (RepairDesk displays all three in its own UI above the note, so they were appearing twice)
- Divider between header block and sections changed from dark (#444) to light (#ccc) to match the section rules

### Symbol update
- Replaced generic block symbols with more expressive BMP Unicode characters, all confirmed in the safe rendering range (U+25xx–U+27xx):
  ▸ System Overview · ⚠ Critical Issues · ⚙ Hardware · ◈ Network · ◉ Display · ◇ Battery · ▪ Storage · ◆ System Status · ► Drivers · ✦ System Health

---

## v2.2.1 — 2026-03-12

### Fix: Section symbols now render correctly in RepairDesk
- RepairDesk's HTML renderer drops 4-byte Unicode emoji (U+1F4xx/1F5xx range) as `????`
- Replaced all broken emoji with safe BMP Unicode symbols (all U+25xx/U+27xx range, same tier as ⚙ which was already confirmed working)
- New symbols: ■ Overview · ▲ Critical · ⚙ Hardware · ◉ Network · ▣ Display · ◈ Battery · ◼ Storage · ◆ System Status · ▤ Drivers · ✦ Health
- Also fixed: Network and Display sections were emitting their section header **twice** (once from a `content.append` added in v2.2.0, once from the existing `lines=[...]` wrapper at the end of each function) — duplicate removed

---

## v2.2.0 — 2026-03-12

### Report Layout Overhaul
- **Styled section headers** — each section now has a blue ruled divider line and an emoji heading (🖥️ System Overview, 🚨 Critical Issues, ⚙️ Hardware Configuration, 🌐 Network, 🖵 Display, 🔋 Battery, 💾 Storage Health, 📊 System Status, 🔧 Drivers, 🩺 System Health). Section headers are rendered at 12pt in dark navy, matching RepairDesk's HTML renderer.
- **Header divider** — thin ruled line between the job header (tech name / ticket / notes) and the report body
- The emoji/colour styling is applied at assembly time — all `_format_*` methods remain plain text lists internally, keeping them easy to edit

---

## v2.1.9 — 2026-03-12

### New: Power & Battery Readings
Added real-time power data to the Battery section (both GUI panel and report):

- **Power Source** — "AC Power (plugged in)" or "On Battery", pulled from `root\wmi BatteryStatus.PowerOnline`
- **Charge Rate** — watts flowing into the battery while charging (e.g. "Charging at 34W"). Useful flag if a laptop shows lower-than-expected charge rate (bad port, wrong adapter)
- **Discharge Rate** — watts being drawn from battery when on battery power (e.g. "Drawing 9.2W")
- **Voltage** — current battery voltage in volts
- **Estimated Runtime** — time remaining when on battery (colour-coded: red <30min, amber <60min). Skipped when on AC or when WMI returns the sentinel "unknown" value
- **Power Plan** — now shown in the System Status section of the report (Balanced / High Performance / Power Saver etc.)

All readings come from a single PowerShell `BatteryStatus` WMI query with an 8s timeout. If unavailable (desktop, or driver doesn't expose the class), fields are silently omitted.

---

## v2.1.8 — 2026-03-12

### Fixes & UX
- **Checkbox checkmarks** — Qt cannot use `data:` URIs and won't auto-draw a checkmark when the indicator is fully styled. Fix: a white checkmark SVG is written to a temp file at startup and referenced as a real file path in the stylesheet. Checkmarks now render correctly on Windows.
- **Manage Techs reworked** — Removed the button + separate dialog entirely. The Settings dialog now has 5 inline name fields directly in the Technicians card. Just type names and hit Save — no extra dialog needed.

---

## v2.1.7 — 2026-03-12

### Bug Fixes & Design Change
- **Manage Techs button still unresponsive** — Root cause found: the button used `objectName("secondary")`, which caused Qt to match it against the global app stylesheet rule `QPushButton#secondary { background-color: #232B3B; color: text_primary }`. On a dark background this made the button invisible (dark text on dark card), and appearing to do nothing. Fixed by giving the button an explicit green stylesheet instead of relying on the global objectName rule.
- **Per-technician API keys removed** — After checking the RepairDesk API docs, RepairDesk uses a **single store-level API key** — there is no per-technician key system. The `addnote` endpoint has no technician field. The Manage Techs dialog now just tracks **tech names** for scan attribution (logged to the job, shown in startup dialog). The API key fields and masking UI have been removed from the Add/Edit tech dialog.

---

## v2.1.6 — 2026-03-12

### Bug Fix
- **"Manage Techs" button in Settings doesn't respond** — The Manage Techs card was being clipped below the visible area of the Settings dialog on smaller or DPI-scaled screens. The button was rendering off-screen so clicks landed on nothing. Fixed by wrapping the Settings body in a `QScrollArea` — all cards are now always reachable by scrolling, and the Manage Techs button correctly opens the roster editor.

---

## v2.1.5 — 2026-03-12

### Bug Fix
- **Confirm Ticket / Confirm Upload popups show as blank dark blue window** — Root cause: passing `self` (the dark-themed main window) as the parent to `QMessageBox` causes Qt on Windows to inherit the parent's stylesheet down into the box's internal `QLabel` children, overriding any light styling applied to the box itself. Fix: all popups now create `QMessageBox()` with no parent, set `ApplicationModal` manually, and position themselves centered over the parent window. Affects: Confirm Ticket (Job Setup dialog), Confirm Upload (upload flow), and Eject USB prompt.

---

## v2.1.4 — 2026-03-12

### USB Safe Eject on Close
- When closing the app, if it detects it's running from a **removable drive** (USB stick), it prompts: *"Safely eject the USB drive (E:) before removing it?"*
- Yes → issues a PowerShell `Shell.Application` eject command (same as right-click → Eject in Explorer), then closes
- No → closes normally without ejecting
- If the app is running from a fixed drive (desktop testing), the prompt is silently skipped — no popup
- LHM is always terminated first before the eject prompt appears

---

## v2.1.3 — 2026-03-11

### Bug Fixes & UX
- **Checkbox checkmarks missing** — Qt does not support `data:` URI images in stylesheets. Removed the broken SVG data URI; Qt now renders its native checkmark on the green indicator background, which works correctly cross-platform
- **Manage Techs moved to Settings** — removed the ⚙ button from the Job Setup dialog (it wasn't working there anyway). Manage Techs is now a proper card in the Settings dialog, showing a summary of configured techs and a button to open the roster editor
- **Settings save no longer wipes technician roster** — `SettingsDialog._on_save` now preserves the `technicians` and `last_tech_name` keys when saving

---

## v2.1.2 — 2026-03-11

### Bug Fixes
- **Startup dialog crash — `KeyError: 'accent'`** — `COLORS['accent']` was referenced in the checkbox indicator stylesheet in `StartupDialog` but was never defined in `theme.py`. This caused the dialog to silently crash every time, falling through to `_start_spec_collection()` and skipping the Job Setup popup entirely. Fixed by replacing all 3 references with `COLORS['success']` (the green that was intended).

---

## v2.1.1 — 2026-03-11

### Bug Fixes
- **Startup dialog never appeared on machines without LHM** — `_check_temp_sensor()` was polling for LHM for up to 90 seconds even when LHM was never launched (e.g. LHM exe missing, or running on a desktop without the assets folder). Fix: `_lhm_launched` flag now tracks whether LHM actually started; temp sensor poll is skipped entirely if LHM was never launched, so the Job Setup dialog appears immediately
- **Webcam — no camera on desktop is correct behavior** — `status: none` is returned silently; nothing appears in the report or GUI on machines with no camera (as intended)

---

## v2.1.0 — 2026-03-11

### Per-Technician API Keys
- New **Manage Techs** screen (⚙ button in Job Setup dialog) for adding/removing technicians and their individual RepairDesk API keys
- When techs are configured, the free-text name field is replaced with a **dropdown selector** — just pick your name and go
- Upload now uses the selected tech's own API key, so diagnostic notes post under their RepairDesk account instead of the admin account
- API keys are masked in the UI by default with a show/hide toggle
- Tech roster stored in `settings.json` on the USB stick alongside other settings

### Version Display
- Version number now visible in three places: OS title bar, main window header, and Job Setup dialog header
- Bumping `APP_VERSION` in `settings.py` automatically propagates to all three — no other changes needed

### Battery Health — Aftermarket Battery Fix
- `powercfg /batteryreport` timeout increased from 10s → 30s (aftermarket batteries respond slowly)
- Added `BatteryFullChargedCapacity` query to the WMI `root\wmi` namespace as a fallback when `powercfg` times out — health % now calculates correctly on aftermarket/replacement batteries
- Design capacity also falls back to `BatteryStaticData` WMI class when `powercfg` is unavailable

### WiFi Diagnostics — Debug Logging
- Added detailed debug logging for all WiFi non-ok paths (no adapter, disconnected, parse error)
- Raw `netsh` output now logged at DEBUG level when state cannot be parsed — makes silent WiFi failures diagnosable from the log file

### Webcam Testing (new feature)
- Detects cameras via WMI (`Win32_PnPEntity` — Camera/Image class)
- Attempts a functional test: opens camera via OpenCV, captures a test frame, records resolution (e.g. `1280x720`)
- `opencv-python-headless` auto-installs to `vendor/` on first run if not present
- Webcam result shown in **Display panel** in GUI and **Display section** of uploaded report
- Non-responding camera triggers a **Critical Issue** in the report
- Webcam check skipped if Display category is unchecked in Job Setup

---

## v2.0.0 — 2026-03-11

### Job Setup Dialog (new feature)
- New **Job Setup dialog** appears at every app launch (skippable via "Skip" button)
- Tech can enter their **name**, **Ticket ID**, **Report Type**, **Tech Notes**, and select **Test Categories** before the scan starts
- Ticket ID confirmation uses the same fetch → customer/device popup flow as the upload dialog
- **Report Type** is required to start (Initial Device Report / Final Device Report (Post Repair)) — shown as a bold header at the top of every uploaded report
- Tech Notes entered here pre-fill the upload dialog (still editable at upload time)
- Header subtitle updates to show tech name, ticket, and report type after setup

### Selective Test Categories
- Tech can uncheck any of: **CPU / RAM / GPU / Storage / Network & WiFi / Battery / Display**
- Unchecked categories are skipped during the scan (faster re-tests)
- Skipped categories appear as **fine print at the bottom of the uploaded report**
- Skipping CPU skips the stress test; skipping GPU skips GPU stress; skipping Storage skips disk speed; skipping Network skips WiFi diagnostics

### Upload Flow Improvements
- If ticket was confirmed at startup, the upload dialog **skips the re-confirmation popup** and uploads directly
- Report Preview dialog now **pre-fills** ticket number, tech name, and notes from the startup dialog

### Partition Style (MBR / GPT)
- Each drive now shows its partition style in both the GUI and uploaded report
- MBR drives flagged in Critical Issues: "legacy format, incompatible with Secure Boot and modern reinstalls"
- RAW (unformatted) drives also flagged

### GUI / Report Synchronisation Audit
- Drive **type label** (NVMe SSD / SATA SSD / HDD) now shown in report (was GUI-only)
- **Partition Style** now shown in GUI storage panel (was report-only after initial addition)
- CPU temp labels **(Normal / Warm / Hot)** added to report (were GUI-only)
- **Disk Speed** in report now shows category label (NVMe / SATA SSD / HDD) and notes it's the C: drive
- **Battery assessment** (Excellent / Good / Fair / Poor) added to report (was GUI-only)
- Battery capacity label renamed "Full Charge Capacity" in report to match GUI
- **WiFi diagnostics** (signal %, link speed, standard, SSID) added to GUI Network panel (was report-only)

### Bug Fixes
- **SMART false alarm fix**: drives where smartctl fails to query (e.g. Neo Forza NFS011SA356) no longer show "SMART Failed — back up immediately". Now correctly shows "SMART unavailable — verify manually". Genuine SMART failures (drive self-assessment returns FAILED) still trigger the warning.
- **pynvml auto-install fix**: `sys` was imported as `_sys` in advanced_health.py but bare `sys` was used in the pynvml auto-install path — silently failed every time on machines without pynvml pre-installed.

---

## v1.5.0 — 2026-03-10

### WiFi Diagnostics
- Added WiFi diagnostics via `netsh wlan show interfaces`
- Reports: adapter name, WiFi generation (WiFi 6/5/4), signal %, link speed RX/TX Mbps, SSID, radio type
- Critical issues: signal <20% (very weak), signal <40% (weak), link speed <54 Mbps on modern adapter, 802.11g/b adapter, disconnected
- No-adapter warning only fires on laptops (desktops silently skip)
- WiFi section correctly placed in Network section of report

### GPU Stress Test Fix
- RTX 3090 (and other GPUs) not being detected for stress test — guard check used `results['temperatures']['gpu']` which was `None` when pynvml wasn't installed, skipping the test even with a GPU present
- Fix: GPU detection now uses `nvidia-smi --query-gpu=name` directly (Method 1), WMI fallback for AMD (Method 2)

### Report Layout Fixes
- WiFi info was appearing orphaned at the bottom of the report — moved to Network section
- Drive Speed was appearing orphaned at the bottom — moved to Storage section

### RepairDesk API — Workflow Status Updates (explored, on hold)
- Confirmed `POST /ticket/updateticketstatus` works
- Live-tested: both line items on ticket T-15108 updated to "Diagnostics - In Progress" successfully
- Workflow step update integration deferred pending UI design decision

### Security Fixes
- API key no longer logged (urllib3 suppressed)
- Customer personal data no longer logged

---

## v1.4.0 — 2026-03-06

### Drive Health & Speed
- SMART failure detection: `SMART_FAILED` status → SEVERITY_WARN, "Back up data immediately"
- Drive speed test: 32MB sequential read/write, direct worker thread
  - Critical: <80 MB/s read → "possible drive failure or HDD"
  - Warning: <200 MB/s read → "consider SSD upgrade"

### Customer Confirmation Before Upload
- Upload flow now fetches ticket from RepairDesk and shows customer name + device before proceeding
- Tech must confirm correct customer before report is uploaded

### CPU Thermal Throttling Detection
- Peak temp tracked across full 60s ramp; flags if ramp peak > measurement peak + 10°C
- Thermal abort at 100°C (raised from 95°C)

---

## v1.3.0 — 2026-03-05 (b)

### UI Workflow Refactor
- Removed launch dialog
- Tech name, ticket number, and notes moved to post-scan summary popup
- Critical issues panel added to upload dialog

### GPU Stress Testing
- OpenCL-based GPU stress test (pyopencl auto-installs to vendor/ on first run)
- 15s ramp + 20s measurement
- Memory temperature monitoring via LHM (DDR5; DDR4 usually not available)

### Version Tracking
- `APP_VERSION` added to `settings.py`
- Version displayed in Settings dialog

---

## v1.2.0 — 2026-03-05 (a)

### CPU Temperature Monitoring
- LibreHardwareMonitor HTTP integration (localhost:8085/data.json)
- LHM config auto-patching, auto-start, 90s polling
- CPU stress test: 60s ramp (20%→100%) + 20s measurement
- Thermal abort at 95°C
- Idle and load temperatures shown in GUI and report

### WiFi Auto-Connect
- Connects to configured shop WiFi on startup if no internet available
- SSID and password stored in settings

### Report Title
- Changed to "System Device Report"

---

## v1.1.0 — 2026-03-04

### CPU Temperature (initial)
- Multi-method temperature collection
- LibreHardwareMonitor integration (first pass)
- Stress test dialog with live temperature display

---

## v1.0.0 — 2026-03-03

### Initial Release
- RepairDesk API integration: ticket resolution, diagnostic note upload
- System spec collection: CPU, RAM, GPU, Storage, Network, Battery, Display, Motherboard, BIOS
- SMART health monitoring via smartctl
- Dark theme with RepairDesk green accent
- Logo integration
- Settings stored in settings.json (portable USB)
- Activity log panel
- Portable USB deployment (vendor/ directory for dependencies)
