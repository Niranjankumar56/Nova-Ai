import os
import shutil
import time
import winreg
from pathlib import Path
from typing import Optional, Tuple

_KNOWN_APP_SPECS = {
    "chrome": {
        "exes": ["chrome.exe"],
        "paths": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ],
        "proc_names": ["chrome.exe"],
    },
    "google chrome": {
        "exes": ["chrome.exe"],
        "paths": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "proc_names": ["chrome.exe"],
    },
    "notepad": {
        "exes": ["notepad.exe"],
        "paths": [r"C:\Windows\System32\notepad.exe", r"C:\Windows\notepad.exe"],
        "proc_names": ["notepad.exe"],
    },
    "calculator": {
        "exes": ["calc.exe"],
        "paths": [r"C:\Windows\System32\calc.exe"],
        "protocol": "ms-calculator:",
        "proc_names": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
    },
    "calc": {
        "exes": ["calc.exe"],
        "paths": [r"C:\Windows\System32\calc.exe"],
        "protocol": "ms-calculator:",
        "proc_names": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
    },
    "vs code": {
        "exes": ["code.cmd", "code.exe"],
        "paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "proc_names": ["Code.exe"],
    },
    "vscode": {
        "exes": ["code.cmd", "code.exe"],
        "paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "proc_names": ["Code.exe"],
    },
    "code": {
        "exes": ["code.cmd", "code.exe"],
        "paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "proc_names": ["Code.exe"],
    },
    "edge": {
        "exes": ["msedge.exe"],
        "paths": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
        "proc_names": ["msedge.exe"],
    },
    "explorer": {
        "exes": ["explorer.exe"],
        "paths": [r"C:\Windows\explorer.exe"],
        "proc_names": ["explorer.exe"],
    },
    "word": {
        "exes": ["winword.exe"],
        "proc_names": ["WINWORD.EXE"],
    },
    "excel": {
        "exes": ["excel.exe"],
        "proc_names": ["EXCEL.EXE"],
    },
    "settings": {
        "protocol": "ms-settings:",
        "proc_names": ["SystemSettings.exe"],
    },
}

_APP_PATHS_CACHE = None
_SHORTCUTS_CACHE = None

def _get_registry_app_paths() -> dict[str, str]:
    global _APP_PATHS_CACHE
    if _APP_PATHS_CACHE is not None:
        return _APP_PATHS_CACHE
    apps = {}
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
    _APP_PATHS_CACHE = apps
    return apps

def _get_start_menu_shortcuts() -> dict[str, str]:
    global _SHORTCUTS_CACHE
    if _SHORTCUTS_CACHE is not None:
        return _SHORTCUTS_CACHE
    shortcuts = {}
    dirs = [
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    for sm_dir in dirs:
        if sm_dir.exists():
            for lnk in sm_dir.rglob("*.lnk"):
                shortcuts[lnk.stem.lower()] = str(lnk)
    _SHORTCUTS_CACHE = shortcuts
    return shortcuts

def resolve_application(name: str) -> Optional[dict]:
    q = name.strip().lower()
    
    # 1. Known Specs
    if q in _KNOWN_APP_SPECS:
        spec = _KNOWN_APP_SPECS[q]
        if "protocol" in spec:
            return {"type": "protocol", "target": spec["protocol"], "proc_names": spec.get("proc_names", [])}
        for p in spec.get("paths", []):
            if Path(p).exists():
                return {"type": "exe", "target": p, "proc_names": spec.get("proc_names", [Path(p).name])}
        for exe in spec.get("exes", []):
            found = shutil.which(exe)
            if found:
                return {"type": "exe", "target": found, "proc_names": spec.get("proc_names", [exe])}

    # 2. Registry App Paths
    app_paths = _get_registry_app_paths()
    if q in app_paths:
        return {"type": "exe", "target": app_paths[q], "proc_names": [Path(app_paths[q]).name]}
    if f"{q}.exe" in app_paths:
        return {"type": "exe", "target": app_paths[f"{q}.exe"], "proc_names": [Path(app_paths[f"{q}.exe"]).name]}

    # 3. System PATH
    found = shutil.which(q) or shutil.which(f"{q}.exe")
    if found:
        return {"type": "exe", "target": found, "proc_names": [Path(found).name]}

    # 4. Start Menu Shortcuts
    shortcuts = _get_start_menu_shortcuts()
    if q in shortcuts:
        return {"type": "shortcut", "target": shortcuts[q], "proc_names": [f"{q}.exe"]}
    for k, v in shortcuts.items():
        if q in k or k in q:
            return {"type": "shortcut", "target": v, "proc_names": [f"{Path(v).stem}.exe"]}

    return None

t0 = time.time()
apps_to_test = ["Chrome", "Notepad", "Calculator", "VS Code", "Edge", "Excel", "NonExistentApp"]
for a in apps_to_test:
    res = resolve_application(a)
    print(f"'{a}' -> {res}")
t1 = time.time()
print(f"Resolved {len(apps_to_test)} applications in {t1 - t0:.4f}s total!")

