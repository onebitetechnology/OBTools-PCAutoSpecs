# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


# PyInstaller executes spec files via ``exec()``, so ``__file__`` is not set.
# ``SPECPATH`` is provided by PyInstaller and points at the directory
# containing this spec file.
project_root = Path(globals().get("SPECPATH", Path.cwd())).resolve()
block_cipher = None

datas = [
    (str(project_root / "src"), "src"),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "README.md"), "."),
    (str(project_root / "PC AutoSpec Read Me.md"), "."),
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "PCAutoSpec.bat"), "."),
]

hiddenimports = [
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
    "wmi",
]

a = Analysis(
    ["run.py"],
    pathex=[str(project_root), str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PCAutoSpec",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PCAutoSpec",
)
