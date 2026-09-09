"""Non-destructive log inspection. Reasons never contain log payloads."""
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import logging
import sys

@dataclass(frozen=True)
class LogIntegrityResult:
    status: Literal['complete', 'incomplete', 'corrupt', 'unreadable']
    reason: str

def inspect_log_bytes(data: bytes) -> LogIntegrityResult:
    try:
        text = data.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        return LogIntegrityResult('corrupt', 'invalid UTF-8')
    if any((ord(c) < 32 and c not in '\t\r\n') or ord(c) == 127 for c in text):
        return LogIntegrityResult('corrupt', 'unexpected control characters')
    start = text.rfind('Session Start')
    end = text.rfind('Session End')
    final = text.rfind('Final Status')
    if start < 0 or end < start or final < end:
        return LogIntegrityResult('incomplete', 'missing or unordered session markers; session may still be active')
    return LogIntegrityResult('complete', 'session markers present')

def inspect_log_file(path: Path) -> LogIntegrityResult:
    try:
        return inspect_log_bytes(Path(path).read_bytes())
    except OSError:
        return LogIntegrityResult('unreadable', 'file could not be read')

def newest_prior_log(current: Path):
    try:
        paths = [p for p in current.parent.glob('AutoSpecUploader_*.log') if p.resolve() != current.resolve()]
        return max(paths, key=lambda p: p.stat().st_mtime_ns, default=None)
    except OSError:
        return None

def warn_previous_log(current: Path):
    prior = newest_prior_log(current)
    if prior is None:
        return
    result = inspect_log_file(prior)
    if result.status != 'complete':
        logging.warning('Previous diagnostic log integrity=%s file=%s reason=%s; this is an app/storage record, not a hardware diagnosis', result.status, prior.name, result.reason)

def finalize_log(log_file: Path, exit_code: int):
    from datetime import datetime
    logging.info('Session End: %s', datetime.now().isoformat(timespec='seconds'))
    logging.info('Final Status: %s', 'COMPLETE (Application exited normally)' if exit_code == 0 else f'ERROR (Application exit code {exit_code})')
    flush_failed = False
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            flush_failed = True
    result = inspect_log_file(log_file)
    if flush_failed or result.status != 'complete':
        print(f'Current diagnostic log integrity={result.status} file={log_file.name} reason={"handler flush failed" if flush_failed else result.reason}; app/storage record, not a hardware diagnosis', file=sys.stderr)
    return result
