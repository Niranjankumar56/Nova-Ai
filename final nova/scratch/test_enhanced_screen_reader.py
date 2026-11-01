import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
from core.screen_reader import (
    capture_screen_state,
    locate_target,
    answer_screen_question,
    get_active_window_info,
    parse_query_qualifiers,
)

print("1. Window info:")
win = get_active_window_info()
print(f"   Title: '{win['title']}', Process: '{win['process_name']}', Rect: {win['rect']}")

print("\n2. Capturing screen state...")
t0 = time.time()
st = capture_screen_state(include_ocr=True, include_vision=True)
t1 = time.time()
print(f"   Captured in {t1 - t0:.2f}s. Elements count: {len(st.elements)}")
print(f"   Sections detected: {list(st.sections.keys())}")
for sec, elist in st.sections.items():
    print(f"     - {sec}: {len(elist)} elements")

print("\n3. Testing query qualifier parser:")
test_queries = [
    "Click the second Edit button",
    "Click Edit in the Profile section",
    "Click Save at the top",
    "Where is the File option?",
]
for tq in test_queries:
    print(f"   '{tq}' -> {parse_query_qualifiers(tq)}")

print("\n4. Locating target 'File':")
loc_file = locate_target("File", screen_state=st)
print(f"   Found: {loc_file['found']}, Center: {loc_file['center']}, Method: {loc_file['method']}")

print("\n5. Testing Screen QA:")
ans_overview = answer_screen_question("What is on my screen?", screen_state=st)
print(f"   QA Overview:\n{ans_overview}")

ans_buttons = answer_screen_question("What options are available here?", screen_state=st)
print(f"\n   QA Buttons:\n{ans_buttons}")

