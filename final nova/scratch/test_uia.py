import sys
import tempfile
import traceback
from pathlib import Path

temp_gen = Path(tempfile.gettempdir()) / "nova_comtypes_gen"
temp_gen.mkdir(parents=True, exist_ok=True)

try:
    import comtypes.client
    comtypes.client.gen_dir = str(temp_gen)
    import comtypes.gen
    if str(temp_gen) not in comtypes.gen.__path__:
        comtypes.gen.__path__.insert(0, str(temp_gen))
except Exception as e:
    print(f"comtypes prep notice: {e}")

try:
    import uiautomation as auto
    print(f"UIA import succeeded! Version: {getattr(auto, 'VERSION', 'ok')}")
except Exception as e:
    traceback.print_exc()

try:
    import pythoncom
    pythoncom.CoInitialize()
except Exception:
    pass

try:
    fg = auto.GetForegroundControl()
    print("Foreground control:", fg)
except Exception:
    traceback.print_exc()

try:
    root = auto.GetRootControl()
    print("Root control:", root)
    if root:
        print("Root Name:", root.Name)
        children = root.GetChildren()
        print(f"Root has {len(children)} top-level children:")
        for c in children[:5]:
            print(f"  - {c.Name} ({c.ControlTypeName})")
except Exception:
    traceback.print_exc()
