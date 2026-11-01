import tempfile
from pathlib import Path
import ctypes

temp_gen = Path(tempfile.gettempdir()) / "nova_comtypes_gen"
temp_gen.mkdir(parents=True, exist_ok=True)

try:
    import comtypes.client
    comtypes.client.gen_dir = str(temp_gen)
    import comtypes.gen
    if str(temp_gen) not in comtypes.gen.__path__:
        comtypes.gen.__path__.insert(0, str(temp_gen))
except Exception as e:
    pass

import uiautomation as auto

def safe_get_foreground_control():
    user32 = ctypes.windll.user32
    try:
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl and ctrl.Exists(0, 0):
                return ctrl
    except Exception as e:
        print("ControlFromHandle failed:", e)
    try:
        root = auto.GetRootControl()
        if root and root.Exists(0, 0):
            return root
    except Exception as e:
        print("GetRootControl failed:", e)
    return None

ctrl = safe_get_foreground_control()
print("safe_get_foreground_control result:", ctrl)
if ctrl:
    print("Name:", ctrl.Name, "Type:", ctrl.ControlTypeName)

