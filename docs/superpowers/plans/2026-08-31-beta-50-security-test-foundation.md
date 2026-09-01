# PCAutoSpec beta.50 Security and Test Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `2.2.45-beta.50` with centralized credential-safe logging, one terminal audit event for every RepairDesk upload outcome, and a pytest gate that runs before the Windows build.

**Architecture:** Put secret scrubbing at the logging-handler boundary so every message and rendered traceback is sanitized, while retaining one shared text-redaction function for user-visible errors. Keep RepairDesk request metadata on the API client and emit the terminal upload event from `UploadWorker`, the single owner of upload lifecycle state. Establish a small, fixture-driven pytest suite and make it a release prerequisite.

**Tech Stack:** Python 3, standard `logging` and `traceback`, PySide6 workers, `requests`, pytest, GitHub Actions, PyInstaller, Inno Setup.

**Spec:** [Diagnostic Correctness Remediation Design](../specs/2026-08-31-pcautospec-diagnostic-correctness-design.md)

## Global Constraints

- Preserve runtime diagnostic output and hardware classification behavior in this beta.
- Never copy production logs, API keys, bearer tokens, OAuth codes, customer names, emails, phone numbers, or ticket notes into tests or commits.
- All credential examples must be obvious synthetic values such as `test-api-secret-123`.
- Keep RepairDesk tests fully mocked; no test may make a live HTTP request.
- Maintain the existing public `RepairDeskAPI.add_diagnostic_note()` response shape, including its `success` key.
- A terminal upload event may contain only outcome, ticket ID, request-attempt count, and HTTP status. It must not contain customer or device details.
- Stop after the beta.50 release candidate and complete its USB hardware checkpoint before beginning beta.51.

---

## Task 1: Add the pytest foundation and sanitized fixtures

**Files:**

- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/sanitized_repairdesk_errors.json`
- Create: `tests/test_fixture_hygiene.py`

- [ ] Add a development requirements file with the runtime dependencies and a bounded pytest version:

```text
-r requirements.txt
pytest>=8.0,<9
```

- [ ] Configure pytest imports and discovery:

```ini
[pytest]
pythonpath = src
testpaths = tests
addopts = -ra
```

- [ ] Add `tests/conftest.py` with a `fixture_dir` fixture that returns `tests/fixtures` as a `pathlib.Path`.

- [ ] Add a JSON fixture containing only synthetic examples for an API-key URL, bearer header, OAuth token payload, and nested exception text. Use `example.invalid` for every host.

- [ ] Write `tests/test_fixture_hygiene.py` first. It must recursively read every file below `tests/fixtures` and fail if a recognized credential value is neither `[REDACTED]` nor prefixed `test-`. It must also reject live RepairDesk/API hostnames and obvious bearer/JWT formats. Recognized fields are `api_key`, `authorization`, `client_secret`, `access_token`, `refresh_token`, and `code`.

- [ ] Run the focused test and confirm it passes because all values are synthetic and explicitly allow-listed:

```bash
python -m pytest tests/test_fixture_hygiene.py -q
```

Expected: `1 passed`.

- [ ] Commit the test foundation:

```bash
git add requirements-dev.txt pytest.ini tests/conftest.py tests/fixtures/sanitized_repairdesk_errors.json tests/test_fixture_hygiene.py
git commit -m "test: establish sanitized pytest foundation"
```

## Task 2: Centralize sensitive-text redaction

**Files:**

- Create: `src/log_safety.py`
- Create: `tests/test_log_safety.py`
- Modify: `src/repairdesk_api.py:18-30`
- Modify: `src/oauth_repairdesk.py:21-32`

- [ ] Write failing parameterized tests for this public function:

```python
def redact_sensitive_text(value: object) -> str:
    """Return text with recognized credentials replaced by '[REDACTED]'."""
```

Cover all of the following locations and casing variants:

```python
CASES = [
    ("?api_key=test-api-secret-123", "?api_key=[REDACTED]"),
    ("&API_KEY=test-api-secret-123", "&API_KEY=[REDACTED]"),
    ("Authorization: Bearer test-bearer-secret", "Authorization: Bearer [REDACTED]"),
    ('{"client_secret":"test-client-secret"}', '{"client_secret":"[REDACTED]"}'),
    ('{"access_token":"test-access-token"}', '{"access_token":"[REDACTED]"}'),
    ('{"refresh_token":"test-refresh-token"}', '{"refresh_token":"[REDACTED]"}'),
    ("?code=test-oauth-code", "?code=[REDACTED]"),
]
```

Also assert that an ordinary URL, ticket ID, HTTP status, and attempt count are unchanged.

- [ ] Run the test to prove it fails because `src/log_safety.py` does not exist:

```bash
python -m pytest tests/test_log_safety.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'log_safety'`.

- [ ] Implement `redact_sensitive_text()` using compiled, case-insensitive expressions. Preserve the key/header spelling while replacing only the value. Support URL/query syntax, header syntax, Python dict rendering, and JSON rendering.

- [ ] Replace the private redactor implementations in `repairdesk_api.py` and `oauth_repairdesk.py` with imports from `log_safety`. Keep a temporary private alias only if an existing internal call still requires the old name:

```python
from log_safety import redact_sensitive_text

_redact_sensitive_text = redact_sensitive_text
```

- [ ] Run the focused redaction tests:

```bash
python -m pytest tests/test_log_safety.py -q
```

Expected: all cases pass and non-sensitive context is preserved.

- [ ] Commit the shared redactor:

```bash
git add src/log_safety.py src/repairdesk_api.py src/oauth_repairdesk.py tests/test_log_safety.py
git commit -m "fix: centralize credential redaction"
```

## Task 3: Sanitize every log record and nested traceback

**Files:**

- Modify: `src/log_safety.py`
- Modify: `src/AutoSpecUploaderGUI.py:81-116`
- Modify: `tests/test_log_safety.py`

- [ ] Add failing tests for the logging boundary. Exercise each of these record shapes through a real `logging.StreamHandler` backed by `io.StringIO`:

  - secret embedded in `record.msg`;
  - secret supplied through tuple `record.args`;
  - secret supplied through mapping `record.args`;
  - a chained `requests` exception whose inner message contains a URL query API key;
  - an already-rendered `record.exc_text` containing an OAuth token.

- [ ] Define and test these interfaces:

```python
class CredentialRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize message arguments and traceback text before formatting."""


def install_credential_filter(*handlers: logging.Handler) -> None:
    """Attach one credential-redaction filter to each supplied handler."""
```

- [ ] Run the tests and confirm at least the traceback cases fail before implementation:

```bash
python -m pytest tests/test_log_safety.py -q
```

Expected: failures show the synthetic secret in captured output.

- [ ] Implement the filter. For `exc_info`, render the complete exception chain with `traceback.format_exception`, redact it, store the result in `record.exc_text`, and clear `record.exc_info` so the formatter cannot append the unsanitized traceback again. Sanitize `msg` and recursively sanitize string values in tuple or mapping arguments without changing numeric types.

- [ ] In `setup_logging()`, call `install_credential_filter(file_handler, console_handler)` after constructing the handlers and before adding them to the root logger.

- [ ] Run the focused tests and the current application import smoke check:

```bash
python -m pytest tests/test_log_safety.py -q
python -c "import sys; sys.path.insert(0, 'src'); import AutoSpecUploaderGUI"
```

Expected: tests pass; the GUI module imports without an exception.

- [ ] Commit handler-boundary redaction:

```bash
git add src/log_safety.py src/AutoSpecUploaderGUI.py tests/test_log_safety.py
git commit -m "fix: redact credentials from log tracebacks"
```

## Task 4: Track RepairDesk request attempts and final HTTP status

**Files:**

- Modify: `src/repairdesk_api.py:33-106`
- Create: `tests/test_repairdesk_api.py`

- [ ] Add this immutable metadata type near the top of `repairdesk_api.py`:

```python
@dataclass(frozen=True)
class RequestOutcome:
    attempts: int = 0
    status_code: int | None = None
```

- [ ] Specify the client interface in tests:

```python
api.last_request_outcome  # RequestOutcome
```

The value must reset at the beginning of every `_request()` call, become `(1, 200)` after first-attempt success, become `(3, 200)` after two 429 responses and a success, and remain `(3, 429)` after final 429 failure.

- [ ] Mock `requests.Session.request` and `time.sleep`. Assert the exact attempt count and status without making a network request.

- [ ] Run the test and confirm it fails because the metadata does not exist:

```bash
python -m pytest tests/test_repairdesk_api.py -q
```

Expected: failures reference missing `last_request_outcome`.

- [ ] Initialize `self.last_request_outcome = RequestOutcome()` in `RepairDeskAPI.__init__`. In `_request()`, set it immediately after each response and before `raise_for_status()`. If the request raises before a response exists, record the current attempt and `None` status.

- [ ] Preserve the current maximum of three attempts and existing 429 retry delay. Do not add retries for other status codes in this beta.

- [ ] Run the API tests:

```bash
python -m pytest tests/test_repairdesk_api.py -q
```

Expected: all request metadata and retry tests pass with zero real HTTP calls.

- [ ] Commit request observability:

```bash
git add src/repairdesk_api.py tests/test_repairdesk_api.py
git commit -m "feat: record repairdesk request outcomes"
```

## Task 5: Emit exactly one terminal upload event

**Files:**

- Modify: `src/workers.py:88-140`
- Create: `tests/test_upload_worker.py`

- [ ] Add a private helper with an exact, stable log schema:

```python
def _log_upload_terminal_event(
    *,
    outcome: str,
    ticket_id: str,
    attempts: int,
    http_status: int | None,
) -> None:
    logging.info(
        "RepairDesk upload terminal outcome=%s ticket=%s attempts=%d http_status=%s",
        outcome,
        ticket_id,
        attempts,
        http_status if http_status is not None else "none",
    )
```

Allowed outcomes are `success`, `cancelled`, and `failed`.

- [ ] Write worker tests using a fake RepairDesk client and call `UploadWorker.run()` synchronously. Connect the worker's Qt signals to small recorder callables; do not attempt to replace the read-only bound signal attributes. Cover:

  - successful note upload;
  - technician cancellation after customer confirmation is requested;
  - API response with `success=False`;
  - raised exception whose text contains a synthetic API key.

Assert each path logs exactly one line containing `RepairDesk upload terminal`, includes ticket/attempt/status, and excludes customer/device test data and every synthetic secret.

- [ ] Run the tests and confirm they fail before the lifecycle event is implemented:

```bash
python -m pytest tests/test_upload_worker.py -q
```

Expected: no terminal event is found.

- [ ] Refactor `UploadWorker.run()` so all four exits flow through one terminal-event emission. Read attempts/status from `self.api.last_request_outcome`; use `RequestOutcome()` when a fake or older client lacks the property during compatibility tests.

- [ ] Sanitize exception text before both `logging.error()` and `finished.emit()`. Passing `exc_info=True` is allowed because the handler filter now sanitizes the complete chain.

- [ ] Keep progress-signal behavior intact. Terminal logs must not include the customer name, email, phone, device name, or note body even though the UI can still display approved customer/device details.

- [ ] Run the worker and logging tests together:

```bash
python -m pytest tests/test_upload_worker.py tests/test_log_safety.py tests/test_repairdesk_api.py -q
```

Expected: all tests pass and every path emits one safe terminal event.

- [ ] Commit upload lifecycle logging:

```bash
git add src/workers.py tests/test_upload_worker.py
git commit -m "feat: log terminal repairdesk upload outcomes"
```

## Task 6: Gate the Windows release build on tests

**Files:**

- Modify: `.github/workflows/windows-release.yml:27-36`
- Modify: `scripts/build_windows_release.ps1`

- [ ] Change the dependency install step to use the development requirements:

```yaml
- name: Install dependencies
  run: pip install -r requirements-dev.txt pyinstaller
```

- [ ] Add this step immediately before the first build or Inno Setup step:

```yaml
- name: Run test suite
  run: python -m pytest -q
```

- [ ] Apply the same gate to `scripts/build_windows_release.ps1`: install `requirements-dev.txt`, run `& $Python -m pytest -q`, check `$LASTEXITCODE`, and throw before PyInstaller if it is non-zero.

- [ ] Validate the workflow indentation and run the complete local suite:

```bash
python -m pytest -q
git diff --check
```

Expected: the complete suite passes; `git diff --check` prints nothing.

- [ ] Commit the release gate:

```bash
git add .github/workflows/windows-release.yml scripts/build_windows_release.ps1
git commit -m "ci: run pytest before windows build"
```

## Task 7: Version, changelog, and beta.50 qualification

**Files:**

- Modify: `src/settings.py:13`
- Modify: `src/__init__.py:4`
- Modify: `src/CHANGELOG.md`
- Modify: `README.md` only if it contains the prior current-version string

- [ ] Set both version constants to `2.2.45-beta.50` and add a changelog section summarizing credential-safe logs, upload terminal events, and the automated pre-build test gate.

- [ ] Search for stale current-version references:

```bash
rg -n "2\.2\.45-beta\.49|APP_VERSION|__version__" README.md src .github
```

Expected: beta.49 remains only in historical changelog entries or explicit regression fixtures.

- [ ] Run the full automated release gate:

```bash
python -m pytest -q
python -m compileall -q src
git diff --check
```

Expected: all tests pass, compileall exits 0, and diff-check is clean.

- [ ] Build the Windows release through the existing GitHub Actions workflow. Confirm pytest finishes before PyInstaller and Inno Setup begin.

- [ ] On a test USB using synthetic/non-production credentials, complete these manual checks:

  - launch and complete one diagnostic session;
  - perform one mocked upload success; use a designated live test ticket only after Jeff gives separate approval for that request;
  - cancel one upload at the technician confirmation prompt;
  - force one controlled HTTP failure against a non-production endpoint;
  - inspect the complete log for exactly one terminal event per attempt;
  - search the log for every synthetic secret and confirm none appears;
  - compare the hardware/report output against beta.49 and confirm no diagnostic labels changed.

- [ ] Open the packaged report preview, close the app cleanly, and confirm the generated log is readable with no impossible counter or unexpected exception.

- [ ] Record the build run URL, USB identifier, Windows version, tester, and pass/fail evidence in the release notes. Do not record credentials or customer data.

- [ ] Commit the release metadata:

```bash
git add src/settings.py src/__init__.py src/CHANGELOG.md README.md
git commit -m "chore: release 2.2.45-beta.50"
```

- [ ] Tag only after every automated and USB check passes:

```bash
git tag -a v2.2.45-beta.50 -m "PCAutoSpec 2.2.45-beta.50"
```

Expected: beta.50 is a security/test-foundation release with unchanged diagnostic interpretation.
