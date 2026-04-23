# PC AutoSpec Read Me

## What This App Does

PC AutoSpec is a portable Windows diagnostics and RepairDesk upload tool for repair shops.

It helps you:
- gather quick system details for a ticket
- run a deeper diagnostic scan when needed
- review results before upload
- send formatted notes into RepairDesk
- keep the app updated from GitHub

## Quick Start

1. Open `PCAutoSpec.exe`.
2. Complete the first-time setup if prompted.
3. In `Job Setup`, choose the technician, ticket, upload mode, and test categories.
4. Pick one of these paths:
- `Quick Upload System Details` for a fast hardware summary
- `Start Scan` for a full diagnostic run
- `Skip, Don't Scan` to leave the app idle without running anything
5. Review the results.
6. Upload to RepairDesk when ready.

## Main Buttons And What They Do

### Main Window

- `Job Setup`
  Opens the pre-scan setup window where you enter ticket details, choose upload mode, and pick which diagnostic categories to run.

- `Scan Summary / Upload`
  Opens the review and upload dialog after a scan. This is where you preview the HTML note and send it to RepairDesk.

- `Settings`
  Opens app settings for store details, authentication, updates, Wi-Fi auto-connect, technician list, and help links.

### Job Setup

- `Confirm Ticket`
  Verifies the ticket number against RepairDesk before uploading or scanning.

- `Quick Upload System Details`
  Collects a lightweight system overview and uploads it to the confirmed ticket without running the full diagnostic test set.

- `Start Scan`
  Runs the selected test categories and then lets you review the results before upload.

- `Skip, Don't Scan`
  Closes Job Setup and returns to the main app without running a scan.

- `Select All` / `Deselect All`
  Quickly enable or disable all diagnostic categories.

## Quick Upload Vs Full Scan

### Quick Upload System Details

Quick Upload is for fast intake notes when you only need basic hardware details on the ticket.

It uploads:
- model
- serial number
- current OS version
- CPU
- RAM
- drive information

It does not run the deeper diagnostics like stress testing, event log review, Windows Update checks, or Defender checks.

### Full Scan

Full Scan is for deeper diagnostic work when you want a fuller picture of the machine's condition.

It can include:
- hardware identification
- temperatures
- drive health
- drive speed
- battery health
- event logs
- Windows Update status
- Defender status
- startup programs
- device manager issues
- power and boot details

## What Each Full Test Does

### CPU

Collects:
- processor model
- clock information
- core and thread details
- idle temperature when available
- load temperature during the CPU stress test

The stress test temporarily increases CPU load to check thermal behavior and stability.

### RAM

Collects:
- total installed memory
- memory type when available
- installed module details
- slot population
- basic compatibility observations

### GPU

Collects:
- graphics adapter model
- VRAM when available
- driver details
- live GPU telemetry when available
- GPU temperature data when available

### Motherboard & BIOS

Collects:
- motherboard model
- BIOS version
- chipset or platform data when available
- some memory/platform capability details

### Storage

Collects:
- each drive
- capacity and used/free space
- model / part number
- drive type where available
- SMART health when available
- drive read/write speed when available

### Network & WiFi

Collects:
- Ethernet and Wi-Fi adapters
- MAC address
- link speed
- Wi-Fi SSID and signal when available
- driver details where available

### Display & Webcam

Collects:
- internal and external display details
- panel information when available
- webcam presence and basic function state when available

### Battery

Collects:
- battery presence
- design capacity
- full charge capacity
- wear level
- cycle count when available

### Event Logs

Looks at recent Windows event log issues to help surface crashes, recurring failures, or other warning signs.

### Windows Update

Checks:
- recent Windows Update activity
- failed updates
- pending reboot conditions

### Defender

Checks:
- real-time protection status
- definition age
- last scan details when available

### Startup Items

Collects startup programs so you can quickly spot heavy, unusual, or unnecessary auto-start items.

### Device Manager

Checks for hardware with driver errors, warning states, or missing drivers.

### Power & Boot

Collects:
- active power plan
- uptime
- boot-time related health information when available

## Scan Summary / Upload

After a full scan, open `Scan Summary / Upload` to:
- review the summarized job details
- preview the HTML note
- add or update tech notes
- upload to RepairDesk

Quick uploads skip this longer review flow and send the system overview straight to the confirmed ticket.

## Settings

### Store / RepairDesk Settings

Use Settings to configure:
- store name
- RepairDesk API key or OAuth credentials
- update channel preferences
- technician names
- Wi-Fi auto-connect information

### App Updates

You can:
- check for updates
- download updates
- install updates
- choose whether to include beta builds

When an update is available, the app can prompt you automatically on launch.

### Read Me / Help

Use the `Open Read Me` button in Settings any time you want this guide.

## Notes About Portable Use

- The app is designed to run from a USB or portable folder.
- Settings and logs are stored beside the app.
- If you update from a USB build, the updater should use the portable package instead of registering a full installed app on the host PC.

## Tips

- Use `Quick Upload System Details` for fast intake notes.
- Use `Full Scan` when the machine needs deeper diagnostics.
- If LibreHardwareMonitor or its helper takes a moment to install, give it a few seconds and watch for prompts behind other windows.
- If something looks stuck, check the Activity Log on the right side of the app for clues.

## Logs

Logs are stored in the app folder under `logs`.

If you are reporting a bug, include:
- what you were doing
- whether it was a quick upload or full scan
- the ticket number if relevant
- the latest log file from the `logs` folder
