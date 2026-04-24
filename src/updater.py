"""
PC AutoSpec — GitHub release updater.

Checks the latest GitHub Release, downloads the appropriate Windows update
package, and launches/applies it after the app exits.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

import requests

from settings import APP_NAME, APP_VERSION, load_settings, get_app_dir

REPO_OWNER = "onebitetechnology"
REPO_NAME = "OBTools-PCAutoSpecs"
GITHUB_RELEASES_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
)
INSTALLER_ASSET_NAME = "PCAutoSpec-Setup.exe"
PORTABLE_ASSET_NAME = "PCAutoSpec-portable.zip"


def is_update_supported() -> bool:
    """Return True when update download/apply is supported."""
    return sys.platform == "win32"


def _is_running_from_removable_location() -> bool:
    """Best-effort check for a USB/removable install location."""
    if sys.platform != "win32":
        return False

    app_dir = get_app_dir()
    drive, _ = os.path.splitdrive(app_dir)
    if not drive:
        return False

    try:
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$logical = Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='{drive}'\" "
                    "-ErrorAction SilentlyContinue; "
                    "if (-not $logical) { return }; "
                    "$partition = @(Get-CimAssociatedInstance -InputObject $logical "
                    "-ResultClassName Win32_DiskPartition -ErrorAction SilentlyContinue)[0]; "
                    "$disk = $null; "
                    "if ($partition) { "
                    "$disk = @(Get-CimAssociatedInstance -InputObject $partition "
                    "-ResultClassName Win32_DiskDrive -ErrorAction SilentlyContinue)[0] "
                    "}; "
                    "[PSCustomObject]@{ "
                    "DriveType = [string]$logical.DriveType; "
                    "InterfaceType = if ($disk) { $disk.InterfaceType } else { '' }; "
                    "Model = if ($disk) { $disk.Model } else { '' }; "
                    "PNPDeviceID = if ($disk) { $disk.PNPDeviceID } else { '' } "
                    "} | ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=creationflags,
        )
        payload = json.loads(result.stdout.strip()) if result.stdout.strip() else {}
        drive_type = str(payload.get("DriveType", "")).strip()
        interface_type = str(payload.get("InterfaceType", "")).upper()
        model = str(payload.get("Model", "")).upper()
        pnp_id = str(payload.get("PNPDeviceID", "")).upper()
        return (
            drive_type == "2"
            or "USB" in interface_type
            or pnp_id.startswith("USB")
            or "USB" in model
        )
    except Exception as e:
        logging.debug(f"Could not detect removable install location: {e}")
        return False


def _normalize_version(version: str) -> str:
    return str(version or "").strip().lstrip("vV")


def _version_key(version: str) -> tuple:
    cleaned = _normalize_version(version)
    main, _, suffix = cleaned.partition("-")
    parts = re.findall(r"\d+", main)
    main_key = tuple(int(part) for part in parts) if parts else (0,)
    if not suffix:
        return main_key + ((1,),)

    suffix_lower = suffix.lower()
    if suffix_lower.startswith(("alpha", "a")):
        stage = 0
    elif suffix_lower.startswith(("beta", "b")):
        stage = 1
    elif suffix_lower.startswith("rc"):
        stage = 2
    else:
        stage = 0
    suffix_numbers = tuple(int(part) for part in re.findall(r"\d+", suffix_lower)) or (0,)
    return main_key + ((0, stage, suffix_numbers),)


def _is_prerelease(release_data: Dict) -> bool:
    if bool(release_data.get("prerelease")):
        return True
    tag = _normalize_version(release_data.get("tag_name") or release_data.get("name") or "")
    return "-" in tag


def _select_release(releases: list[Dict], include_prereleases: bool) -> Optional[Dict]:
    eligible = []
    for release in releases:
        if release.get("draft"):
            continue
        if not include_prereleases and _is_prerelease(release):
            continue
        version = _normalize_version(release.get("tag_name") or release.get("name") or "")
        if not version:
            continue
        eligible.append(release)

    if not eligible:
        return None

    return max(
        eligible,
        key=lambda release: _version_key(
            release.get("tag_name") or release.get("name") or ""
        ),
    )


def _download_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    root = base / APP_NAME / "updates"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _pending_metadata_path() -> Path:
    return _download_root() / "pending-update.json"


def _save_pending_metadata(metadata: Dict) -> None:
    _pending_metadata_path().write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )


def _clear_pending_metadata() -> None:
    path = _pending_metadata_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            logging.debug("Could not clear pending update metadata")


def get_pending_update() -> Optional[Dict]:
    """Return pending downloaded update metadata if it still exists on disk."""
    path = _pending_metadata_path()
    if not path.exists():
        return None
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logging.debug(f"Failed to read pending update metadata: {e}")
        return None

    package_path = Path(metadata.get("package_path") or metadata.get("installer_path", ""))
    pending_version = _normalize_version(metadata.get("version"))
    if (not package_path.is_file()
            or not pending_version
            or _version_key(pending_version) <= _version_key(APP_VERSION)):
        _clear_pending_metadata()
        return None

    metadata["package_path"] = str(package_path)
    metadata["installer_path"] = str(package_path)
    metadata.setdefault("package_kind", "installer")
    return metadata


def _select_installer_asset(release_data: Dict) -> Optional[Dict]:
    for asset in release_data.get("assets", []):
        if asset.get("name") == INSTALLER_ASSET_NAME:
            return asset
    for asset in release_data.get("assets", []):
        name = str(asset.get("name", ""))
        if name.lower().endswith(".exe") and "setup" in name.lower():
            return asset
    return None


def _select_portable_asset(release_data: Dict) -> Optional[Dict]:
    for asset in release_data.get("assets", []):
        if asset.get("name") == PORTABLE_ASSET_NAME:
            return asset
    for asset in release_data.get("assets", []):
        name = str(asset.get("name", ""))
        if name.lower().endswith(".zip") and "portable" in name.lower():
            return asset
    return None


def check_for_updates(include_prereleases: Optional[bool] = None, timeout: int = 20) -> Dict:
    """Query the latest GitHub Release and compare it to the running app version."""
    if include_prereleases is None:
        include_prereleases = bool(load_settings().get("include_beta_updates", False))

    state = {
        "supported": is_update_supported(),
        "current_version": APP_VERSION,
        "include_prereleases": include_prereleases,
        "latest_version": None,
        "available": False,
        "downloaded": False,
        "message": "Update checks are idle.",
        "release_notes": "",
        "installer_path": None,
        "package_path": None,
        "package_kind": "installer",
        "download_url": None,
        "html_url": None,
        "published_at": None,
        "prerelease": False,
    }

    if not state["supported"]:
        state["message"] = "Automatic update download is currently supported on Windows only."
        return state

    pending = get_pending_update()
    if pending:
        pending_version = pending.get("version")
        if _version_key(pending_version) > _version_key(APP_VERSION):
            state.update({
                "latest_version": pending_version,
                "available": True,
                "downloaded": True,
                "installer_path": pending.get("package_path") or pending.get("installer_path"),
                "package_path": pending.get("package_path") or pending.get("installer_path"),
                "package_kind": pending.get("package_kind", "installer"),
                "message": (
                    f"Update {pending_version} has already been downloaded. "
                    "Use Install Update Now to apply it."
                ),
            })

    try:
        response = requests.get(
            GITHUB_RELEASES_URL,
            headers={"Accept": "application/vnd.github+json"},
            params={"per_page": 20},
            timeout=timeout,
        )
        response.raise_for_status()
        releases = response.json()
    except Exception as e:
        if state["downloaded"]:
            return state
        state["message"] = f"Update check failed: {e}"
        return state

    release_data = _select_release(releases, include_prereleases)
    if not release_data:
        state["message"] = (
            "No matching releases were found for the selected update channel."
        )
        return state

    latest_version = _normalize_version(
        release_data.get("tag_name") or release_data.get("name") or ""
    )
    release_notes = str(release_data.get("body") or "").strip()
    use_portable_package = _is_running_from_removable_location()
    package_kind = "portable" if use_portable_package else "installer"
    package_asset = (
        _select_portable_asset(release_data)
        if use_portable_package
        else _select_installer_asset(release_data)
    )

    state.update({
        "latest_version": latest_version or None,
        "release_notes": release_notes,
        "html_url": release_data.get("html_url"),
        "published_at": release_data.get("published_at"),
        "prerelease": _is_prerelease(release_data),
    })

    if not latest_version:
        state["message"] = "Latest release did not include a valid version."
        return state

    current_key = _version_key(APP_VERSION)
    latest_key = _version_key(latest_version)

    if latest_key < current_key:
        state["available"] = False
        state["message"] = (
            f"Installed version {APP_VERSION} is newer than the latest "
            f"published release ({latest_version})."
        )
        return state

    if latest_key == current_key:
        state["available"] = False
        state["message"] = f"App is up to date. Current version: {APP_VERSION}"
        return state

    state["available"] = True
    if not package_asset:
        package_label = "portable ZIP" if use_portable_package else "Windows installer"
        state["message"] = (
            f"Update {latest_version} is available, but no {package_label} "
            "asset was found on the latest GitHub Release."
        )
        return state

    state["download_url"] = package_asset.get("browser_download_url")
    state["package_kind"] = package_kind
    if state["downloaded"] and (state["package_path"] or state["installer_path"]):
        state["message"] = (
            f"Update {latest_version} has already been downloaded. "
            "Use Install Update Now to apply it."
        )
        return state

    release_label = "Beta update" if state["prerelease"] else "Update"
    state["message"] = f"{release_label} {latest_version} is available."
    return state


def download_update(
    update_info: Dict,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    timeout: int = 60,
) -> Dict:
    """Download the latest update package to a local update cache."""
    if not is_update_supported():
        raise RuntimeError("Automatic update download is supported on Windows only.")

    download_url = update_info.get("download_url")
    latest_version = _normalize_version(update_info.get("latest_version"))
    package_kind = str(update_info.get("package_kind") or "installer")
    if not download_url or not latest_version:
        raise RuntimeError("No downloadable update is available.")

    download_dir = _download_root()
    package_name = (
        f"PCAutoSpec-portable-{latest_version}.zip"
        if package_kind == "portable"
        else f"PCAutoSpec-Setup-{latest_version}.exe"
    )
    package_path = download_dir / package_name
    temp_path = package_path.with_suffix(".download")

    if progress_callback:
        progress_callback(0, f"Starting download for version {latest_version}...")

    with requests.get(download_url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        total_bytes = int(response.headers.get("Content-Length", "0") or 0)
        downloaded = 0

        with open(temp_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    if total_bytes > 0:
                        percent = min(100, int((downloaded / total_bytes) * 100))
                        message = f"Downloading update... {percent}%"
                    else:
                        percent = 0
                        message = f"Downloading update... {downloaded // 1024} KB"
                    progress_callback(percent, message)

    temp_path.replace(package_path)
    metadata = {
        "version": latest_version,
        "package_path": str(package_path),
        "installer_path": str(package_path),
        "package_kind": package_kind,
        "downloaded_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    _save_pending_metadata(metadata)

    package_label = "Portable update" if package_kind == "portable" else "Update"
    return {
        "version": latest_version,
        "package_path": str(package_path),
        "installer_path": str(package_path),
        "package_kind": package_kind,
        "message": f"{package_label} {latest_version} downloaded and ready to install.",
    }


def launch_pending_update(
    package_path: str,
    install_dir: Optional[str] = None,
    app_pid: Optional[int] = None,
    relaunch_executable: Optional[str] = None,
) -> None:
    """
    Launch the update package after the app exits.

    Uses a temporary cmd script so the installer starts after the current
    process has had time to shut down and release any file locks.
    """
    if sys.platform != "win32":
        raise RuntimeError("Install update is only supported on Windows.")

    package = Path(package_path)
    if not package.is_file():
        raise RuntimeError("Downloaded update package file was not found.")

    if install_dir is None:
        install_dir = get_app_dir()

    update_root = _download_root()
    launcher_script = update_root / "launch-update.cmd"
    install_dir = str(Path(install_dir).resolve()) if install_dir else ""
    if app_pid is None:
        app_pid = os.getpid()
    if relaunch_executable is None and getattr(sys, 'frozen', False):
        relaunch_executable = sys.executable
    relaunch_executable = str(Path(relaunch_executable).resolve()) if relaunch_executable else ""

    if package.suffix.lower() == ".zip":
        if not install_dir:
            raise RuntimeError("Portable updates require a target folder.")

        staging_dir = update_root / "portable-staging"
        pending_metadata = _pending_metadata_path()
        log_path = update_root / "portable-apply.log"
        zip_path = str(package).replace("'", "''")
        dest_path = install_dir.replace("'", "''")
        stage_path = str(staging_dir).replace("'", "''")
        pending_path = str(pending_metadata).replace("'", "''")
        apply_log_path = str(log_path).replace("'", "''")
        relaunch_path = relaunch_executable.replace("'", "''") if relaunch_executable else ""
        powershell_cmd = (
            "$ErrorActionPreference = 'Stop'; "
            f"$pidToWait = {int(app_pid)}; "
            "$deadline = (Get-Date).AddMinutes(2); "
            "while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) { "
            "Start-Sleep -Milliseconds 500; "
            "if ((Get-Date) -gt $deadline) { throw 'Timed out waiting for PC AutoSpec to close.' } "
            "}; "
            "Start-Sleep -Seconds 1; "
            f"$zip = '{zip_path}'; "
            f"$dest = '{dest_path}'; "
            f"$stage = '{stage_path}'; "
            f"$pending = '{pending_path}'; "
            f"$log = '{apply_log_path}'; "
            f"$relaunch = '{relaunch_path}'; "
            "'Starting portable update apply' | Out-File -FilePath $log -Encoding utf8 -Append; "
            "if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }; "
            "New-Item -ItemType Directory -Path $stage | Out-Null; "
            "Expand-Archive -Path $zip -DestinationPath $stage -Force; "
            "$items = @(Get-ChildItem -Path $stage -Force); "
            "$source = if ($items.Count -eq 1 -and $items[0].PSIsContainer) { $items[0].FullName } else { $stage }; "
            "& robocopy $source $dest /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD logs /XF settings.json pending-update.json | Out-Null; "
            "if ($LASTEXITCODE -gt 7) { throw ('Robocopy failed with exit code ' + $LASTEXITCODE) }; "
            "if (Test-Path $pending) { Remove-Item $pending -Force -ErrorAction SilentlyContinue }; "
            "'Portable update apply complete' | Out-File -FilePath $log -Encoding utf8 -Append; "
            "if ($relaunch -and (Test-Path $relaunch)) { "
            "Start-Sleep -Seconds 1; "
            "Start-Process -FilePath $relaunch -WorkingDirectory $dest | Out-Null; "
            "'Relaunched PC AutoSpec after portable update' | Out-File -FilePath $log -Encoding utf8 -Append "
            "} "
            "Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue"
        )
        launcher_script.write_text(
            "@echo off\n"
            f"powershell -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command \"{powershell_cmd}\"\n"
            'del "%~f0"\n',
            encoding="utf-8",
        )
    else:
        installer_cmd = f'start "" "{package}"'
        if install_dir:
            installer_cmd += f' /DIR="{install_dir}"'
        launcher_script.write_text(
            "@echo off\n"
            f"powershell -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command \""
            f"$pidToWait = {int(app_pid)}; "
            "$deadline = (Get-Date).AddMinutes(2); "
            "while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) { "
            "Start-Sleep -Milliseconds 500; "
            "if ((Get-Date) -gt $deadline) { exit 1 } "
            "}; "
            "Start-Sleep -Seconds 1; "
            "\"\n"
            f"{installer_cmd}\n"
            'del "%~f0"\n',
            encoding="utf-8",
        )

    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS

    subprocess.Popen(
        ["cmd.exe", "/c", str(launcher_script)],
        close_fds=True,
        creationflags=creationflags,
    )
