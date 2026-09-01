import re
from pathlib import Path


LIVE_HOST_PATTERN = re.compile(r"(?:api\.)?repairdesk\.co", re.IGNORECASE)
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
CREDENTIAL_PATTERN = re.compile(
    r"(?:[?&]|[\"']?)"
    r"(api_key|client_secret|access_token|refresh_token|code)"
    r"[\"']?\s*[:=]\s*[\"']?([^\"'&,}\s]+)",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"\bBearer\s+([^\s\"',}]+)", re.IGNORECASE)


def _is_safe_fixture_secret(value: str) -> bool:
    normalized = value.strip()
    return normalized == "[REDACTED]" or normalized.lower().startswith("test-")


def test_fixture_files_contain_only_synthetic_or_redacted_credentials(
    fixture_dir: Path,
) -> None:
    violations = []

    for path in sorted(item for item in fixture_dir.rglob("*") if item.is_file()):
        text = path.read_text(encoding="utf-8", errors="strict")
        if LIVE_HOST_PATTERN.search(text):
            violations.append(f"{path.name}: live RepairDesk hostname")
        if JWT_PATTERN.search(text):
            violations.append(f"{path.name}: JWT-shaped value")

        for match in CREDENTIAL_PATTERN.finditer(text):
            if not _is_safe_fixture_secret(match.group(2)):
                violations.append(f"{path.name}: unsafe {match.group(1)} value")

        for match in BEARER_PATTERN.finditer(text):
            if not _is_safe_fixture_secret(match.group(1)):
                violations.append(f"{path.name}: unsafe bearer value")

    assert violations == []
