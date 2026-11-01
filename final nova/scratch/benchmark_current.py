import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.screen_reader import (
    get_active_window_info,
    capture_screen_image,
    extract_uia_elements,
    extract_ocr_elements,
    answer_screen_question,
)

t0 = time.time()
win = get_active_window_info()
print("Active window:", win)

img, size, offset = capture_screen_image()
print(f"Screenshot: size={size}, offset={offset}, img={img is not None} in {time.time()-t0:.2f}s")

t1 = time.time()
uia = extract_uia_elements(max_elements=50)
print(f"UIA elements: {len(uia)} in {time.time()-t1:.2f}s")
for e in uia[:5]:
    print(f"  [UIA] {e.element_type}: '{e.text}' at {e.center}")

t2 = time.time()
ocr = extract_ocr_elements(img, min_confidence=30)
print(f"OCR elements: {len(ocr)} in {time.time()-t2:.2f}s")
for e in ocr[:5]:
    print(f"  [OCR] {e.element_type}: '{e.text}' at {e.center}")

t3 = time.time()
ans = answer_screen_question("What is on my screen?")
print(f"Screen QA in {time.time()-t3:.2f}s:")
print(ans[:300])
