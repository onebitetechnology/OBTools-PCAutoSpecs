"""
PC AutoSpec — GitHub release updater.

Checks the latest GitHub Release, downloads the Windows installer, and
launches it after the app exits.
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

from settings import APP_NAME, APP_VERSION

REPO_OWNER = "onebitetechnology"
REPO_NAME = "OBTools-PCAutoSpecs"
GITHUB_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
)
INSTALLER_ASSET_NAME = "PCAutoSpec-Setup.exe"


def is_update_supported() -> bool:
    """Return True when update download/apply is supported."""
    return sys.platform == "win32"


def _normalize_version(version: str) -> str:
    return str(version or "").strip().lstrip("vV")


def _version_key(version: str) -> tuple:
    cleaned = _normalize_version(version)
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts)


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

    installer_path = Path(metadata.get("installer_path", ""))
    if not installer_path.is_file():
        _clear_pending_metadata()
        return None

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


def check_for_updates(timeout: int = 20) -> Dict:
    """Query the latest GitHub Release and compare it to the running app version."""
    state = {
        "supported": is_update_supported(),
        "current_version": APP_VERSION,
        "latest_version": None,
        "available": False,
        "downloaded": False,
        "message": "Update checks are idle.",
        "release_notes": "",
        "installer_path": None,
        "download_url": None,
        "html_url": None,
        "published_at": None,
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
                "installer_path": pending.get("installer_path"),
                "message": (
                    f"Update {pending_version} has already been downloaded. "
                    "Use Install Update Now to apply it."
                ),
            })

    try:
        response = requests.get(
            GITHUB_LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        response.raise_for_status()
        release_data = response.json()
    except Exception as e:
        if state["downloaded"]:
            return state
        state["message"] = f"Update check failed: {e}"
        return state

    latest_version = _normalize_version(
        release_data.get("tag_name") or release_data.get("name") or ""
    )
    release_notes = str(release_data.get("body") or "").strip()
    installer_asset = _select_installer_asset(release_data)

    state.update({
        "latest_version": latest_version or None,
        "release_notes": release_notes,
        "html_url": release_data.get("html_url"),
        "published_at": release_data.get("published_at"),
    })

    if not latest_version:
        state["message"] = "Latest release did not include a valid version."
        return state

    if _version_key(latest_version) <= _version_key(APP_VERSION):
        state["available"] = False
        state["message"] = f"App is up to date. Current version: {APP_VERSION}"
        return state

    state["available"] = True
    if not installer_asset:
        state["message"] = (
            f"Update {latest_version} is available, but no Windows installer "
            "asset was found on the latest GitHub Release."
        )
        return state

    state["download_url"] = installer_asset.get("browser_download_url")
    if state["downloaded"] and state["installer_path"]:
        state["message"] = (
            f"Update {latest_version} has already been downloaded. "
            "Use Install Update Now to apply it."
        )
        return state

    state["message"] = f"Update {latest_version} is available."
    return state


def download_update(
    update_info: Dict,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    timeout: int = 60,
) -> Dict:
    """Download the latest installer to a local update cache."""
    if not is_update_supported():
        raise RuntimeError("Automatic update download is supported on Windows only.")

    download_url = update_info.get("download_url")
    latest_version = _normalize_version(update_info.get("latest_version"))
    if not download_url or not latest_version:
        raise RuntimeError("No downloadable update is available.")

    download_dir = _download_root()
    installer_path = download_dir / f"PCAutoSpec-Setup-{latest_version}.exe"
    temp_path = installer_path.with_suffix(".download")

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

    temp_path.replace(installer_path)
    metadata = {
        "version": latest_version,
        "installer_path": str(installer_path),
        "downloaded_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    _save_pending_metadata(metadata)

    return {
        "version": latest_version,
        "installer_path": str(installer_path),
        "message": f"Update {latest_version} downloaded and ready to install.",
    }


def launch_pending_update(installer_path: str) -> None:
    """
    Launch the installer after the app exits.

    Uses a temporary cmd script so the installer starts after the current
    process has had time to shut down and release any file locks.
    """
    if sys.platform != "win32":
        raise RuntimeError("Install update is only supported on Windows.")

    installer = Path(installer_path)
    if not installer.is_file():
        raise RuntimeError("Downloaded installer file was not found.")

    launcher_script = _download_root() / "launch-update.cmd"
    launcher_script.write_text(
        "@echo off\n"
        "ping 127.0.0.1 -n 3 > nul\n"
        f'start "" "{installer}"\n'
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
