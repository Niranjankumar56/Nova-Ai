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
except Exception:
    pass

import uiautomation as auto

root = auto.GetRootControl()
print("Root:", root)

# Find top level windows under root
count = 0
for ctrl, depth in auto.WalkControl(root, maxDepth=3):
    if count > 20:
        break
    try:
        r = ctrl.BoundingRectangle
        if r and r.width() > 10 and r.height() > 10:
            print(f"Depth {depth}: [{ctrl.ControlTypeName}] '{ctrl.Name}' Rect: ({r.left},{r.top},{r.right},{r.bottom})")
            count += 1
    except Exception as e:
        pass

