"""
actions/open_app.py — Robust Application Launcher & Process Controller for NOVA.

Provides:
  • Fast application discovery via Windows Registry App Paths, Start Menu shortcuts,
    UWP protocols, standard program paths, and PATH binaries.
  • Correct handling of executable paths with spaces.
  • Already-running detection: brings existing window to front rather than spawning duplicates.
  • Post-launch verification: confirms process/window actually appeared before reporting success.
  • Meaningful error messages for missing, blocked, or uninstalled applications.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Tuple, List, Dict

try:
    import psutil
    _PSUTIL = True
except ImportError:
    psutil = None
    _PSUTIL = False

_SYSTEM = platform.system()

_KNOWN_APP_SPECS: dict[str, dict[str, Any]] = {
    "chrome": {
        "exes": ["chrome.exe"],
        "paths": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ],
        "proc_names": ["chrome.exe"],
        "display_name": "Google Chrome",
    },
    "google chrome": {
        "exes": ["chrome.exe"],
        "paths": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ],
        "proc_names": ["chrome.exe"],
        "display_name": "Google Chrome",
    },
    "browser": {
        "exes": ["chrome.exe", "msedge.exe", "firefox.exe"],
        "paths": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ],
        "proc_names": ["chrome.exe", "msedge.exe", "firefox.exe"],
        "display_name": "Web Browser",
    },
    "notepad": {
        "exes": ["notepad.exe"],
        "paths": [r"C:\Windows\System32\notepad.exe", r"C:\Windows\notepad.exe"],
        "proc_names": ["notepad.exe", "Notepad.exe"],
        "display_name": "Notepad",
    },
    "calculator": {
        "exes": ["calc.exe"],
        "paths": [r"C:\Windows\System32\calc.exe"],
        "protocol": "ms-calculator:",
        "proc_names": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
        "display_name": "Calculator",
    },
    "calc": {
        "exes": ["calc.exe"],
        "paths": [r"C:\Windows\System32\calc.exe"],
        "protocol": "ms-calculator:",
        "proc_names": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
        "display_name": "Calculator",
    },
    "vs code": {
        "exes": ["code.cmd", "code.exe"],
        "paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
            r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
        ],
        "proc_names": ["Code.exe"],
        "display_name": "Visual Studio Code",
    },
    "vscode": {
        "exes": ["code.cmd", "code.exe"],
        "paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "proc_names": ["Code.exe"],
        "display_name": "Visual Studio Code",
    },
    "visual studio code": {
        "exes": ["code.cmd", "code.exe"],
        "paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "proc_names": ["Code.exe"],
        "display_name": "Visual Studio Code",
    },
    "code": {
        "exes": ["code.cmd", "code.exe"],
        "paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "proc_names": ["Code.exe"],
        "display_name": "Visual Studio Code",
    },
    "edge": {
        "exes": ["msedge.exe"],
        "paths": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
        "proc_names": ["msedge.exe"],
        "display_name": "Microsoft Edge",
    },
    "msedge": {
        "exes": ["msedge.exe"],
        "paths": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
        "proc_names": ["msedge.exe"],
        "display_name": "Microsoft Edge",
    },
    "microsoft edge": {
        "exes": ["msedge.exe"],
        "paths": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
        "proc_names": ["msedge.exe"],
        "display_name": "Microsoft Edge",
    },
    "explorer": {
        "exes": ["explorer.exe"],
        "paths": [r"C:\Windows\explorer.exe"],
        "proc_names": ["explorer.exe"],
        "display_name": "File Explorer",
    },
    "file explorer": {
        "exes": ["explorer.exe"],
        "paths": [r"C:\Windows\explorer.exe"],
        "proc_names": ["explorer.exe"],
        "display_name": "File Explorer",
    },
    "word": {
        "exes": ["winword.exe"],
        "proc_names": ["WINWORD.EXE", "winword.exe"],
        "display_name": "Microsoft Word",
    },
    "excel": {
        "exes": ["excel.exe"],
        "proc_names": ["EXCEL.EXE", "excel.exe"],
        "display_name": "Microsoft Excel",
    },
    "powerpoint": {
        "exes": ["powerpnt.exe"],
        "proc_names": ["POWERPNT.EXE", "powerpnt.exe"],
        "display_name": "Microsoft PowerPoint",
    },
    "settings": {
        "protocol": "ms-settings:",
        "proc_names": ["SystemSettings.exe"],
        "display_name": "Windows Settings",
    },
    "terminal": {
        "exes": ["wt.exe", "powershell.exe", "cmd.exe"],
        "proc_names": ["WindowsTerminal.exe", "wt.exe", "powershell.exe", "cmd.exe"],
        "display_name": "Terminal",
    },
    "cmd": {
        "exes": ["cmd.exe"],
        "paths": [r"C:\Windows\System32\cmd.exe"],
        "proc_names": ["cmd.exe"],
        "display_name": "Command Prompt",
    },
    "powershell": {
        "exes": ["powershell.exe"],
        "paths": [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"],
        "proc_names": ["powershell.exe"],
        "display_name": "PowerShell",
    },
    "task manager": {
        "exes": ["taskmgr.exe"],
        "paths": [r"C:\Windows\System32\taskmgr.exe"],
        "proc_names": ["Taskmgr.exe", "taskmgr.exe"],
        "display_name": "Task Manager",
    },
    "paint": {
        "exes": ["mspaint.exe"],
        "paths": [r"C:\Windows\System32\mspaint.exe"],
        "proc_names": ["mspaint.exe"],
        "display_name": "Paint",
    },
    "spotify": {
        "exes": ["Spotify.exe"],
        "paths": [str(Path.home() / "AppData" / "Roaming" / "Spotify" / "Spotify.exe")],
        "proc_names": ["Spotify.exe"],
        "display_name": "Spotify",
    },
}

_APP_PATHS_CACHE: Optional[dict[str, str]] = None
_SHORTCUTS_CACHE: Optional[dict[str, str]] = None


def _get_registry_app_paths() -> dict[str, str]:
    """Read registered application executables from Windows App Paths."""
    global _APP_PATHS_CACHE
    if _APP_PATHS_CACHE is not None:
        return _APP_PATHS_CACHE

    apps = {}
    if _SYSTEM != "Windows":
        _APP_PATHS_CACHE = apps
        return apps

    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE,):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", 0, winreg.KEY_READ) as key:
                    count = winreg.QueryInfoKey(key)[0]
                    for i in range(count):
                        try:
                            k_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, k_name) as app_key:
                                val, _ = winreg.QueryValueEx(app_key, "")
                                if val:
                                    p = val.strip('"')
                                    if Path(p).exists():
                                        apps[k_name.lower()] = p
                                        apps[Path(k_name).stem.lower()] = p
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass

    _APP_PATHS_CACHE = apps
    return apps


def _get_start_menu_shortcuts() -> dict[str, str]:
    """Scan Start Menu directories for .lnk shortcuts."""
    global _SHORTCUTS_CACHE
    if _SHORTCUTS_CACHE is not None:
        return _SHORTCUTS_CACHE

    shortcuts = {}
    if _SYSTEM != "Windows":
        _SHORTCUTS_CACHE = shortcuts
        return shortcuts

    dirs = [
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    for sm_dir in dirs:
        if sm_dir.exists():
            try:
                for lnk in sm_dir.rglob("*.lnk"):
                    shortcuts[lnk.stem.lower()] = str(lnk)
            except Exception:
                pass

    _SHORTCUTS_CACHE = shortcuts
    return shortcuts


def resolve_application(name: str) -> Optional[dict[str, Any]]:
    """
    Resolve an application query string to a verified launch target.
    Returns dict with {type: 'exe'|'shortcut'|'protocol', target: str, proc_names: list, display_name: str}
    or None if the application cannot be found.
    """
    raw = str(name or "").strip()
    q = raw.lower()
    q = re.sub(r"^(?:open|launch|start|bring\s+up|run)\s+(?:the\s+)?", "", q, flags=re.I).strip()
    q = re.sub(r"\s+app(?:lication)?$", "", q, flags=re.I).strip()

    if not q:
        return None

    # 1. Check Known App Specs
    if q in _KNOWN_APP_SPECS:
        spec = _KNOWN_APP_SPECS[q]
        disp = spec.get("display_name", raw)
        procs = spec.get("proc_names", [])
        if "protocol" in spec:
            return {"type": "protocol", "target": spec["protocol"], "proc_names": procs, "display_name": disp}
        for p in spec.get("paths", []):
            if Path(p).exists():
                return {"type": "exe", "target": p, "proc_names": procs, "display_name": disp}
        for exe in spec.get("exes", []):
            found = shutil.which(exe)
            if found:
                return {"type": "exe", "target": found, "proc_names": procs, "display_name": disp}

    # 2. Check Windows Registry App Paths
    app_paths = _get_registry_app_paths()
    if q in app_paths:
        t = app_paths[q]
        return {"type": "exe", "target": t, "proc_names": [Path(t).name], "display_name": Path(t).stem}
    if f"{q}.exe" in app_paths:
        t = app_paths[f"{q}.exe"]
        return {"type": "exe", "target": t, "proc_names": [Path(t).name], "display_name": Path(t).stem}

    # 3. Check System PATH
    found = shutil.which(q) or shutil.which(f"{q}.exe")
    if found:
        return {"type": "exe", "target": found, "proc_names": [Path(found).name], "display_name": Path(found).stem}

    # 4. Check Start Menu Shortcuts (.lnk)
    shortcuts = _get_start_menu_shortcuts()
    if q in shortcuts:
        s = shortcuts[q]
        return {"type": "shortcut", "target": s, "proc_names": [f"{Path(s).stem}.exe"], "display_name": Path(s).stem}
    for k, v in shortcuts.items():
        if q in k or k in q:
            return {"type": "shortcut", "target": v, "proc_names": [f"{Path(v).stem}.exe"], "display_name": Path(v).stem}

    # 5. Check direct executable path
    direct = Path(raw.strip('"').strip("'")).expanduser()
    if direct.exists() and direct.is_file() and direct.suffix.lower() in (".exe", ".bat", ".cmd", ".msi", ".com"):
        return {"type": "exe", "target": str(direct), "proc_names": [direct.name], "display_name": direct.stem}

    return None


def _is_process_running(proc_names: list[str]) -> tuple[bool, Optional[int]]:
    """Check if any of the given process names are currently running."""
    if not _PSUTIL or psutil is None:
        return False, None
    lower_names = {p.lower() for p in proc_names}
    try:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = (p.info.get("name") or "").lower()
                if name in lower_names:
                    return True, p.info.get("pid")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False, None


def _bring_process_window_to_front(pid: Optional[int], app_name: str) -> bool:
    """Find visible top-level window for PID or process title and bring to front."""
    if _SYSTEM != "Windows":
        return False

    try:
        user32 = ctypes.windll.user32
        matched_hwnd = None

        def enum_win_proc(hwnd, lparam):
            nonlocal matched_hwnd
            if user32.IsWindowVisible(hwnd):
                win_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
                if pid and win_pid.value == pid:
                    matched_hwnd = hwnd
                    return False
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if app_name.lower() in buf.value.lower():
                        matched_hwnd = hwnd
                        return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_win_proc), 0)

        if matched_hwnd:
            SW_RESTORE = 9
            user32.ShowWindow(matched_hwnd, SW_RESTORE)
            user32.SetForegroundWindow(matched_hwnd)
            return True
    except Exception as e:
        print(f"[open_app] _bring_process_window_to_front error: {e}")

    return False


def launch_application(app_name: str) -> tuple[bool, str]:
    """
    Core Application Launch Engine:
      1. Resolves app against installed applications, registries, and shortcuts.
      2. If already running, activates and brings existing window to foreground.
      3. Launches process cleanly without fragile GUI typing.
      4. Polls and verifies process execution before returning success.
    """
    started = time.monotonic()
    spec = resolve_application(app_name)

    if spec is None:
        elapsed = time.monotonic() - started
        return False, f"Application '{app_name}' is not installed or could not be found on this computer. ({elapsed:.2f}s)"

    disp_name = spec.get("display_name", app_name)
    proc_names = spec.get("proc_names", [])

    # Check if already running
    is_running, pid = _is_process_running(proc_names)
    if is_running:
        brought_front = _bring_process_window_to_front(pid, disp_name)
        elapsed = time.monotonic() - started
        front_msg = " and brought its window to the foreground" if brought_front else ""
        return True, f"'{disp_name}' is already running{front_msg}. ({elapsed:.2f}s)"

    target = spec["target"]
    target_type = spec["type"]

    try:
        if target_type == "exe":
            # Direct binary launch
            if " " in target and not target.startswith('"'):
                subprocess.Popen([target])
            else:
                subprocess.Popen(target, shell=True)
        elif target_type in ("shortcut", "protocol"):
            if _SYSTEM == "Windows":
                os.startfile(target)
            else:
                subprocess.Popen(["open", target])
        else:
            subprocess.Popen(target, shell=True)

    except Exception as e:
        elapsed = time.monotonic() - started
        return False, f"Failed to execute '{disp_name}': {e} ({elapsed:.2f}s)"

    # Verify launch: poll for process or window appearance (max 3.5s)
    verified = False
    for _ in range(7):
        time.sleep(0.45)
        running, _ = _is_process_running(proc_names)
        if running:
            verified = True
            break

    elapsed = time.monotonic() - started
    if verified:
        return True, f"Successfully launched {disp_name}. ({elapsed:.2f}s)"
    else:
        return False, (
            f"Attempted to launch '{disp_name}', but process did not report running within 3.5 seconds. "
            f"It may still be loading or was blocked by Windows."
        )


def close_application(app_name: str) -> tuple[bool, str]:
    """Gracefully close or terminate an application by name or process."""
    spec = resolve_application(app_name)
    proc_names = spec.get("proc_names", [f"{app_name}.exe"]) if spec else [f"{app_name}.exe", app_name]
    disp = spec.get("display_name", app_name) if spec else app_name

    closed_any = False
    if _PSUTIL and psutil:
        lower_procs = {p.lower() for p in proc_names}
        for p in psutil.process_iter(["name", "pid"]):
            try:
                p_name = (p.info.get("name") or "").lower()
                if p_name in lower_procs:
                    p.terminate()
                    closed_any = True
            except Exception:
                pass

    if not closed_any:
        for p_name in proc_names:
            clean = p_name if p_name.lower().endswith(".exe") else f"{p_name}.exe"
            try:
                res = subprocess.run(["taskkill", "/IM", clean, "/T", "/F"], capture_output=True, timeout=5)
                if res.returncode == 0:
                    closed_any = True
            except Exception:
                pass

    if closed_any:
        return True, f"Closed {disp}."
    return False, f"Could not find running process for '{disp}' to close."


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Action entry point for open_app tool."""
    params = parameters or {}
    app_name = str(params.get("app_name") or params.get("name") or params.get("target") or "").strip()
    action = str(params.get("action", "open")).lower().strip()

    if not app_name:
        return "No application name provided."

    if action in ("close", "quit", "kill", "terminate") or app_name.lower().startswith("close "):
        clean_name = app_name[6:].strip() if app_name.lower().startswith("close ") else app_name
        if player:
            try:
                player.write_log(f"SYS: [open_app] Close {clean_name}")
            except Exception:
                pass
        ok, msg = close_application(clean_name)
        return msg

    if player:
        try:
            player.write_log(f"SYS: [open_app] Launch {app_name}")
        except Exception:
            pass

    ok, msg = launch_application(app_name)
    return msg


TOOL = {
    "name": "open_app",
    "description": (
        "Opens or closes desktop applications on the computer. "
        "Use this whenever the user asks to open, launch, start, close, or quit any app (e.g. 'Chrome', 'Notepad', 'Calculator', 'VS Code', 'Explorer'). "
        "Always call this tool — never claim an application opened without executing this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "Name of the application to open or close (e.g. 'Chrome', 'Notepad', 'Calculator', 'VS Code', 'Edge', 'Word').",
            },
            "action": {
                "type": "STRING",
                "description": "open (default) | close",
            },
        },
        "required": [
            "app_name",
        ],
    },
    "handler": open_app,
}
