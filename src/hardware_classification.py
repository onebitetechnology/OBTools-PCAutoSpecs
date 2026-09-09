"""Pure interpretation of hardware evidence; no hardware or network access."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Literal, Mapping

DriveType = Literal['NVMe SSD', 'SATA SSD', 'SSD', 'HDD', 'Virtual Disk', 'USB', 'Unknown']
EvidenceSource = Literal['smart', 'windows', 'model', 'unknown']


@dataclass(frozen=True)
class DriveIdentity:
    physical_type: DriveType
    evidence_source: EvidenceSource


def classify_drive_identity(drive: Mapping[str, object]) -> DriveIdentity:
    """Interpret recorded evidence without using health or benchmark speed."""
    def upper(key):
        value = str(drive.get(key) or '').strip().upper()
        return '' if value in ('UNKNOWN', 'UNSPECIFIED', 'N/A', 'NONE') else value

    model = upper('model')
    if any(marker in model for marker in ('MICROSOFT VIRTUAL', 'VMWARE', 'VBOX', 'VIRTUALBOX', 'VIRTUAL DISK')):
        return DriveIdentity('Virtual Disk', 'model')
    provenance = any(key in drive for key in ('smart_bus_type', 'smart_media_type', 'windows_bus_type', 'windows_media_type'))
    smart_bus, smart_media = upper('smart_bus_type'), upper('smart_media_type')
    windows_bus = upper('windows_bus_type') if provenance else upper('bus_type')
    windows_media = upper('windows_media_type') if provenance else upper('media_type')
    for bus, source in ((smart_bus, 'smart'), (windows_bus, 'windows')):
        if bus == 'USB':
            return DriveIdentity('USB', source)
    bus = smart_bus or windows_bus
    if bus == 'NVME':
        return DriveIdentity('NVMe SSD', 'smart' if smart_bus else 'windows')
    def media_identity(media, source):
        if 'SSD' in media or 'SOLID STATE' in media:
            return DriveIdentity('SATA SSD' if bus == 'SATA' else 'SSD', source)
        if 'HDD' in media or media == 'HARD DISK' or media.startswith('HARD DISK ('):
            return DriveIdentity('HDD', source)
        return None
    if result := media_identity(smart_media, 'smart'):
        return result
    if result := media_identity(windows_media, 'windows'):
        return result
    if drive.get('smart_available_spare') is not None:
        return DriveIdentity('NVMe SSD', 'smart')
    # Generic Fixed hard disk media is returned for SSDs too.
    if any(marker in model for marker in ('NVME', 'NVM EXPRESS')):
        return DriveIdentity('NVMe SSD', 'model')
    if 'SSD' in model:
        return DriveIdentity('SATA SSD' if bus == 'SATA' else 'SSD', 'model')
    return DriveIdentity('Unknown', 'unknown')


@dataclass(frozen=True)
class DrivePerformance:
    band: Literal['Very fast', 'Fast', 'Standard', 'Slow', 'Unavailable']
    severity: Literal['success', 'info', 'warning', 'unavailable']


def assess_drive_performance(*, read_mb_s: float | None, write_mb_s: float | None) -> DrivePerformance:
    try:
        read = float(read_mb_s)
    except (TypeError, ValueError):
        return DrivePerformance('Unavailable', 'unavailable')
    if not math.isfinite(read) or read <= 0:
        return DrivePerformance('Unavailable', 'unavailable')
    if read > 2000:
        return DrivePerformance('Very fast', 'success')
    if read > 400:
        return DrivePerformance('Fast', 'success')
    if read >= 100:
        return DrivePerformance('Standard', 'info')
    return DrivePerformance('Slow', 'warning')


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
