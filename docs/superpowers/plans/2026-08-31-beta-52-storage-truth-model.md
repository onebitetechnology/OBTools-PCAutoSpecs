# PCAutoSpec beta.52 Storage Truth Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `2.2.45-beta.52` with stable physical drive identity, explicit evidence provenance, and benchmark wording that cannot relabel an HDD as an SSD or an SSD as an HDD.

**Architecture:** Extend the pure hardware-classification layer with separate drive-identity and performance-assessment outputs. Classify only after Windows and SMART evidence are merged, using a documented evidence hierarchy. Make GUI/report consumers read the normalized identity; benchmark consumers display performance bands independently.

**Tech Stack:** Python 3, dataclasses, typed literals, COM/WMI, PowerShell `Get-Disk`, SMART data, pytest, PySide6/report formatting.

**Spec:** [Diagnostic Correctness Remediation Design](../specs/2026-08-31-pcautospec-diagnostic-correctness-design.md)

## Global Constraints

- Begin only after beta.51 passes its USB checkpoint.
- Physical identity and measured performance are independent dimensions.
- A benchmark result must never change `physical_type`.
- Recompute identity after SMART data is merged; never trust a stale pre-SMART `friendly_type`.
- Preserve `Virtual Disk`, `USB`, `Unknown`, unavailable, and collection-failure states distinctly.
- Use only sanitized fixture dictionaries; no raw production logs.
- Preserve SMART health grades, partition warnings, and extended HDD-test behavior.
- Stop after beta.52 qualification before display work.

---

## Task 1: Add audited storage fixtures and the identity contract

**Files:**

- Create: `tests/fixtures/storage_identity_cases.json`
- Create: `tests/test_hardware_classification_storage.py`
- Modify: `src/hardware_classification.py`

- [ ] Add sanitized cases representing:

  - `smart_bus_type=NVMe` plus generic Windows `Fixed hard disk media` => `NVMe SSD` from SMART evidence;
  - SATA bus plus `SSD`/`Solid State` media => `SATA SSD`;
  - SATA bus plus `HDD`/`Hard Disk` media => `HDD`;
  - model `SanDisk X600` plus later SMART `MediaType=SSD, BusType=SATA` => `SATA SSD`;
  - generic Windows data followed by SMART `BusType=NVMe` => `NVMe SSD`;
  - Microsoft/VMware/VirtualBox virtual model => `Virtual Disk`;
  - USB bus => `USB`;
  - conflicting generic fields without authoritative media/bus evidence => `Unknown`.

- [ ] Write failing tests for these interfaces:

```python
DriveType = Literal["NVMe SSD", "SATA SSD", "SSD", "HDD", "Virtual Disk", "USB", "Unknown"]
EvidenceSource = Literal["smart", "windows", "model", "unknown"]


@dataclass(frozen=True)
class DriveIdentity:
    physical_type: DriveType
    evidence_source: EvidenceSource


def classify_drive_identity(drive: Mapping[str, object]) -> DriveIdentity: ...
```

- [ ] Prove the red test:

```bash
python -m pytest tests/test_hardware_classification_storage.py -q
```

Expected: missing drive interfaces.

- [ ] Implement precedence exactly:

  1. virtual-model markers (`MICROSOFT VIRTUAL`, `VMWARE`, `VBOX`, `VIRTUAL DISK`);
  2. authoritative SMART/Get-PhysicalDisk `bus_type` and `media_type`;
  3. strong model markers (`NVME`, `NVM EXPRESS`, `SSD`);
  4. generic Windows media fields;
  5. unknown.

USB bus is authoritative for the connection and returns `USB`. RAID bus plus authoritative solid-state media returns generic `SSD`, not a guessed SATA/NVMe protocol. `available_spare` may support NVMe identity only when it comes from SMART data; its absence cannot imply HDD.

- [ ] Run and commit:

```bash
python -m pytest tests/test_hardware_classification_storage.py -q
git add src/hardware_classification.py tests/fixtures/storage_identity_cases.json tests/test_hardware_classification_storage.py
git commit -m "feat: add evidence-based drive identity"
```

## Task 2: Classify only after SMART merge

**Files:**

- Modify: `src/system_specs.py:3632-3655`
- Modify: `src/system_specs.py:4960-5055`
- Create: `tests/test_system_specs_storage.py`

- [ ] Add a deterministic fake COM/WMI disk plus monkeypatched `_get_disk_smart_structured()`, `_get_disk_bus_type()`, partition-style subprocess, and drive-letter helpers.

- [ ] Test the audited stale-label sequence: initial Windows evidence appears generic/SATA, SMART merge supplies `bus_type="NVMe"`, and the returned drive must contain:

```python
{
    "physical_type": "NVMe SSD",
    "friendly_type": "NVMe SSD",
    "classification_source": "smart",
}
```

- [ ] Add SATA SSD, RAID-backed generic SSD, HDD, USB, virtual, and unavailable-SMART cases. Assert an unavailable SMART result does not become HDD merely because health data is absent.

- [ ] Run the red test:

```bash
python -m pytest tests/test_system_specs_storage.py -q
```

Expected: the stale NVMe case remains the earlier friendly type.

- [ ] Retain `_classify_basic_drive_type()` as a compatibility wrapper that delegates to `classify_drive_identity()` using only supplied basic evidence.

- [ ] Preserve evidence provenance before merging: store initial fields as `windows_media_type`/`windows_bus_type`; copy returned SMART fields to `smart_media_type`/`smart_bus_type`; then retain merged `media_type`/`bus_type` for compatibility. The pure classifier must read the provenance-specific fields first.

- [ ] In `_get_storage_health_structured()`, remove the pre-SMART final `friendly_type` assignment. After preserving provenance and calling `drive_info.update(smart_data)`, classify once and assign `physical_type`, compatibility `friendly_type`, and `classification_source`. Do the same on USB and SMART-unavailable paths.

- [ ] Ensure SMART collection status (`status`, `health_percent`) is not overloaded into physical identity.

- [ ] Run and commit:

```bash
python -m pytest tests/test_hardware_classification_storage.py tests/test_system_specs_storage.py -q
git add src/system_specs.py tests/test_system_specs_storage.py
git commit -m "fix: classify storage after smart evidence merge"
```

## Task 3: Add an independent performance assessment

**Files:**

- Modify: `src/hardware_classification.py`
- Modify: `tests/test_hardware_classification_storage.py`

- [ ] Add this pure interface:

```python
PerformanceBand = Literal["Very fast", "Fast", "Standard", "Slow", "Unavailable"]


@dataclass(frozen=True)
class DrivePerformance:
    band: PerformanceBand
    severity: Literal["success", "info", "warning", "unavailable"]


def assess_drive_performance(
    *, read_mb_s: float | None, write_mb_s: float | None
) -> DrivePerformance: ...
```

- [ ] Write failing boundary tests for the retained read thresholds:

  - above 2000 MB/s => `Very fast`, success;
  - above 400 through 2000 => `Fast`, success;
  - 100 through 400 => `Standard`, info;
  - below 100 => `Slow`, warning;
  - missing/non-positive read speed => `Unavailable`; write speed may be absent without changing a valid read-based band.

- [ ] Assert that `assess_drive_performance()` accepts no physical-type argument and returns no identity wording such as SSD, NVMe, SATA, or HDD.

- [ ] Run red, implement, rerun, and commit:

```bash
python -m pytest tests/test_hardware_classification_storage.py -q
git add src/hardware_classification.py tests/test_hardware_classification_storage.py
git commit -m "feat: separate drive performance from identity"
```

## Task 4: Make report identity consume the normalized truth

**Files:**

- Modify: `src/report_formatter.py:411-470`
- Create: `tests/test_storage_report.py`

- [ ] Write tests that pass a drive with stale/conflicting legacy fields but a normalized `physical_type`. Assert `_classify_drive_type()` always returns `physical_type` first.

- [ ] Add report-section tests for NVMe SSD, SATA SSD, HDD, USB, Virtual Disk, and Unknown. Assert the report never uses read/write speed to choose the type.

- [ ] Run the red test:

```bash
python -m pytest tests/test_storage_report.py -q
```

Expected: legacy `friendly_type` or heuristic branches win in at least one conflict.

- [ ] Change `_classify_drive_type()` to return normalized `physical_type` when present. Keep legacy fallbacks only for older saved result dictionaries.

- [ ] Change `_classify_drive_type_from_line()` and `_classify_drive_type_from_storage_health()` to prefer structured storage health. A free-text line is the last fallback, not equal authority.

- [ ] Run and commit:

```bash
python -m pytest tests/test_storage_report.py tests/test_system_specs_storage.py -q
git add src/report_formatter.py tests/test_storage_report.py
git commit -m "fix: render normalized drive identity in reports"
```

## Task 5: Remove identity names from disk-speed bands

**Files:**

- Modify: `src/panels.py:856-873`
- Modify: `src/panels.py:1870-1890`
- Modify: `src/report_formatter.py:510-535`
- Modify: `src/report_formatter.py:1548-1566`
- Modify: `src/diagnostics/advanced_health.py:1900-1960`
- Create: `tests/test_storage_performance_consumers.py`

- [ ] Write tests for one known NVMe SSD at 350 MB/s and one known HDD at 2200 MB/s. Require:

  - identity remains `NVMe SSD` and `HDD` respectively;
  - performance renders `Standard` and `Very fast` respectively;
  - no output renders `HDD/Slow`, `SATA SSD performance`, or `NVMe SSD performance`;
  - the known slow SSD can still trigger a performance warning without changing type.

- [ ] Run the red test:

```bash
python -m pytest tests/test_storage_performance_consumers.py -q
```

Expected: current benchmark branches output identity-like categories.

- [ ] Replace all speed-category branches with `assess_drive_performance()`. Render a combined row such as:

```text
Disk Speed: 350 MB/s read, 180 MB/s write (Standard performance)
```

- [ ] Keep the existing cached-read-corrected suffix.

- [ ] Rewrite critical issue wording for a known slow SSD to:

```text
Drive Performance: CRITICAL - Example NVMe 1TB (NVMe SSD) measured below the SSD service threshold (Read 350 MB/s, Write 180 MB/s)
```

For an unknown or HDD drive below the generic low threshold, use `Drive Performance: VERY SLOW`; do not append `possible HDD` because identity is already separate.

- [ ] Update comments/docstrings in advanced health so duration/speed bands do not claim a physical protocol.

- [ ] Run and commit:

```bash
python -m pytest tests/test_storage_performance_consumers.py tests/test_storage_report.py -q
git add src/panels.py src/report_formatter.py src/diagnostics/advanced_health.py tests/test_storage_performance_consumers.py
git commit -m "fix: stop benchmark results relabeling drives"
```

## Task 6: Verify every storage consumer and legacy compatibility path

**Files:**

- Modify: affected files found by the search below
- Modify: `tests/test_storage_report.py`
- Modify: `tests/test_storage_performance_consumers.py`

- [ ] Search for every remaining storage identity/speed heuristic:

```bash
rg -n "HDD/Slow|Excellent \(NVMe|Good \(SATA|friendly_type|physical_type|_classify_drive_type|read_mb_s.*2000|read_speed.*2000" src
```

- [ ] For every result, classify it as identity, performance, or legacy compatibility. Update runtime UI/report branches to normalized identity/performance; leave only intentional compatibility fallbacks and historical changelog text.

- [ ] Add one saved beta.51-style dictionary containing only `friendly_type` and confirm the report remains readable. Add one beta.52 dictionary with both fields and confirm `physical_type` wins.

- [ ] Run all storage tests and the whole suite:

```bash
python -m pytest tests/test_hardware_classification_storage.py tests/test_system_specs_storage.py tests/test_storage_report.py tests/test_storage_performance_consumers.py -q
python -m pytest -q
```

Expected: all tests pass and the search finds no runtime benchmark-to-identity wording.

- [ ] Commit any final consumer cleanup:

```bash
git add src tests
git commit -m "refactor: use storage truth model consistently"
```

Skip this commit if the search required no changes.

## Task 7: Version, changelog, and beta.52 qualification

**Files:**

- Modify: `src/settings.py:13`
- Modify: `src/__init__.py:4`
- Modify: `src/CHANGELOG.md`

- [ ] Set both versions to `2.2.45-beta.52`. Document post-SMART classification, evidence provenance, virtual/unknown states, and performance-only speed bands.

- [ ] Run the release gate:

```bash
python -m pytest -q
python -m compileall -q src
git diff --check
```

Expected: complete suite passes.

- [ ] Build through the gated Windows workflow.

- [ ] On controlled USB hardware, test at minimum one NVMe SSD, one SATA SSD, one HDD, one USB drive, and—if safely available—one VM virtual disk. For each, verify the same identity in overview, storage card, report, and critical issues. Run the C: benchmark and confirm it changes only performance wording.

- [ ] Specifically reproduce an audited NVMe device that formerly changed from SATA SSD/HDD to NVMe between runs. Confirm its type remains NVMe even when the measured read speed is below 2000 MB/s.

- [ ] Compare storage identity and performance wording across the GUI, report preview, critical issues, activity log, and locally generated RepairDesk note HTML. They must agree without making a live RepairDesk request.

- [ ] Commit and tag only after every check passes:

```bash
git add src/settings.py src/__init__.py src/CHANGELOG.md
git commit -m "chore: release 2.2.45-beta.52"
git tag -a v2.2.45-beta.52 -m "PCAutoSpec 2.2.45-beta.52"
```

Expected: beta.52 cannot use benchmark speed to rename physical storage.
