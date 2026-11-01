import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np
from PIL import Image
from core.screen_reader import capture_screen_image

img, size, offset = capture_screen_image()
print(f"Captured screen image: {img.size}")

img_np = np.array(img.convert("RGB"))
gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
contrast = np.std(gray)
mean_b = np.mean(gray)
print(f"Raw image stats -> Mean: {mean_b:.1f}, Contrast (StdDev): {contrast:.1f}")

# Test CLAHE
clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
clahe_img = clahe.apply(gray)
print(f"CLAHE contrast: {np.std(clahe_img):.1f}")

# Test Unsharp Mask
blurred = cv2.GaussianBlur(gray, (0, 0), 2.0)
sharp = cv2.addWeighted(gray, 1.5, blurred, -0.5, 0)
print(f"Unsharp mask contrast: {np.std(sharp):.1f}")

