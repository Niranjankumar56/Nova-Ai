import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np
from core.screen_reader import capture_screen_image, get_active_window_info

img, size, offset = capture_screen_image()
win_info = get_active_window_info()
print(f"Captured screen {size}, active win: {win_info.get('title')}")

img_np = np.array(img.convert("RGB"))
gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
edges = cv2.Canny(gray, 50, 150)
contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
print(f"Detected {len(contours)} contours.")

icons_found = []
for cnt in contours:
    x, y, w, h = cv2.boundingRect(cnt)
    if w < 12 or h < 12 or w > 64 or h > 64:
        continue
    aspect = float(w) / float(h)
    if 0.75 <= aspect <= 1.35:
        # Near square/circle
        area = cv2.contourArea(cnt)
        if area > 80:
            icons_found.append((x, y, w, h, aspect, area))

print(f"Found {len(icons_found)} potential icon-sized square/circular elements.")
for item in icons_found[:8]:
    print(f"  Icon candidate at ({item[0]}, {item[1]}) size {item[2]}x{item[3]}")

