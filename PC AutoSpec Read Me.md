# PC AutoSpec Read Me

## What PC AutoSpec Is

PC AutoSpec is a portable Windows diagnostics and RepairDesk upload tool built for repair shops and refurb workflows.

It is designed to help a tech:
- gather quick system details for a ticket
- run a deeper full diagnostic scan when needed
- review the results before upload
- send formatted HTML notes into RepairDesk
- update the app from GitHub

PC AutoSpec can be used in two main ways:
- `Quick Upload System Details` for fast intake notes
- `Start Scan` for a fuller diagnostic report

## First-Time Setup

When the app is opened for the first time, it may ask for setup details.

Shops can configure:
- store name
- RepairDesk authentication
  - API key, or
  - OAuth 2.0
- optional shop WiFi auto-connect details
- optional technician list

The public build should ship blank. Each shop provides its own credentials.

## Main Window

### `Job Setup`

Opens the pre-scan setup window.

Use this to:
- choose technician
- enter or confirm the ticket number
- choose `Upload System Overview only` or `Upload full results`
- select which test categories should run

### `Scan Summary / Upload`

Opens the post-scan review window.

Use this to:
- review the job details
- preview the HTML upload
- make last-minute edits to upload details
- upload the results to RepairDesk

### `Settings`

Opens configuration and support options, including:
- store / RepairDesk auth settings
- app update channel and updater controls
- technician list
- WiFi settings
- Read Me link
- bug report / feature request helper

## Job Setup

The `Job Setup` window is the main starting point for each machine.

### Buttons

#### `Confirm Ticket`

Checks the RepairDesk ticket before quick upload or full upload.

#### `Quick Upload System Details`

Runs a lightweight collection and uploads the system overview directly to the confirmed ticket.

This is intended for:
- intake
- quick refurb intake notes
- machines where you do not want to run the full diagnostics yet

#### `Start Scan`

Runs the selected diagnostic categories, then leaves the app ready for review in `Scan Summary / Upload`.

#### `Skip, Don't Scan`

Closes Job Setup and leaves the app idle.

#### `Select All` / `Deselect All`

Quickly enable or disable all scan categories.

## Quick Upload vs Full Scan

## Quick Upload System Details

Quick Upload is for fast ticket notes.

It is meant to answer:
- what machine is this?
- what OS is on it?
- what CPU / RAM / storage is inside it?

The uploaded overview is intentionally compact.

It includes:
- model
- serial number
- current OS version
- CPU
- RAM
- drive information
- which tests were not performed

Quick Upload does **not** run the full advanced diagnostics.

After a successful quick upload, the app asks what to do next:
- `Perform Full Scan`
- `Close App / Eject USB`
- `Stay Here`

## Full Scan

Full Scan is for deeper diagnostics and refurb validation.

It can include:
- hardware identification
- CPU/GPU temperatures
- CPU load temperature stress test
- drive health
- drive speed
- battery health
- event logs
- Windows Update status
- manufacturer update tool presence
- Defender status
- startup items
- device manager issues
- power / boot details
- keyboard test

## Test Categories

The checkboxes in `Job Setup` decide which deeper diagnostics should run.

Basic system identification still appears even when deeper checks are skipped.

### CPU

Collects:
- CPU model
- clock information
- core and thread counts
- idle temperature when available
- load temperature during the CPU stress test

The CPU stress test is short and is only meant to observe temperature behavior under load.

There is also a cancel button during the CPU stress popup.

### RAM

Collects:
- total installed memory
- memory type when available
- slot population
- module details

For quick upload, RAM is simplified to practical installed capacity, such as:
- `8GB of DDR4`
- `16GB of DDR4`
- `32GB of DDR5`

### GPU

Collects:
- GPU model
- VRAM when available
- driver details
- live GPU metrics when available
- GPU idle / load temperatures when available

Temperature lines now try to show the sensor source being used.

### Motherboard & BIOS

Collects:
- motherboard model
- BIOS version
- chipset / platform details when available

### Storage

Collects:
- drive model / part number
- drive type
- total capacity
- used / free space
- SMART health when available
- read / write speed when available

Storage formatting is meant to stay technician-friendly. For example:

`C: 256GB/60.35GB Used - Patriot M.2 P320 256GB (NVMe SSD)`

When PC AutoSpec is running from a USB stick, the app's own USB volume is ignored in the storage scan so it does not clutter the results.

#### Manual Extended HDD Test

For HDDs, the Storage section also supports a manual `Extended HDD Test`.

This uses the drive's built-in SMART long self-test. It is:
- non-destructive
- manual
- intended for suspected HDD problems or refurb verification

It is not the same as a destructive write test.

### Keyboard Test

Keyboard Test is a technician-driven popup test.

How it works:
- keys start grey
- key turns green after the first successful press
- key only turns red for near-instant duplicate bounce / ghost presses
- any required key not pressed by the time the test is completed turns red
- click a key to reset it back to grey and test it again
- double-click a key to mark it as not physically present on that keyboard

Result logic:
- `All keys registered` when all standard required keys were pressed
- `Issue - Some Keys not registered` when required keys are missing or repeated

Numpad and some optional keys are treated as optional, so the test does not fail just because the keyboard does not physically include them.

The result appears:
- in the app UI
- in the HTML upload
- in critical issues when there is a keyboard problem

### Network & WiFi

Collects:
- Ethernet adapters
- WiFi adapters
- MAC address
- link speed
- SSID and signal when available
- basic WiFi state

WiFi being disconnected is not treated as a critical issue if the machine already has active Ethernet.

### Display & Webcam

Collects:
- display details
- internal panel details when available
- webcam detection / basic function result when available

### Battery

Collects:
- battery presence
- design capacity
- full charge capacity
- wear level
- cycle count
- power source
- estimated runtime when available

Battery capacities prefer watt-hour values when available.

### Event Logs

Checks recent Windows event log errors and critical events to help spot:
- app crashes
- shutdown/power issues
- recurring system errors

### Windows Update

Checks:
- recent installed updates
- failed update count
- pending reboot state
- available driver updates
- available optional updates

If important Windows driver / optional updates are still available, PC AutoSpec can flag that in critical issues so the tech can install updates and re-run the scan.

### Manufacturer Update Tools

This is a refurb-oriented check.

It currently looks for OEM update tools such as:
- Lenovo Vantage / Lenovo System Update
- Dell Command Update / SupportAssist
- HP Image Assistant / HP Support Assistant

This check tells you whether the relevant manufacturer tool is installed.

It does **not** yet guarantee that all OEM updates have been completed. It is meant to flag whether the machine has the right vendor update path available.

When the support app is missing, PC AutoSpec can flag it in critical issues and provide a vendor download link in the scan summary popup.

### Defender

Checks:
- real-time protection
- antispyware state
- definition age
- last scan details when available

### Startup Items

Collects startup programs so you can quickly spot:
- heavy startup load
- suspicious startup entries
- junk that should be removed during refurb work

### Device Manager

Checks for:
- hardware with warnings
- missing drivers
- device error codes

### Power & Boot

Collects:
- active power plan
- boot timing
- last boot information

## Scan Summary / Upload

`Scan Summary / Upload` is the post-scan review step for a full scan.

Its job is to:
- review the collected results
- preview the HTML note
- make final upload adjustments
- send the note to RepairDesk

This is intentionally different from `Job Setup`.

### Why there are two dialogs

#### `Job Setup`

Used before scanning.

Purpose:
- choose what kind of job this is
- decide quick upload vs full scan
- decide which categories to run
- confirm the ticket

#### `Scan Summary / Upload`

Used after a full scan.

Purpose:
- review the results
- inspect the upload preview
- upload the final report

Quick Upload skips this longer review flow and uploads directly.

## HTML Upload Format

### System Overview Only

The quick upload uses a short top summary such as:

`System Overview: PC AutoSpec Version 2.2.45-beta.xx. Uploaded by: Tech Name`

Then:
- Model
- Serial Number
- Current OS Version
- CPU
- RAM
- Drive Information

### Full Diagnostic Results

The full upload starts with the same compact top block, then continues with:
- Critical Issues
- CPU
- RAM
- GPU
- Motherboard & BIOS
- Storage
- Network & Peripherals
- Monitors
- Battery
- Advanced Diagnostics

The HTML upload also includes the PC AutoSpec app version so you can tell which build generated the note.

## Updates

The app can check GitHub releases for updates.

### Update channels

Users can choose whether to include:
- stable releases only
- beta builds too

### App update flow

In Settings, the update controls work as a step flow:
- `Check for Updates`
- `Download Update`
- `Install Update Now`

The app can also check for updates automatically on launch.

### Portable USB behavior

When running from USB/removable media, the updater prefers the portable ZIP update path instead of the normal Windows installer path, so it does not register itself as a host-installed application unnecessarily.

## RepairDesk Authentication

PC AutoSpec supports:
- legacy API key auth
- OAuth 2.0

Each shop should provide its own credentials.

The public build should not contain anyone else's:
- API key
- OAuth client ID
- OAuth client secret
- access token
- refresh token

## WiFi Auto Connect

WiFi fields are optional.

If used, the app can store shop WiFi information for auto-connect workflows.

If not needed, this section can stay collapsed and blank.

## Technician List

The technician list is dynamic.

You can:
- add as many technician rows as needed
- collapse the section when not needed

## Read Me / Support

In Settings, you can:
- open this Read Me
- use the `Feature Request / Report Bug` helper

The bug report helper opens the default email app and includes:
- PC AutoSpec version
- update channel
- machine name
- latest log path

If needed, manually attach the latest log file from the `logs` folder.

## Logs

Logs are stored beside the app in:

`logs`

If you are reporting a bug, include:
- what you were doing
- whether it was quick upload or full scan
- ticket number if relevant
- whether this was a USB or installed run
- the latest log file

## Known Practical Notes

- LibreHardwareMonitor may depend on its helper installer on some machines. If the app warns you to check for a hidden installer, look behind other windows.
- WinPE is not a fully supported full-diagnostics environment. Basic info may work better there than the full diagnostic set.
- Some drive SMART or OEM update checks depend on what the controller and Windows expose on that machine.

## Basic Workflow Recommendation

### Intake / quick note

1. Open PC AutoSpec
2. Open `Job Setup`
3. Confirm the ticket
4. Choose `Upload System Overview only`
5. Click `Quick Upload System Details`

### Full diagnostic

1. Open `Job Setup`
2. Confirm the ticket
3. Choose `Upload full results`
4. Leave desired categories checked
5. Click `Start Scan`
6. Complete technician-driven prompts like Keyboard Test if requested
7. Open `Scan Summary / Upload`
8. Review the HTML preview
9. Upload to RepairDesk

## Keeping This Guide Current

This Read Me should be updated whenever major workflow, UI, test-category, updater, or upload-format changes are made.
