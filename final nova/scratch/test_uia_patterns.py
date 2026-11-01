import tempfile
from pathlib import Path

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
print("UIA imported successfully:", auto.VERSION if hasattr(auto, 'VERSION') else 'ok')

try:
    # Try ControlFromPoint
    pt_ctrl = auto.ControlFromPoint(100, 100)
    print("Control at (100, 100):", pt_ctrl)
    if pt_ctrl:
        print("  Name:", pt_ctrl.Name, "Type:", pt_ctrl.ControlTypeName)
        inv = pt_ctrl.GetInvokePattern()
        print("  Has InvokePattern:", inv is not None)
except Exception as e:
    print("ControlFromPoint error:", e)

