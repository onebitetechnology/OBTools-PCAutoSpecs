# PCAutoSpec Diagnostic Correctness Remediation Design

- **Status:** Approved in chat on 2026-08-31
- **Current baseline:** `v2.2.45-beta.49` at commit `5acaad5`
- **Delivery model:** Five staged beta releases with a go/no-go gate between releases

## Problem Statement

An audit of 62 PCAutoSpec USB log files found several cases where the collected evidence was accurate but the application interpreted or labeled it incorrectly. The highest-impact examples were:

- A transient CPU ramp peak was reported as the sustained load temperature.
- Modern Intel mobile processors were parsed as first generation and labeled Windows 10 only.
- Disk benchmark thresholds were presented as physical drive types.
- SMART data could update a drive's media and bus evidence without recomputing its friendly type.
- Internal laptop panels were classified as external monitors.
- A 16.3-inch EDID measurement was snapped to 15.6 inches.
- WMI clock values were presented as turbo frequencies even when contradictory.
- Successful checks could exceed the displayed denominator.
- RepairDesk exception tracebacks could persist an API key even when the outer error message was redacted.
- RepairDesk upload success was visible in the GUI but not persisted as a terminal log event.

The audit also found one corrupted binary-tailed log. The design treats this as a resilience and observability issue rather than assuming a hardware diagnosis.

## Goals

1. Make PCAutoSpec's labels reflect the evidence actually collected.
2. Ensure the GUI, report preview, uploaded RepairDesk note, activity log, and critical-issue list agree.
3. Preserve uncertainty instead of guessing a hardware type, connection, compatibility status, or failure.
4. Prevent credentials from appearing anywhere in logs, including nested exception tracebacks.
5. Establish automated regression coverage before changing diagnostic behavior.
6. Ship the work in independently testable beta releases.

## Non-Goals

- Redesigning the PCAutoSpec interface or report layout.
- Replacing WMI, SMART, EDID, LibreHardwareMonitor, or the existing benchmark collectors.
- Making a live RepairDesk change or upload during automated testing.
- Building a comprehensive external CPU specification database.
- Inferring confirmed thermal throttling from temperature movement alone.
- Reconstructing or modifying historical customer diagnostic notes.

## Design Principles

- Collectors gather evidence; pure normalizers interpret it; renderers present it.
- A physical identity is never inferred from a performance benchmark.
- Explicit evidence outranks heuristic evidence.
- Unknown, unavailable, skipped, and failed are distinct states.
- Existing report structure remains stable unless an approved label must change.
- New result fields are additive during beta migration.
- Raw production logs and credentials are never committed as test fixtures.

## Staged Release Sequence

### beta.50 — Security and Test Foundation

- Add centralized credential redaction for log messages and complete exception chains.
- Cover API keys, authorization headers, bearer tokens, OAuth access and refresh tokens, and credential-bearing query parameters.
- Persist one terminal upload event per confirmed upload: `success`, `cancelled`, or `failed`.
- Log retry attempt number, HTTP status, terminal outcome, and ticket ID without customer details or credentials.
- Add `pytest` and make automated tests run before the Windows build.
- Use mocked RepairDesk responses; make no live RepairDesk request during automated verification.
- Do not change diagnostic output in this beta.

### beta.51 — CPU Correctness

- Correct Intel generation and Windows compatibility parsing.
- Prefer explicit marketing strings such as `13th Gen` over model-number inference.
- Cover older four-digit Core names, modern four-digit mobile names, five-digit HX names, and Core Ultra names.
- Return `Unknown — verify manually` when compatibility evidence is insufficient.
- Separate ramp peak, full-load peak, load median, and overall peak temperatures.
- Stop presenting an inferred temperature drop as confirmed throttling.
- Correct misleading base and turbo clock labels.
- Retain the legacy `peak_temp_c` field temporarily while moving all current consumers to explicit fields.

### beta.52 — Storage Truth Model

- Establish one authoritative physical-drive classifier.
- Recompute the friendly type after SMART data is merged.
- Keep drive identity separate from benchmark performance.
- Prevent benchmark thresholds from changing NVMe, SATA SSD, HDD, virtual disk, or unknown identity.
- Treat unavailable SMART and virtual disks as distinct from physical-drive failure.
- Make the GUI, report, critical issues, and activity log consume the same normalized storage result.

### beta.53 — Display and Panel Accuracy

- Classify internal and external monitors from connection and EDID/WMI evidence, not GPU controller names.
- Prevent a built-in laptop panel from being duplicated as an external monitor.
- Preserve exact EDID dimensions and panel model code.
- Add explicit 16-inch nominal-class support without overwriting the measured value.
- Label ambiguous connections as unknown instead of external.

### beta.54 — Counters, Resilience, and Release Qualification

- Track attempted, passed, failed, unavailable, and skipped checks from one registry.
- Derive numerator and denominator from that registry so impossible counts cannot occur.
- Detect incomplete or corrupted diagnostic-log output and avoid turning it into a hardware diagnosis.
- Run the sanitized historical fixture suite through all normalized classifiers.
- Complete a packaged Windows smoke test and the cross-subsystem USB hardware matrix.
- Treat this beta as the qualification candidate before a stable release.

## Architecture and Component Boundaries

### Evidence Collectors

`src/system_specs.py` and `src/diagnostics/advanced_health.py` remain responsible for gathering WMI, SMART, EDID, benchmark, and temperature evidence. They may preserve raw source values but should not assign presentation labels when the evidence is incomplete.

### New Pure Normalization Modules

#### `src/log_safety.py`

Owns credential redaction, safe exception formatting, and the final logging filter. The filter is a last line of defense and must sanitize nested traceback text as well as ordinary messages.

#### `src/hardware_classification.py`

Owns pure CPU generation and compatibility parsing, CPU clock labeling, storage identity, storage performance assessment, display connection classification, and panel-size normalization. Functions accept recorded evidence and return deterministic structured results without performing WMI, SMART, EDID, network, or filesystem operations.

#### `src/diagnostics/thermal_summary.py`

Owns phase-aware temperature summarization. It receives ramp samples, load samples, sensor information, the thermal limit, abort state, and any independent throttle telemetry. It does not run the stress workload itself.

### Consumers

`src/panels.py`, `src/report_formatter.py`, the critical-issue generators, and activity-log formatting consume normalized results. They do not independently infer drive types, temperature semantics, processor compatibility, or display connection type.

### Test Layout

The implementation creates focused tests under `tests/` and compact sanitized fixtures under `tests/fixtures/`. Fixtures contain only evidence needed for a regression case. They exclude API keys, tokens, ticket numbers, customer names, computer names, serial numbers, and full production logs.

## Behavioral Contracts

### Credential Safety and Upload Observability

- No API key, token, authorization header, or credential-bearing URL may appear in any log record or traceback.
- Upload tests use mocked HTTP behavior and never contact RepairDesk.
- A real RepairDesk upload test requires Jeff's separate approval and a designated test ticket.
- Each confirmed upload emits exactly one terminal event after any retries.
- A terminal event records ticket ID, attempt count, HTTP status when available, and outcome, but no customer details.

### CPU Generation and Windows Compatibility

- An explicit generation string takes precedence over inferred model rules.
- Known model conventions are covered by table-driven tests.
- `Windows 10 only` is emitted only when the evidence positively supports that conclusion.
- Insufficient evidence produces `Unknown — verify manually`.
- Core Ultra processors use their own family representation instead of being forced into the legacy Core generation scheme.

### CPU Temperature

The normalized result contains:

- `ramp_peak_temp_c`
- `load_peak_temp_c`
- `load_median_temp_c`
- `overall_peak_temp_c`
- `thermal_limit_c`
- `aborted`
- `throttling_evidence`, with one of `none`, `suspected`, `confirmed`, or `unavailable`

The ordinary `Temp — Load` value uses `load_peak_temp_c`. A ramp transient is displayed separately when it exceeds the load peak by at least 5°C or reaches 90°C. A temperature drop without independent telemetry can produce only `suspected` throttling. Any phase may still trigger the thermal safety abort at the configured limit.

During beta.51, `peak_temp_c` remains as a compatibility field representing the overall observed peak. Current GUI and report consumers move immediately to the explicit fields. Removal of the compatibility field is deferred until the staged releases confirm no remaining consumer depends on it.

### CPU Clocks

- WMI `MaxClockSpeed` is not labeled turbo unless the source is known to represent maximum turbo frequency.
- Contradictory or unavailable values are omitted rather than guessed.
- A separately collected live clock is labeled as current or observed, not base or turbo.

### Storage Identity and Performance

Physical identity authority is:

1. Explicit SMART or Get-PhysicalDisk media and bus evidence.
2. Specific model markers.
3. Generic Windows media evidence.
4. `Unknown`.

Benchmark speed produces a separate performance assessment and never changes physical identity. The friendly type is recomputed after SMART data is merged. Virtual disks, unavailable SMART, and physical failures remain distinct states.

### Display and Panel

- Connection type comes from monitor connection and EDID/WMI evidence.
- GPU controller names are not used to identify internal panels.
- Ambiguous connection evidence produces `Connection unknown`.
- Exact EDID dimensions and panel model code remain available.
- Nominal size is a separate class and includes 16-inch panels.
- Replacement guidance continues to prioritize panel model code over nominal size.

### Check Counts and Corrupt Data

- Every check records whether it was attempted, passed, failed, unavailable, or skipped.
- The displayed numerator and denominator are calculated from the same attempted-check registry.
- Missing sensors, unavailable SMART, virtual disks, and intentionally skipped categories are not failures.
- Incomplete or corrupted data is reported as such and is never converted into a hardware diagnosis.

## Verification Strategy

### Automated Regression Tests

- Every fix begins with a regression test that fails against the old behavior.
- Tests cover the observed failure and adjacent correct cases.
- CPU fixtures include i5-6300U, i7-1165G7, i7-1195G7, i7-1255U, i7-1355U, i9-14900HX, and Core Ultra 9 185H naming forms.
- Temperature fixtures include ramp-transient cases, sustained-hot cases, flat stable cases, abort cases, missing sensors, and independent throttle evidence.
- Storage fixtures include NVMe at multiple measured speeds, SATA SSD without `SSD` in the model name, HDD, RAID-backed SSD evidence, virtual disk, and unavailable SMART.
- Display fixtures include internal-only laptop, external-only desktop, laptop plus external monitor, ambiguous connection, and 16.3-inch EDID dimensions.
- Logging fixtures include nested exceptions with fake credential values and verify that none survives in rendered log output.

### Cross-Consumer Consistency

One normalized result is passed through the GUI-formatting, report-formatting, critical-issue, and activity-log paths. Tests assert that identity, temperature, compatibility, warning state, and wording agree across consumers.

### Packaged Windows Validation

Each beta must:

1. Pass all automated tests.
2. Build the PyInstaller bundle and installer in the Windows workflow.
3. Launch the packaged application and complete the targeted scan flow.
4. Open the report preview.
5. Close cleanly and produce a readable log.
6. Scan the log for credential patterns, impossible counters, and unexpected exceptions.

### Targeted USB Hardware Matrix

- beta.50: mocked upload success, cancellation, retry, and failure behavior; no live RepairDesk request without separate approval.
- beta.51: the audited i7-1355U system when available and one older Intel system; confirm compatibility, clock wording, all temperature phases, GUI, and preview.
- beta.52: one NVMe SSD, one SATA SSD, and one HDD when available; confirm identity remains fixed across benchmark variation.
- beta.53: an internal-only laptop and a laptop with an external monitor; confirm connection type, count, EDID size, and panel model.
- beta.54: a complete USB scan with all enabled categories and a UI/preview/log comparison.

If required hardware is unavailable, the beta may remain an internal build but is not promoted as validated.

## Go/No-Go Gate

A beta advances only when:

- All automated tests pass.
- The Windows packaged build succeeds.
- The targeted hardware flow passes.
- GUI, preview, critical issues, activity log, and uploaded-note formatting agree for the changed fields.
- No credential pattern appears in logs.
- No new unexplained warning, exception, impossible counter, or corrupted output appears.
- `src/CHANGELOG.md` records the behavior change and any remaining limitation.

Any failed condition blocks promotion and the start of the next beta until the failure is understood and resolved.

## Compatibility and Rollback

- Each beta is independently reviewable and releasable.
- New normalized fields are additive during migration.
- The existing report ordering is preserved.
- A failed hardware gate is handled by retaining the prior validated beta, not by weakening the gate.
- Live RepairDesk behavior is not changed outside the exact approved upload logging and sanitization scope.

## Implementation Planning Decomposition

After this specification is approved, implementation planning is split into five plans matching beta.50 through beta.54. Each plan includes test-first tasks, exact files and interfaces, verification commands, the Windows build gate, the targeted hardware checklist, changelog work, and a commit boundary. No later beta begins before the previous beta's go/no-go gate is satisfied.
