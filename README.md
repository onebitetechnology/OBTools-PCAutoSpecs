# PC AutoSpec

Automated PC diagnostics for RepairDesk repair shops.

Run it on a customer's machine, get a full hardware report, upload it straight to a RepairDesk ticket as a formatted diagnostic note. One click.

---

## Quick Start

1. Extract the ZIP to a USB stick (or any folder)
2. Double-click **`PCAutoSpec.bat`**
3. Windows will ask for admin permissions (needed for hardware access) — click Yes
4. First run: you'll be prompted to enter your store name and RepairDesk API key
5. Scan runs automatically — enter a ticket number and upload

That's it. No installation. No Python to install. Everything is bundled.

If you're working from source or publishing the project, use `settings.example.json`
as the template and keep your real `settings.json` private.

---

## What's in the folder

```
PCAutoSpec/
  PCAutoSpec.bat            <- double-click this to run
  run.py                    <- Python entry point
  requirements.txt          <- dependency list (for running from source)
  settings.json             <- your API key + store name (local only)
  python/                   <- bundled Python 3.12 runtime + all dependencies
  src/                      <- source code (readable, editable)
    AutoSpecUploaderGUI.py  <- main window
    system_specs.py         <- hardware detection engine
    panels.py               <- system info + activity log panels
    dialogs.py              <- settings + report preview dialogs
    widgets.py              <- reusable UI components
    workers.py              <- background threads
    theme.py                <- dark theme colors + QSS stylesheet
    report_formatter.py     <- HTML diagnostic report generator
    assessments.py          <- health grading (SMART, battery, drivers)
    repairdesk_api.py       <- RepairDesk API client
    settings.py             <- portable settings management
    config.py               <- config bridge
    smartctl.exe            <- bundled SMART query tool
    diagnostics/
      advanced_health.py    <- event log, Windows Update, Defender checks
```

**`settings.json` lives next to the bat file — on YOUR USB, not on the customer's PC.** The customer's machine is never modified.

---

## What It Does

- Detects CPU, RAM, GPU, storage, network, display, battery, motherboard, BIOS, serial numbers
- Reads SMART data and gives you a health grade (A+ to F) with plain-English explanations
- Handles Intel RST controllers — uses CSMI passthrough for SATA, Windows health API for NVMe behind RAID
- Flags critical issues (failing drives, high temps, device errors, degraded batteries)
- Checks drivers, Windows Update status, event log errors, Defender, UAC
- GPU temperature monitoring via nvidia-smi (NVIDIA cards)
- Disk speed benchmarks (sequential read/write)
- Formats everything into a clean diagnostic note and uploads it to your RepairDesk ticket
- Can check for newer installer releases from GitHub and download/apply updates from Settings

--- 

## Getting Your RepairDesk API Key

1. Log into your RepairDesk account
2. Go to **Settings > Integrations > API**
3. Copy your API key
4. Paste it into PC AutoSpec when prompted on first run (or click the gear icon later)

The API key is stored locally in `settings.json` on your USB. It is never sent anywhere except to the RepairDesk API.

---

## What Gets Uploaded

A formatted diagnostic note added to the ticket you specify. Includes:

- System overview (type, model, serial, computer name)
- CPU details (model, cores, threads, clock speeds)
- RAM (total, slots populated, speed, type)
- GPU (model, VRAM, driver version, temperature)
- Storage health per drive (SMART grade, capacity, power-on hours, temperature)
- Disk usage per partition (% used)
- Disk speed results (sequential read/write MB/s)
- Battery health (laptops — capacity, wear, cycles)
- Network adapters and driver versions
- Display/panel info
- OS, uptime, boot time
- Driver status and device errors
- Event log summary with specific crash details (last 7 days)
- Advanced diagnostics: Defender, startup impact, power plan, boot speed
- Flagged issues and recommendations

The note is uploaded as an **internal/private note** — customers don't see it.

You can edit the HTML in the preview dialog before uploading.

---

## System Requirements

- **OS**: Windows 10 or 11
- **Permissions**: Administrator recommended (required for SMART data, WMI hardware queries, event logs)
- **Internet**: Required for RepairDesk upload (diagnostics collection works offline)

---

## How It Runs (Bundled vs From Source)

### Bundled runtime (what's in the ZIP)

The `python/` folder contains a complete Python 3.12 runtime with all dependencies pre-installed. `PCAutoSpec.bat` points to this bundled Python. You don't need Python installed on your machine or the customer's machine. Extract, run, done.

This is the recommended way to use PC AutoSpec. Put it on a USB stick and carry it between machines.

### Running from source (developers)

If you already have Python 3.10+ installed and want to run from source:

```
pip install -r requirements.txt
python run.py
```

You still need `smartctl.exe` in the `src/` folder for SMART data (it's included).

### Building a standalone exe (advanced)

You can use PyInstaller to build a single exe or onedir bundle:

```
pip install pyinstaller
pyinstaller --clean PCAutoSpec.spec
```

Note: PySide6 exe builds are large. The bundled runtime approach (what ships in the ZIP)
is lighter and easier to update, but the spec file in this repo is the better starting
point for repeatable release builds.

### Building a Windows installer

This repo includes:

- `PCAutoSpec.spec` for PyInstaller
- `installer/PCAutoSpec.iss` for Inno Setup
- `scripts/build_windows_release.ps1` to build both on Windows

On a Windows dev machine:

```powershell
py -3.12 -m venv .venv
.\scripts\build_windows_release.ps1
```

That produces:

- `dist/PCAutoSpec/` — portable PyInstaller bundle
- `release/PCAutoSpec-Setup.exe` — Windows installer

### GitHub / Release Prep

Before publishing:

1. Copy `settings.example.json` to `settings.json` locally and add your real credentials there.
2. Do not commit `settings.json`, `logs/`, `python/`, or `vendor/`.
3. The repo includes `.github/workflows/windows-release.yml` and builds on every push to `main`.
4. Tags like `v2.2.8` publish GitHub Release assets for the in-app updater to download.

---

## Customization

### Theme

The entire look is controlled by `src/theme.py`. The `COLORS` dictionary at the top defines every color in the app — backgrounds, text, buttons, borders, status colors. Change the values and restart.

Key colors to change for your shop's branding:
- `primary` — the main accent color (default: `#10B981` green)
- `primary_hover` and `primary_active` — button hover/click states
- `bg_root` — main background
- `card_bg` — panel/card backgrounds

The `build_stylesheet()` function generates the full QSS (Qt stylesheet) from those colors.

### Store name

Set your store name in `settings.json` or via the settings dialog (gear icon). This appears in the diagnostic report title.

### API endpoint

If you use a custom RepairDesk instance, change `api_base_url` in `settings.json`.

---

## Known Limitations

- **NVMe behind Intel RST**: Most modern laptops use Intel RST, which blocks direct SMART access on NVMe drives. PC AutoSpec falls back to Windows health data (Healthy/Warning/Unhealthy) — you'll get a health status but not detailed SMART attributes like power-on hours or temperature. SATA drives behind RST work fully via CSMI passthrough.
- **USB drives**: Automatically skipped for SMART checks (not useful for diagnostics).
- **WinPE**: Designed for live Windows. Some features (event logs, Windows Update status) won't work in a PE environment.
- **GPU temps**: NVIDIA uses nvidia-smi (comes with NVIDIA drivers). AMD is attempted via WMI but depends on driver support. Intel integrated GPU temperature is not currently collected.

---

## License

MIT — do what you want with it. See LICENSE.

---

## Credits

Built by a repair tech, for repair techs. Open-sourced for the RepairDesk community.

[Elite Repairs](https://eliterepairs.com.au) — Gladstone, QLD
