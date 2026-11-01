import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from actions.open_app import open_app, launch_application, resolve_application

print("1. Testing resolve_application:")
for app in ["Chrome", "Notepad", "Calculator", "VS Code", "NonExistentApp123"]:
    spec = resolve_application(app)
    print(f"  '{app}' -> {spec}")

print("\n2. Testing open_app with Notepad:")
msg = open_app({"app_name": "Notepad"})
print(f"  Result: {msg}")

print("\n3. Testing open_app with NonExistentApp:")
msg_fail = open_app({"app_name": "NonExistentApp123"})
print(f"  Result: {msg_fail}")

print("\n4. Testing close_app with Notepad:")
msg_close = open_app({"app_name": "Notepad", "action": "close"})
print(f"  Result: {msg_close}")

