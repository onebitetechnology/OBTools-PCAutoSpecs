from pathlib import Path
import re

ROOT=Path(__file__).parents[1]

def test_versions_and_prerelease():
    settings=re.search(r"APP_VERSION = ['\"]([^'\"]+)", (ROOT/'src/settings.py').read_text(encoding='utf-8')).group(1)
    package=re.search(r'__version__ = "([^"]+)', (ROOT/'src/__init__.py').read_text(encoding='utf-8')).group(1)
    installer=re.search(r'AppVersion=(.+)', (ROOT/'installer/PCAutoSpec.iss').read_text(encoding='utf-8')).group(1)
    assert settings==package==installer=='2.2.45-beta.54'
    workflow=(ROOT/'.github/workflows/windows-release.yml').read_text(encoding='utf-8')
    assert "prerelease: ${{ contains(github.ref_name, '-beta') || contains(github.ref_name, '-rc') }}" in workflow

def test_release_gates_precede_packaging():
    local=(ROOT/'scripts/build_windows_release.ps1').read_text(encoding='utf-8')
    hosted=(ROOT/'.github/workflows/windows-release.yml').read_text(encoding='utf-8')
    for source in (local,hosted):
        assert source.index('requirements-dev.txt') < source.index('-m pytest -q') < source.index('PyInstaller bundle')
    lines=local.splitlines()
    native=[i for i,line in enumerate(lines) if line.strip().startswith(('& $','py -3.12'))]
    assert len(native)==6
    for i in native:
        assert '$LASTEXITCODE -ne 0' in lines[i+1]
        assert 'throw ' in '\n'.join(lines[i+1:i+3])
