# PCAutoSpec beta.54 Resilience and Release Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `2.2.45-beta.54` with truthful advanced-check counters, explicit corrupt/incomplete-log handling, and a repeatable qualification matrix covering all four preceding beta remediations.

**Architecture:** Register every advanced check once, record one normalized outcome per registry entry, and compute counts from those same records. Add pure byte-level log-integrity inspection plus startup/finalization integration that keeps storage corruption separate from hardware diagnosis. Finish with automated release assertions and a durable USB acceptance matrix.

**Tech Stack:** Python 3, dataclasses, enums/literals, standard logging/pathlib, pytest, GitHub Actions, PowerShell build script, Windows USB qualification.

**Spec:** [Diagnostic Correctness Remediation Design](../specs/2026-08-31-pcautospec-diagnostic-correctness-design.md)

## Global Constraints

- Begin only after beta.53 passes its USB checkpoint.
- One registry defines check names and the denominator; do not count arbitrary result dictionaries.
- `attempted = passed + failed + unavailable`; `skipped` is reported separately.
- A cancelled technician-driven stress test counts as skipped, not failed.
- Corrupt/incomplete log output is an application/storage-integrity state, never a hardware diagnosis.
- Never repair, truncate, overwrite, or delete an affected log automatically.
- Qualification evidence must be sanitized and contain no RepairDesk/customer secrets.
- Do not tag beta.54 until the complete beta.50–beta.54 regression matrix passes.

---

## Task 1: Define normalized advanced-check outcomes

**Files:**

- Create: `src/diagnostics/check_summary.py`
- Create: `tests/test_check_summary.py`

- [ ] Write failing tests for these interfaces:

```python
CheckOutcome = Literal["passed", "failed", "unavailable", "skipped"]


@dataclass(frozen=True)
class CheckRecord:
    key: str
    label: str
    outcome: CheckOutcome

    @property
    def attempted(self) -> bool:
        return self.outcome != "skipped"


@dataclass(frozen=True)
class CheckCounts:
    registered: int
    attempted: int
    passed: int
    failed: int
    unavailable: int
    skipped: int


def normalize_check_outcome(value: object) -> CheckOutcome: ...


def summarize_check_records(records: Sequence[CheckRecord]) -> CheckCounts: ...
```

- [ ] Require status mapping:

  - dict `status="ok"` or a numeric scalar => passed;
  - dict `status` in `error`, `failed`, `critical` => failed;
  - dict `status="unavailable"`, `None`, or unrecognized/missing status => unavailable;
  - dict `status` in `skipped`, `cancelled` => skipped.

- [ ] Require count invariants:

```python
registered == passed + failed + unavailable + skipped
attempted == passed + failed + unavailable
```

- [ ] Test an empty registry, duplicate keys (must raise `ValueError`), and mixed outcomes.

- [ ] Prove red, implement, and rerun:

```bash
python -m pytest tests/test_check_summary.py -q
```

Expected: all normalization and invariant cases pass.

- [ ] Commit:

```bash
git add src/diagnostics/check_summary.py tests/test_check_summary.py
git commit -m "feat: normalize advanced check outcomes"
```

## Task 2: Use one advanced-check registry and truthful counters

**Files:**

- Modify: `src/diagnostics/advanced_health.py:2259-2500`
- Create: `tests/test_advanced_health_summary.py`

- [ ] Define `ADVANCED_CHECK_REGISTRY` once with these 14 public checks: `event_viewer`, `windows_update`, `defender`, `temperatures`, `startup_impact`, `device_manager`, `power_plan`, `boot_time`, `wifi`, `webcam`, `disk_speed`, `cpu_load_temp`, `memory_temp_c`, and `gpu_load_temp`.

- [ ] Add deterministic tests monkeypatching every collector. Cover all pass, mixed pass/failure/unavailable, category skips, no dedicated GPU, and technician-cancelled CPU stress.

- [ ] Require a result metadata field:

```python
results["_check_summary"] = {
    "registered": 14,
    "attempted": 11,
    "passed": 8,
    "failed": 1,
    "unavailable": 2,
    "skipped": 3,
}
```

- [ ] Require the progress line shape:

```text
Advanced health: 8/11 attempted checks passed; 1 failed; 2 unavailable; 3 skipped
```

- [ ] Run the red test:

```bash
python -m pytest tests/test_advanced_health_summary.py -q
```

Expected: current code reports a mismatched `successful/len(filtered checks)` value.

- [ ] Refactor execution through a local `record_result(key, value)` helper. Every registry key must be recorded exactly once, including skipped and unavailable dynamic checks. Keep `_device_manager_errors` as derived metadata and exclude underscore-prefixed metadata from the registry.

- [ ] For memory temperature, retain the public scalar value but normalize a number as passed and `None` as unavailable when building `CheckRecord`.

- [ ] Use `summarize_check_records()` for both `_check_summary` and the progress line. Add an assertion in tests that registry keys equal the 14 expected public result keys.

- [ ] Run and commit:

```bash
python -m pytest tests/test_check_summary.py tests/test_advanced_health_summary.py -q
git add src/diagnostics/advanced_health.py tests/test_advanced_health_summary.py
git commit -m "fix: report truthful advanced check counts"
```

## Task 3: Render the new counter meaning in UI and report

**Files:**

- Modify: `src/panels.py`
- Modify: `src/report_formatter.py`
- Create: `tests/test_check_summary_consumers.py`

- [ ] Locate any existing `checks completed`, `Advanced health`, or raw result-count consumer:

```bash
rg -n "checks completed|Advanced health|_check_summary|successful|len\(.*Advanced" src/panels.py src/report_formatter.py src/AutoSpecUploaderGUI.py
```

- [ ] Write failing consumer tests for mixed counts. Require human-readable output that names unavailable/skipped separately rather than presenting them as failures or silently excluding them.

- [ ] Add a compact line to the advanced-health UI/report only where a summary location already exists. Use:

```text
Checks: 8/11 attempted passed · 1 failed · 2 unavailable · 3 skipped
```

- [ ] For legacy saved results without `_check_summary`, omit the line rather than reconstructing a misleading count.

- [ ] Run, implement, rerun, and commit:

```bash
python -m pytest tests/test_check_summary_consumers.py -q
git add src/panels.py src/report_formatter.py tests/test_check_summary_consumers.py
git commit -m "feat: show advanced check outcome breakdown"
```

If no UI/report summary location exists, keep the structured result and log line only, document that decision in the commit, and omit unchanged files.

## Task 4: Add pure diagnostic-log integrity inspection

**Files:**

- Create: `src/log_integrity.py`
- Create: `tests/test_log_integrity.py`

- [ ] Write failing tests for:

```python
LogIntegrityStatus = Literal["complete", "incomplete", "corrupt", "unreadable"]


@dataclass(frozen=True)
class LogIntegrityResult:
    status: LogIntegrityStatus
    reason: str


def inspect_log_bytes(data: bytes) -> LogIntegrityResult: ...


def inspect_log_file(path: Path) -> LogIntegrityResult: ...
```

- [ ] Generate synthetic data inside the tests for:

  - valid UTF-8 containing `Session Start`, `Session End`, and `Final Status` => complete;
  - valid UTF-8 with a session start but missing end/final markers => incomplete;
  - valid prefix followed by NUL/control-heavy binary tail => corrupt;
  - invalid UTF-8 => corrupt;
  - missing/unreadable file => unreadable;
  - empty file => incomplete.

- [ ] Require reasons to contain no log contents; only the failed rule may be named.

- [ ] Prove red, implement strict UTF-8 decode and control-character checks, rerun:

```bash
python -m pytest tests/test_log_integrity.py -q
```

Expected: all synthetic integrity states pass.

- [ ] Commit:

```bash
git add src/log_integrity.py tests/test_log_integrity.py
git commit -m "feat: detect incomplete and corrupt diagnostic logs"
```

## Task 5: Integrate non-destructive previous/current log checks

**Files:**

- Modify: `src/AutoSpecUploaderGUI.py:81-116`
- Modify: `src/AutoSpecUploaderGUI.py:1545-1635`
- Create: `tests/test_log_integrity_integration.py`

- [ ] Add a helper that selects the newest prior `AutoSpecUploader_*.log` excluding the current path. Test no prior log, one complete prior log, one incomplete prior log, and a newer corrupt log.

- [ ] At startup after logging is configured, inspect only the newest prior log. For non-complete status log exactly one warning shaped like:

```text
Previous diagnostic log integrity=incomplete file=<filename> reason=<rule>; this is an app/storage record, not a hardware diagnosis
```

Use the basename only, not the entire log contents.

- [ ] Before `sys.exit(exit_code)`, flush every root handler and inspect the current log. If it is not complete, emit a console warning and leave the file untouched. Do not recursively write another file-log warning after final inspection.

- [ ] Change `Final Status: SUCCESS (Diagnostics completed)` to reflect application exit, not diagnostic completion:

```text
Final Status: COMPLETE (Application exited normally)
```

Use `Final Status: ERROR (Application exit code N)` for a non-zero Qt exit code.

- [ ] Run integration tests using a temporary directory and monkeypatched handlers:

```bash
python -m pytest tests/test_log_integrity.py tests/test_log_integrity_integration.py -q
```

Expected: files are classified and warned about but never altered.

- [ ] Commit:

```bash
git add src/AutoSpecUploaderGUI.py tests/test_log_integrity_integration.py
git commit -m "fix: surface diagnostic log integrity safely"
```

## Task 6: Make local and CI release gates identical

**Files:**

- Modify: `scripts/build_windows_release.ps1`
- Modify: `.github/workflows/windows-release.yml`
- Create: `tests/test_release_metadata.py`

- [ ] Add tests asserting `src/settings.py` and `src/__init__.py` expose the same version and that a beta version is marked prerelease by the workflow tag expression.

- [ ] Update the local PowerShell build script to install `requirements-dev.txt`, run `python -m pytest -q`, and stop before PyInstaller when tests fail.

- [ ] Confirm the GitHub Actions workflow already installs development requirements and runs the same pytest command before build. If beta.50 left only `requirements.txt`, correct it here and add a workflow-structure assertion.

- [ ] Run:

```bash
python -m pytest tests/test_release_metadata.py -q
python -m pytest -q
```

Expected: local and CI release entry points both run the full suite before packaging.

- [ ] Commit:

```bash
git add scripts/build_windows_release.ps1 .github/workflows/windows-release.yml tests/test_release_metadata.py
git commit -m "ci: align local and hosted release gates"
```

## Task 7: Create and execute the beta.54 qualification matrix

**Files:**

- Create: `docs/testing/2.2.45-beta.54-release-qualification.md`
- Modify: `src/settings.py:13`
- Modify: `src/__init__.py:4`
- Modify: `src/CHANGELOG.md`

- [ ] Create a checkbox matrix with columns: area, fixture/physical machine, action, expected result, actual result, evidence reference, tester/date. Include at least:

  - credential message/args/chained traceback redaction;
  - RepairDesk success/cancel/failure terminal events;
  - 6th, 11th, 12th, 13th, 14th, Core Ultra, and unknown CPU identity;
  - honest WMI clock labels;
  - normal, 95-ramp/75-load, confirmed-throttle, and thermal-abort temperature cases;
  - NVMe, SATA SSD, HDD, USB, virtual, unknown, slow-SSD, and fast-HDD storage cases;
  - internal-only laptop, laptop plus external, unknown connection, desktop multi-monitor, and 16.3-inch panel cases;
  - all advanced-check outcome states;
  - complete, incomplete, corrupt, and unreadable log states;
  - historical `available_updates`, `driver_titles`, and `cameras` regression paths;
  - source run, portable build, installer build, and USB eject flow.

- [ ] Set both versions to `2.2.45-beta.54` and add the resilience/qualification changelog entry.

- [ ] Run the complete release gate:

```bash
python -m pytest -q
python -m compileall -q src
git diff --check
```

Expected: all tests pass and source compiles.

- [ ] Build locally with `scripts/build_windows_release.ps1` and through GitHub Actions. Confirm both gates stop before packaging on a deliberately failing test in a disposable branch/worktree, then revert that temporary test change before qualification.

- [ ] Execute every matrix row. Use synthetic data for secret/error tests and controlled hardware for sensor/bus/connection checks. Record evidence references without embedding production logs or customer information.

- [ ] For every changed field, compare GUI, report preview, critical issues, activity log, and locally generated RepairDesk note HTML. Any disagreement blocks the tag; no live RepairDesk request is needed.

- [ ] Re-run the original 62-log audit script/read-only analysis against the new normalization functions where applicable. Expected historical interpretation changes:

  - modern Intel names classify correctly;
  - audited NVMe hardware stays NVMe regardless of benchmark band;
  - 95°C ramp / 74–75°C sustained load is not reported as 95°C under load;
  - laptop built-in panels are not counted as external;
  - corrupted log is identified as corrupt, not parsed as hardware evidence.

- [ ] Commit completed release documentation and metadata:

```bash
git add docs/testing/2.2.45-beta.54-release-qualification.md src/settings.py src/__init__.py src/CHANGELOG.md
git commit -m "chore: qualify 2.2.45-beta.54"
```

- [ ] Tag only when the matrix has no unresolved required row:

```bash
git tag -a v2.2.45-beta.54 -m "PCAutoSpec 2.2.45-beta.54"
```

Expected: beta.54 is the qualified staged-beta endpoint, with honest counters and explicit log-integrity states.
