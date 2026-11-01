import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
from core.screen_reader import capture_screen_image, extract_ocr_elements

t0 = time.time()
img, size, offset = capture_screen_image()
t1 = time.time()
elems = extract_ocr_elements(img)
t2 = time.time()

print(f"Captured screen {size} in {t1 - t0:.2f}s.")
print(f"Detected {len(elems)} OCR elements in {t2 - t1:.2f}s.")
for e in elems[:15]:
    print(f"  [{e.element_type}] '{e.text}' @ {e.center} bbox={e.bbox} (conf {e.confidence:.2f})")
