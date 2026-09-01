# PCAutoSpec beta.53 Display and Panel Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `2.2.45-beta.53` with evidence-based internal/external monitor roles, no duplicate built-in laptop panel, and preserved exact panel dimensions with a correct nominal 16-inch class.

**Architecture:** Normalize WMI monitor connection records into a structured `DisplayDetails` topology. Match EDID/WMI records by normalized instance identifier and reserve `PanelDetails` for the one evidence-backed internal panel. Keep `Display` as a compatibility string derived from non-internal records while migrating GUI/report consumers to structured roles.

**Tech Stack:** Python 3, dataclasses, PowerShell/WMI monitor classes, EDID, COM/WMI, pytest, PySide6/report formatting.

**Spec:** [Diagnostic Correctness Remediation Design](../specs/2026-08-31-pcautospec-diagnostic-correctness-design.md)

## Global Constraints

- Begin only after beta.52 passes its USB checkpoint.
- Never infer panel role from a GPU/video-controller manufacturer or integrated-GPU name.
- Preserve ambiguous monitor connections as `unknown`; do not silently label them external.
- Match monitor evidence by instance/device ID before using list position.
- A built-in panel must appear once, in `PanelDetails`, not again as an external monitor.
- Preserve exact EDID diagonal and model code for parts identification; nominal size is a separate label.
- Keep strict touch-screen detection unchanged.
- Stop after beta.53 qualification before resilience/counter work.

---

## Task 1: Add pure connection-role classification

**Files:**

- Create: `tests/fixtures/display_connection_cases.json`
- Create: `tests/test_hardware_classification_display.py`
- Modify: `src/hardware_classification.py`

- [ ] Add sanitized connection cases for WMI `VideoOutputTechnology` values and expected roles:

  - internal: `6` (LVDS), `11` (embedded DisplayPort), `13` (embedded UDI), `2147483648` (internal);
  - external: `0`–`5`, `8`–`10`, `12`, `14`–`16`;
  - unknown: `-2` (uninitialized), `-1` (other), their unsigned representations, missing, malformed, and unmapped values.

- [ ] Write failing tests for:

```python
DisplayRole = Literal["internal", "external", "unknown"]


@dataclass(frozen=True)
class DisplayConnection:
    role: DisplayRole
    connection_label: str


def classify_display_connection(
    video_output_technology: int | None,
) -> DisplayConnection: ...
```

- [ ] Prove the red test:

```bash
python -m pytest tests/test_hardware_classification_display.py -q
```

Expected: missing display interfaces.

- [ ] Implement the Microsoft `D3DKMDT_VIDEO_OUTPUT_TECHNOLOGY` values explicitly. Use labels already familiar in the report: `Other`, `HD15/VGA`, `S-Video`, `Composite`, `Component`, `DVI`, `HDMI`, `LVDS`, `D-Jpn`, `SDI`, `DisplayPort (External)`, `DisplayPort (Embedded)`, `UDI (External)`, `UDI (Embedded)`, `SDTV Dongle`, `Miracast`, `Indirect Wired`, and `Internal`. Unknown/uninitialized values render `Unknown connection`.

- [ ] Run and commit:

```bash
python -m pytest tests/test_hardware_classification_display.py -q
git add src/hardware_classification.py tests/fixtures/display_connection_cases.json tests/test_hardware_classification_display.py
git commit -m "feat: classify monitor connection roles"
```

## Task 2: Preserve exact panel size and derive nominal class separately

**Files:**

- Modify: `src/hardware_classification.py`
- Modify: `tests/test_hardware_classification_display.py`

- [ ] Add this interface:

```python
@dataclass(frozen=True)
class PanelSize:
    exact_inches: float | None
    nominal_inches: float | None
    display_label: str


def normalize_panel_size(exact_inches: float | None) -> PanelSize: ...
```

- [ ] Write failing tests for exact diagonals `13.3`, `14.0`, `15.6`, `16.0`, `16.3`, and `17.3`. Require `16.3` to remain exact and map to nominal `16.0`, rendering:

```text
16.3 in measured (16-inch class)
```

- [ ] Use nominal classes `(10.1, 11.6, 12.5, 13.3, 14.0, 15.6, 16.0, 17.3, 18.4, 21.5, 24.0, 27.0)`. Choose the nearest only within 0.6 inches; otherwise keep nominal `None` and render the exact measurement alone.

- [ ] Test `None`, zero, and negative inputs as unavailable.

- [ ] Run red, implement, rerun, and commit:

```bash
python -m pytest tests/test_hardware_classification_display.py -q
git add src/hardware_classification.py tests/test_hardware_classification_display.py
git commit -m "fix: preserve exact 16 inch panel measurements"
```

## Task 3: Collect and normalize all monitor connection records

**Files:**

- Modify: `src/system_specs.py:3864-4160`
- Create: `tests/test_system_specs_display.py`

- [ ] Add a private collector with this result shape:

```python
def _get_monitor_connection_records() -> list[dict[str, object]]:
    # Each item:
    {
        "instance_name": "DISPLAY\\CMN15F5\\..._0",
        "display_id": "CMN15F5",
        "video_output_technology": 11,
        "role": "internal",
        "connection_type": "DisplayPort (Embedded)",
    }
```

- [ ] Write tests by monkeypatching the PowerShell subprocess JSON output. Include one embedded DisplayPort panel, one HDMI monitor, one unknown connection, and no-record/failure cases.

- [ ] Run the red test:

```bash
python -m pytest tests/test_system_specs_display.py -q
```

Expected: connection-record collector is absent.

- [ ] Query every `root\wmi:WmiMonitorConnectionParams` record, not `Select-Object -First 1`. Emit compressed JSON. Normalize instance slashes/case and extract the `DISPLAY\<ID>\` component as `display_id`.

- [ ] In PowerShell, test `$null -ne $conn.VideoOutputTechnology` rather than truthiness so valid value `0` (HD15/VGA) is retained. Normalize signed/unsigned values before classification.

- [ ] Classify each record through `classify_display_connection()`. Treat PowerShell failure or invalid JSON as an empty evidence set; log collection failure without guessing roles.

- [ ] Delete `_has_builtin_display_for_logic()` and all GPU-controller/panel-manufacturer role heuristics from `_get_display_info()`.

- [ ] Run and commit:

```bash
python -m pytest tests/test_system_specs_display.py tests/test_hardware_classification_display.py -q
git add src/system_specs.py tests/test_system_specs_display.py
git commit -m "fix: derive display roles from monitor connections"
```

## Task 4: Build a structured display topology and compatibility output

**Files:**

- Modify: `src/system_specs.py:1085-1110`
- Modify: `src/system_specs.py:3864-4200`
- Modify: `tests/test_system_specs_display.py`

- [ ] Define the normalized `DisplayDetails` item shape in tests:

```python
{
    "display_id": "CMN15F5",
    "instance_name": "DISPLAY\\CMN15F5\\..._0",
    "name": "Chimei Innolux CMN15F5 - 1920x1080 @ 60Hz",
    "manufacturer": "Chimei Innolux",
    "model": "CMN15F5",
    "role": "internal",
    "connection_type": "DisplayPort (Embedded)",
}
```

- [ ] Add a laptop topology fixture with one internal eDP panel and one external HDMI monitor. Add an internal-only laptop, external-only desktop, and ambiguous monitor case.

- [ ] Assert:

  - `DisplayDetails` contains each physical monitor once;
  - internal-only laptop has one internal record and no external record;
  - compatibility `Display` contains only external and unknown records, never internal;
  - unknown remains role `unknown` and is not included under an `External` label by structured consumers;
  - video controllers contribute resolution fallback only, never display identity or role.

- [ ] Run the red topology tests:

```bash
python -m pytest tests/test_system_specs_display.py -q
```

Expected: current laptop path has zero internal records and duplicates its panel as external.

- [ ] Refactor `_get_display_info()` to return a tuple `(display_text, display_details)`. Join connection, PnP/EDID, and DesktopMonitor evidence by normalized `display_id`/instance name. Deduplicate by instance name first, then display ID.

- [ ] At the caller, assign both fields:

```python
specs["Display"], specs["DisplayDetails"] = _get_display_info(com_wmi)
```

- [ ] Preserve `Display information unavailable` only when collection yielded no records. An unknown-role monitor is evidence and must remain in `DisplayDetails`.

- [ ] Run and commit:

```bash
python -m pytest tests/test_system_specs_display.py -q
git add src/system_specs.py tests/test_system_specs_display.py
git commit -m "feat: normalize display topology"
```

## Task 5: Match PanelDetails to the evidence-backed internal connection

**Files:**

- Modify: `src/system_specs.py:1170-1195`
- Modify: `src/system_specs.py:6298-6475`
- Modify: `tests/test_system_specs_display.py`

- [ ] Change the interface to accept the selected internal display identifier:

```python
def _get_panel_details(internal_instance_name: str | None = None) -> dict[str, object] | None: ...
```

- [ ] Write a test with an external HDMI record returned first and an internal embedded-DisplayPort record second. Require panel manufacturer, model code, dimensions, modes, and connection to come from the internal record.

- [ ] Require the result fields:

```python
{
    "instance_name": "...",
    "model_code": "CMN15F5",
    "size_inches_exact": 16.3,
    "size_inches_nominal": 16.0,
    "size_inches": 16.0,
    "size_display": "16.3 in measured (16-inch class)",
    "connection_role": "internal",
}
```

- [ ] Run the red test and confirm current `Select-Object -First 1` can select the external monitor.

- [ ] Update the PowerShell to query all `WmiMonitorID`, `WmiMonitorBasicDisplayParams`, `WmiMonitorListedSupportedSourceModes`, and `WmiMonitorConnectionParams` records, matching by normalized `InstanceName`. If no internal identifier is available, return `None` rather than selecting an arbitrary external monitor.

- [ ] Calculate only the exact diagonal in PowerShell. Apply `normalize_panel_size()` in Python and populate exact, nominal, compatibility `size_inches`, and display label.

- [ ] Preserve manufacturer, model, serial, resolution, manufacturing date, and strict touch detection.

- [ ] Run and commit:

```bash
python -m pytest tests/test_system_specs_display.py tests/test_hardware_classification_display.py -q
git add src/system_specs.py tests/test_system_specs_display.py
git commit -m "fix: bind panel details to internal edid"
```

## Task 6: Render internal, external, and unknown displays once

**Files:**

- Modify: `src/panels.py:1120-1230`
- Modify: `src/report_formatter.py:1651-1750`
- Create: `tests/test_display_consumers.py`

- [ ] Write UI/report tests using structured laptop topology. Require:

  - one `Built-in Panel` entry;
  - `Panel Model`/part number retained;
  - exact and nominal size both visible;
  - HDMI monitor appears once under `External`/`External Displays`;
  - unknown-role monitor appears once under `Unclassified`/`Unclassified Displays`;
  - internal panel never appears in either later section.

- [ ] Add desktop and legacy-saved-result cases. Structured data wins when present; legacy `Display` string remains a fallback when `DisplayDetails` is absent.

- [ ] Run the red test:

```bash
python -m pytest tests/test_display_consumers.py -q
```

Expected: existing consumers treat every `Display` line as external on laptops.

- [ ] Update both consumers to group `DisplayDetails` by role. Render PanelDetails first, then external, then unknown. Use `size_display` for the primary size and preserve centimeter dimensions as supporting evidence.

- [ ] Do not render an empty `External Displays` heading on an internal-only laptop.

- [ ] Run and commit:

```bash
python -m pytest tests/test_display_consumers.py tests/test_system_specs_display.py -q
git add src/panels.py src/report_formatter.py tests/test_display_consumers.py
git commit -m "fix: render each monitor once by connection role"
```

## Task 7: Version, changelog, and beta.53 qualification

**Files:**

- Modify: `src/settings.py:13`
- Modify: `src/__init__.py:4`
- Modify: `src/CHANGELOG.md`

- [ ] Set both versions to `2.2.45-beta.53`. Document connection-based roles, built-in deduplication, EDID matching, and exact/nominal panel sizes.

- [ ] Run the release gate:

```bash
python -m pytest -q
python -m compileall -q src
git diff --check
```

Expected: complete suite passes.

- [ ] Build through the gated Windows workflow.

- [ ] Test on controlled USB hardware: an internal-only laptop, a laptop with HDMI/DisplayPort external monitor, a desktop with one monitor, and if available a multi-monitor desktop. Verify each monitor appears once with the right role and connection.

- [ ] Recheck the two audited 16.3-inch cases. Confirm the app preserves `16.3 in measured`, shows `16-inch class`, and retains the exact panel model for ordering.

- [ ] Disconnect/reconnect the external laptop monitor and rerun. Confirm the built-in panel remains internal and the external list changes predictably.

- [ ] Compare panel role, model, and size across the GUI, report preview, activity log, and locally generated RepairDesk note HTML. They must agree without making a live RepairDesk request.

- [ ] Commit and tag only after every check passes:

```bash
git add src/settings.py src/__init__.py src/CHANGELOG.md
git commit -m "chore: release 2.2.45-beta.53"
git tag -a v2.2.45-beta.53 -m "PCAutoSpec 2.2.45-beta.53"
```

Expected: beta.53 reports panel identity/size accurately without duplicate or guessed external displays.
