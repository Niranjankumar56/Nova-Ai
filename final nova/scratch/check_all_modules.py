import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

modules = [
    "main",
    "ui",
    "core.screen_reader",
    "actions.computer_control",
    "core.action_planner",
    "core.gemini",
    "core.audio_devices",
    "core.stt",
    "core.tts",
    "core.wake_word",
    "core.confirm",
    "memory.config_manager",
]

errors = []
for mod in modules:
    try:
        importlib.import_module(mod)
        print(f"  [OK] {mod}")
    except Exception as e:
        print(f"  [FAIL] {mod}: {e}")
        errors.append((mod, str(e)))

if not errors:
    print(f"\nALL {len(modules)} CORE MODULES IMPORTED CLEANLY WITH ZERO ERRORS!")
else:
    print(f"\nFAILED MODULES: {errors}")
