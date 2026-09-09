"""Pure interpretation of hardware evidence; no hardware or network access."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re


@dataclass(frozen=True)
class CpuIdentity:
    family: str
    generation: str
    generation_number: int | None
    windows_compatibility: str


def classify_intel_cpu(cpu_name: str) -> CpuIdentity:
    name = re.sub(r'\((?:R|TM)\)|[®™]', '', (cpu_name or '').upper())
    family = 'Intel Core Ultra' if 'CORE ULTRA' in name else 'Intel Core'
    unknown = CpuIdentity(family, 'Unknown', None, 'Unknown — verify manually')
    if family == 'Intel Core Ultra':
        model = re.search(r'CORE ULTRA\s+[579]\s+([12])\d{2}[A-Z]+\b', name)
        return (CpuIdentity(family, f'Core Ultra Series {model[1]}', None,
                            'Windows 11 compatible') if model else unknown)
    explicit = re.search(r'\b(\d+)(?:ST|ND|RD|TH)\s+GEN\b', name)
    generation = int(explicit[1]) if explicit else None
    if generation is None:
        model = re.search(r'\bI[3579]\s*-?\s*(\d{4,5})([A-Z][A-Z0-9]*)?\b', name)
        if model:
            digits, suffix = model.groups()
            number = int(digits)
            if 10000 <= number <= 14999 or (1100 <= number <= 1499 and suffix):
                generation = int(digits[:2])
            elif len(digits) == 4 and 2000 <= number <= 9999:
                generation = int(digits[0])
    if generation is None or not 1 <= generation <= 14:
        return unknown
    ending = 'th' if 10 <= generation % 100 <= 20 else {1:'st',2:'nd',3:'rd'}.get(generation % 10, 'th')
    return CpuIdentity(family, f'{generation}{ending} Gen', generation,
                       'Windows 11 compatible' if generation >= 8 else 'Windows 10 only')


@dataclass(frozen=True)
class CpuClockSummary:
    base_ghz: float | None
    boost_ghz: float | None
    current_ghz: float | None
    wmi_max_ghz: float | None

    def display_parts(self) -> tuple[str, ...]:
        seen, parts = set(), []
        for label, value in [('Base', self.base_ghz), ('Boost', self.boost_ghz),
                             ('Current', self.current_ghz), ('WMI max', self.wmi_max_ghz)]:
            if value is not None and f'{value:.2f}' not in seen:
                seen.add(f'{value:.2f}')
                parts.append(f'{label}: {value:.2f} GHz')
        return tuple(parts)


def normalize_cpu_clocks(*, authoritative_base_mhz, authoritative_boost_mhz,
                         current_mhz, wmi_max_mhz) -> CpuClockSummary:
    def ghz(value):
        return value / 1000 if isinstance(value, (float, int)) and math.isfinite(value) and value > 0 else None
    return CpuClockSummary(*(ghz(value) for value in
        (authoritative_base_mhz, authoritative_boost_mhz, current_mhz, wmi_max_mhz)))
