"""Outcome accounting for registered checks, independent of hardware collectors."""
from dataclasses import dataclass
from numbers import Real
from typing import Literal, Sequence

CheckOutcome = Literal['passed', 'failed', 'unavailable', 'skipped']

@dataclass(frozen=True)
class CheckRecord:
    key: str
    label: str
    outcome: CheckOutcome

    @property
    def attempted(self) -> bool:
        return self.outcome != 'skipped'

@dataclass(frozen=True)
class CheckCounts:
    registered: int
    attempted: int
    passed: int
    failed: int
    unavailable: int
    skipped: int

def normalize_check_outcome(value: object) -> CheckOutcome:
    if isinstance(value, Real) and not isinstance(value, bool):
        return 'passed'
    status = value.get('status') if isinstance(value, dict) else None
    if status == 'ok':
        return 'passed'
    if status in ('error', 'failed', 'critical'):
        return 'failed'
    if status in ('skipped', 'cancelled'):
        return 'skipped'
    return 'unavailable'

def summarize_check_records(records: Sequence[CheckRecord]) -> CheckCounts:
    if len({record.key for record in records}) != len(records):
        raise ValueError('Duplicate check keys')
    counts = dict.fromkeys(('passed', 'failed', 'unavailable', 'skipped'), 0)
    for record in records:
        if record.outcome not in counts:
            raise ValueError('Invalid check outcome')
        counts[record.outcome] += 1
    return CheckCounts(len(records), len(records) - counts['skipped'], **counts)
