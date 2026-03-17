"""
PC AutoSpec — Automated PC diagnostics for RepairDesk
Main launcher script
"""
import sys
import os
from pathlib import Path


def setup_dll_directory():
    """Add PyInstaller's temporary directory to DLL search path"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        try:
            import ctypes
            import ctypes.wintypes

            meipass_dir = sys._MEIPASS

            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.SetDllDirectoryW.argtypes = [ctypes.wintypes.LPCWSTR]
            kernel32.SetDllDirectoryW.restype = ctypes.wintypes.BOOL

            if hasattr(kernel32, 'AddDllDirectory'):
                kernel32.AddDllDirectory.argtypes = [ctypes.wintypes.LPCWSTR]
                kernel32.AddDllDirectory.restype = ctypes.wintypes.LPVOID
                kernel32.AddDllDirectory(meipass_dir)

            kernel32.SetDllDirectoryW(meipass_dir)
            os.environ['PATH'] = meipass_dir + os.pathsep + os.environ.get('PATH', '')
        except Exception:
            pass


def is_admin():
    """Check if running with administrator privileges"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    """Relaunch elevated via ShellExecute runas — triggers real Windows UAC prompt.
    Returns True if elevated instance was launched (caller should exit).
    Returns False if elevation failed or was declined (continue without admin).
    """
    try:
        import ctypes

        if getattr(sys, 'frozen', False):
            # Frozen exe — just re-run ourselves
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, None,
                os.path.dirname(sys.executable), 1
            )
        else:
            # Running from source — re-run python with this script
            script = os.path.abspath(__file__)
            params = f'"{script}"'
            if len(sys.argv) > 1:
                params += ' ' + ' '.join(f'"{a}"' for a in sys.argv[1:])
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params,
                os.path.dirname(script), 1
            )

        return ret > 32  # > 32 = success
    except Exception:
        return False


if __name__ == "__main__":
    setup_dll_directory()

    if not is_admin():
        # Trigger real UAC prompt — no homemade dialogs
        if elevate():
            sys.exit(0)  # Elevated instance launched, this one exits
        # If we're here, user clicked No on UAC or it failed — continue without admin

    # Add src directory to Python path
    src_path = Path(__file__).parent / "src"
    sys.path.insert(0, str(src_path))

    from AutoSpecUploaderGUI import main
    main()
