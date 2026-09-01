# PCAutoSpec beta.51 CPU Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `2.2.45-beta.51` with correct Intel generation/Windows compatibility, honest CPU clock labels, and separate ramp versus sustained-load temperature reporting.

**Architecture:** Move CPU-name, clock, and thermal interpretation into pure functions with typed immutable outputs. Keep collectors responsible for raw WMI/registry/sensor evidence. Preserve `peak_temp_c` temporarily as the overall peak, while every UI/report/issue consumer switches to explicit ramp and load fields.

**Tech Stack:** Python 3, dataclasses, regular expressions, `statistics.median`, COM/WMI, LibreHardwareMonitor, pytest, PySide6.

**Spec:** [Diagnostic Correctness Remediation Design](../specs/2026-08-31-pcautospec-diagnostic-correctness-design.md)

## Global Constraints

- Begin only after beta.50 passes its USB checkpoint.
- Do not add runtime web lookups or mutable remote CPU data.
- Unknown CPUs emit `Unknown — verify manually`; do not guess compatibility.
- Treat Intel Core Ultra as a separate family.
- WMI `MaxClockSpeed` is `WMI max`, never turbo without authoritative evidence.
- A temperature drop alone is only suspected throttling; confirmation needs independent evidence.
- Keep GPU temperature behavior unchanged.
- Stop after beta.51 qualification before beginning storage work.

---

## Task 1: Add audited CPU identity fixtures and a pure classifier

**Files:**

- Create: `tests/fixtures/cpu_identity_cases.json`
- Create: `tests/test_hardware_classification_cpu.py`
- Create: `src/hardware_classification.py`

- [ ] Add fixture rows for these exact outcomes:

| CPU | Family | Generation | Number | Compatibility |
|---|---|---|---:|---|
| Intel Core i5-6300U | Intel Core | 6th Gen | 6 | Windows 10 only |
| Intel Core i7-1165G7 | Intel Core | 11th Gen | 11 | Windows 11 compatible |
| Intel Core i7-1195G7 | Intel Core | 11th Gen | 11 | Windows 11 compatible |
| Intel Core i7-1255U | Intel Core | 12th Gen | 12 | Windows 11 compatible |
| 13th Gen Intel Core i7-1355U | Intel Core | 13th Gen | 13 | Windows 11 compatible |
| Intel Core i9-14900HX | Intel Core | 14th Gen | 14 | Windows 11 compatible |
| Intel Core Ultra 9 185H | Intel Core Ultra | Core Ultra Series 1 | null | Windows 11 compatible |
| Intel Core i7 processor | Intel Core | Unknown | null | Unknown — verify manually |

- [ ] Write failing tests for this exact interface:

```python
@dataclass(frozen=True)
class CpuIdentity:
    family: str
    generation: str
    generation_number: int | None
    windows_compatibility: str


def classify_intel_cpu(cpu_name: str) -> CpuIdentity: ...
```

- [ ] Prove the red test:

```bash
python -m pytest tests/test_hardware_classification_cpu.py -q
```

Expected: `ModuleNotFoundError` for `hardware_classification`.

- [ ] Implement parsing precedence: Core Ultra, explicit ordinal generation phrase, Core model-plus-suffix, unknown. For five-digit models `10000`–`14999`, use the first two digits. For four-digit mobile models `1100`–`1499` with a suffix, use the first two digits; otherwise use the first digit. Format 11th/12th/13th ordinals correctly.

- [ ] Run and commit:

```bash
python -m pytest tests/test_hardware_classification_cpu.py -q
git add src/hardware_classification.py tests/fixtures/cpu_identity_cases.json tests/test_hardware_classification_cpu.py
git commit -m "feat: classify modern intel cpu generations"
```

Expected: every audited CPU case passes.

## Task 2: Integrate CPU identity without regressing exact database matches

**Files:**

- Modify: `src/system_specs.py:1368-1830`
- Create: `tests/test_system_specs_cpu.py`

- [ ] Write characterization tests for `_get_cpu_enhanced_details()` using the fixture table. Add `Intel Core i9-9900K` and assert its architecture, year, socket, RAM support, TDP, and upgrade data stay unchanged.

- [ ] Run the test and capture the current modern-mobile failures:

```bash
python -m pytest tests/test_system_specs_cpu.py -q
```

Expected: 11th/12th/13th/14th mobile models expose the first-digit bug.

- [ ] Keep exact `CPU_DATABASE` matches authoritative for detailed fields. Replace only the generic Intel Core generation/compatibility fallback with `classify_intel_cpu()`.

- [ ] Map classifier unknown to `generation="Unknown"` and `windows_compatibility="Unknown — verify manually"`. Remove the optimistic “modern series” compatibility guess.

- [ ] Run and commit:

```bash
python -m pytest tests/test_hardware_classification_cpu.py tests/test_system_specs_cpu.py -q
git add src/system_specs.py tests/test_system_specs_cpu.py
git commit -m "fix: report cpu generation and windows compatibility"
```

## Task 3: Normalize CPU clock evidence and labels

**Files:**

- Modify: `src/hardware_classification.py`
- Modify: `tests/test_hardware_classification_cpu.py`

- [ ] Add failing tests for this type and function:

```python
@dataclass(frozen=True)
class CpuClockSummary:
    base_ghz: float | None
    boost_ghz: float | None
    current_ghz: float | None
    wmi_max_ghz: float | None

    def display_parts(self) -> tuple[str, ...]: ...


def normalize_cpu_clocks(
    *,
    authoritative_base_mhz: int | None,
    authoritative_boost_mhz: int | None,
    current_mhz: int | None,
    wmi_max_mhz: int | None,
) -> CpuClockSummary: ...
```

- [ ] Assert authoritative values render as `Base` and `Boost`; WMI-only maximum renders as `WMI max`; current clock renders as `Current` when distinct; duplicates appear once; and a 1.70 GHz WMI maximum below a 2.61 GHz base is never called turbo.

- [ ] Run the red test, implement MHz-to-GHz normalization with display order `Base`, `Boost`, `Current`, `WMI max`, then rerun:

```bash
python -m pytest tests/test_hardware_classification_cpu.py -q
```

Expected: all clock provenance cases pass.

- [ ] Commit:

```bash
git add src/hardware_classification.py tests/test_hardware_classification_cpu.py
git commit -m "fix: label cpu clock evidence accurately"
```

## Task 4: Integrate honest clock labels into CPU collection

**Files:**

- Modify: `src/system_specs.py:2060-2115`
- Modify: `tests/test_system_specs_cpu.py`

- [ ] Add a fake COM/WMI processor and monkeypatch `_get_base_clock_from_registry()` and `_parse_cpu_boost_speed()` so `_get_cpu_info()` is deterministic.

- [ ] Assert these shapes:

```text
Known: | Base: 2.60 GHz | Boost: 4.40 GHz
Fallback: | Current: 2.61 GHz | WMI max: 1.70 GHz
```

The fallback must not contain `Turbo:`.

- [ ] Run the test and confirm current output fails with a misleading turbo label:

```bash
python -m pytest tests/test_system_specs_cpu.py -q
```

- [ ] Replace manual clock string assembly with `normalize_cpu_clocks(...).display_parts()`. Keep the CPU name and `(coresC/threadsT)` shape stable.

- [ ] Run and commit:

```bash
python -m pytest tests/test_hardware_classification_cpu.py tests/test_system_specs_cpu.py -q
git add src/system_specs.py tests/test_system_specs_cpu.py
git commit -m "fix: stop presenting wmi max as cpu turbo"
```

## Task 5: Create the thermal-summary truth model

**Files:**

- Create: `src/diagnostics/thermal_summary.py`
- Create: `tests/test_thermal_summary.py`
- Create: `tests/fixtures/cpu_thermal_cases.json`

- [ ] Add the audited representative case: ramp `[55, 72, 95]`, load `[74, 75, 74, 75]`, limit `100`, not aborted, independent throttle evidence unavailable. Add adjacent cases for sustained-hot load `[91, 92, 92]`, flat stable ramp/load near 75°C, thermal abort, empty/missing sensor phases, and independent throttle confirmation.

- [ ] Write failing tests for:

```python
def summarize_cpu_temperature(
    *,
    ramp_samples: Sequence[float],
    load_samples: Sequence[float],
    thermal_limit_c: float,
    aborted: bool,
    independent_throttle_detected: bool | None,
) -> dict[str, object]: ...
```

- [ ] Require these result fields: `ramp_peak_temp_c`, `load_peak_temp_c`, `load_median_temp_c`, `overall_peak_temp_c`, `peak_temp_c`, `thermal_limit_c`, `aborted`, and `throttling_evidence`.

- [ ] For the representative case assert ramp `95.0`, load peak `75.0`, load median `74.5`, overall/compatibility peak `95.0`, and `suspected` throttling.

- [ ] Test throttling states: independent true => `confirmed`; independent false => `none`; no independent evidence plus >10°C drop => `suspected`; no independent evidence without the drop => `unavailable`.

- [ ] Test empty phases return `None` values without raising. Add:

```python
def should_show_ramp_peak(summary: Mapping[str, object]) -> bool: ...
```

It returns true when ramp peak is at least 5°C above load peak or at least 90°C.

- [ ] Run red, implement with `max()` and `statistics.median()` rounded to one decimal, and rerun:

```bash
python -m pytest tests/test_thermal_summary.py -q
```

Expected: all truth-model cases pass; `peak_temp_c` equals overall peak only as a documented compatibility field.

- [ ] Commit:

```bash
git add src/diagnostics/thermal_summary.py tests/test_thermal_summary.py tests/fixtures/cpu_thermal_cases.json
git commit -m "feat: separate ramp and load cpu temperatures"
```

## Task 6: Return explicit fields from the stress collector

**Files:**

- Modify: `src/diagnostics/advanced_health.py:1287-1495`
- Create: `tests/test_cpu_temp_collection.py`

- [ ] Add deterministic tests by monkeypatching multiprocessing, monotonic time, sleep, queues, and `_collect_cpu_temp_info()`. Cover normal 95/75 collection, thermal-limit abort during ramp, cancel during ramp, and cancel during load.

- [ ] Assert every successful path includes the full summary. Cancelled paths preserve collected evidence and never invent a load peak.

- [ ] Run the red test:

```bash
python -m pytest tests/test_cpu_temp_collection.py -q
```

Expected: explicit ramp/load fields are missing.

- [ ] Track `ramp_samples`; pass both phase arrays to `summarize_cpu_temperature()` at every return after collection begins. Retain `samples` as the load list for compatibility.

- [ ] Replace drop-based `throttling_detected=True` with `throttling_evidence="suspected"`. Retain a temporary boolean only as `throttling_evidence == "confirmed"` until Task 7 migrates all consumers.

- [ ] Change log wording from `thermal throttling detected` to `possible throttling; independent confirmation unavailable` for a drop-only case.

- [ ] Run and commit:

```bash
python -m pytest tests/test_thermal_summary.py tests/test_cpu_temp_collection.py -q
git add src/diagnostics/advanced_health.py tests/test_cpu_temp_collection.py
git commit -m "fix: preserve cpu ramp and sustained load evidence"
```

## Task 7: Switch UI, report, and issues to load truth

**Files:**

- Modify: `src/panels.py:544-563`
- Modify: `src/report_formatter.py:671-682`
- Modify: `src/report_formatter.py:885-899`
- Modify: `src/report_formatter.py:984-1000`
- Modify: `src/report_formatter.py:1210-1223`
- Modify: `src/diagnostics/advanced_health.py:2388-2405`
- Create: `tests/test_cpu_temperature_consumers.py`

- [ ] Write tests using the 95°C ramp / 75°C load summary. Assert UI and report primary lines use `Temp — Load: 75°C`, a separate line/suffix shows `Transient ramp peak: 95°C`, and critical issues do not call the load high or critical.

- [ ] Assert suspected wording is `possible throttling`; only confirmed evidence says `throttling confirmed`. Add an aborted-at-limit case that uses overall peak in the critical issue.

- [ ] Run the red tests:

```bash
python -m pytest tests/test_cpu_temperature_consumers.py -q
```

Expected: current consumers incorrectly show 95°C as load.

- [ ] Search all CPU consumers:

```bash
rg -n "cpu_load_temp|throttling_detected|peak_temp_c" src/panels.py src/report_formatter.py src/diagnostics/advanced_health.py
```

- [ ] Use `load_peak_temp_c` for load labels and 75/90 thresholds. Use `overall_peak_temp_c` only for a thermal-limit abort. Show ramp separately through `should_show_ramp_peak()`. Do not change GPU branches.

- [ ] A suspected drop alone must not recommend cooling service. High sustained load or a thermal-limit abort can preserve existing cooling guidance.

- [ ] Run and commit:

```bash
python -m pytest tests/test_hardware_classification_cpu.py tests/test_system_specs_cpu.py tests/test_thermal_summary.py tests/test_cpu_temp_collection.py tests/test_cpu_temperature_consumers.py -q
git add src/panels.py src/report_formatter.py src/diagnostics/advanced_health.py tests/test_cpu_temperature_consumers.py
git commit -m "fix: report sustained cpu load temperature"
```

## Task 8: Version, changelog, and beta.51 qualification

**Files:**

- Modify: `src/settings.py:13`
- Modify: `src/__init__.py:4`
- Modify: `src/CHANGELOG.md`

- [ ] Set both versions to `2.2.45-beta.51` and document CPU generation, clock provenance, and ramp/load separation.

- [ ] Run the release gate:

```bash
python -m pytest -q
python -m compileall -q src
git diff --check
```

Expected: complete suite passes; compile and diff checks are clean.

- [ ] Build through the gated Windows workflow.

- [ ] Test one 6th-generation Intel machine and at least one 11th–14th-generation or Core Ultra machine from a controlled USB. Verify classification, compatibility, clock labels, separate ramp/load values, abort handling, and unchanged GPU output. Save only sanitized acceptance evidence.

- [ ] Compare the CPU identity and thermal wording across the GUI, report preview, critical-issue list, activity log, and locally generated RepairDesk note HTML. They must agree without making a live RepairDesk request.

- [ ] Commit and tag only after every check passes:

```bash
git add src/settings.py src/__init__.py src/CHANGELOG.md
git commit -m "chore: release 2.2.45-beta.51"
git tag -a v2.2.45-beta.51 -m "PCAutoSpec 2.2.45-beta.51"
```

Expected: beta.51 resolves CPU identity and thermal-truth errors without storage/display changes.
