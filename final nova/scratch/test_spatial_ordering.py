def compute_iou(b1, b2):
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    inter_w = max(0, x2 - x1)
    inter_h = max(0, y2 - y1)
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    area1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0

def sort_elements_reading_order(elements):
    # Sort into horizontal bands
    if not elements:
        return []
    # Sort primarily by y, then x
    sorted_y = sorted(elements, key=lambda e: (e['bbox'][1], e['bbox'][0]))
    bands = []
    current_band = []
    current_y = None
    line_threshold = 12

    for e in sorted_y:
        y = e['bbox'][1]
        if current_y is None or abs(y - current_y) <= line_threshold:
            current_band.append(e)
            current_y = y if current_y is None else min(current_y, y)
        else:
            bands.append(sorted(current_band, key=lambda item: item['bbox'][0]))
            current_band = [e]
            current_y = y
    if current_band:
        bands.append(sorted(current_band, key=lambda item: item['bbox'][0]))
    
    result = []
    for band in bands:
        result.extend(band)
    return result

# Test sample
sample = [
    {"text": "Edit", "bbox": (100, 20, 140, 35)},
    {"text": "File", "bbox": (50, 20, 90, 35)},
    {"text": "View", "bbox": (150, 22, 190, 37)},
    {"text": "Main Title", "bbox": (50, 100, 200, 130)},
    {"text": "Cancel", "bbox": (250, 300, 300, 330)},
    {"text": "OK", "bbox": (180, 300, 220, 330)},
]

sorted_res = sort_elements_reading_order(sample)
print("Reading order result:")
for e in sorted_res:
    print(f"  {e['text']} @ {e['bbox']}")

