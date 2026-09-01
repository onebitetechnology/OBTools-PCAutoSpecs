$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "Preparing virtual environment..."
if (-not (Test-Path ".venv")) {
    py -3.12 -m venv .venv
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Pip = Join-Path $ProjectRoot ".venv\Scripts\pip.exe"

& $Python -m pip install --upgrade pip
& $Pip install -r requirements-dev.txt pyinstaller

Write-Host "Running test suite..."
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Test suite failed; release build stopped before packaging."
}

Write-Host "Building PyInstaller bundle..."
& $Python -m PyInstaller --clean PCAutoSpec.spec

$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    throw "Inno Setup 6 not found. Install it, then rerun this script."
}

Write-Host "Building installer..."
& $Iscc "installer\PCAutoSpec.iss"

Write-Host "Release artifacts are in dist\ and release\."
