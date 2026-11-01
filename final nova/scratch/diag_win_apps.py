import winreg
import os
import shutil
from pathlib import Path

def get_registered_app_paths():
    apps = {}
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for root, subkey in keys:
        try:
            with winreg.OpenKey(root, subkey) as key:
                num_subkeys = winreg.QueryInfoKey(key)[0]
                for i in range(num_subkeys):
                    try:
                        app_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, app_name) as app_key:
                            exe_path, _ = winreg.QueryValueEx(app_key, "")
                            if exe_path:
                                exe_clean = exe_path.strip('"')
                                if Path(exe_clean).exists():
                                    apps[app_name.lower()] = exe_clean
                    except Exception:
                        pass
        except Exception as e:
            print(f"Error reading {subkey}: {e}")
    return apps

app_paths = get_registered_app_paths()
print(f"Discovered {len(app_paths)} registered App Paths:")
sample_keys = ["chrome.exe", "msedge.exe", "code.exe", "notepad.exe", "calc.exe", "excel.exe"]
for k in sample_keys:
    if k in app_paths:
        print(f"  {k} -> {app_paths[k]}")
    else:
        print(f"  {k} -> NOT IN APP PATHS")

# Check Start Menu shortcuts
start_menu_dirs = [
    Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]
shortcuts = {}
for sm_dir in start_menu_dirs:
    if sm_dir.exists():
        for lnk in sm_dir.rglob("*.lnk"):
            shortcuts[lnk.stem.lower()] = str(lnk)

print(f"\nDiscovered {len(shortcuts)} Start Menu shortcuts.")
for query in ["chrome", "google chrome", "visual studio code", "notepad", "calculator"]:
    match = shortcuts.get(query)
    print(f"  Shortcut for '{query}': {match}")

