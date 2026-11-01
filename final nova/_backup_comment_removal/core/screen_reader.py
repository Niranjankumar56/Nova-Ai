"""
core/screen_reader.py — Intelligent Windows Screen Reading, Element Grounding & Verification for NOVA.

Provides:
  • Active window & application context discovery
  • Windows UI Automation (UIA) control extraction (prioritizing interactive elements over containers)
  • Local Tesseract OCR integration with multi-word phrase grouping & exact/word-boundary matching
  • Visual icon detection for unlabeled controls (profile, settings, search, etc.) + Gemini Vision fallback
  • 4-tier target localization: UIA -> OCR -> Visual Analysis -> Coordinate Validation
  • Disambiguation and ordinal selection ("click the 3rd matching result")
  • Observe-Plan-Act-Verify post-action state verification
  • Windows DPI-awareness & multi-monitor support
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import difflib
import io
import json
import os
import platform
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, List

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── DPI & Desktop Desktop Setup ──────────────────────────────────────────────

def ensure_desktop_and_dpi() -> None:
    """Ensure current thread is attached to the interactive input desktop and DPI aware."""
    if platform.system() != "Windows":
        return
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x01ff)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass
    try:
        # Per-Monitor DPI Aware v2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Run on module load
ensure_desktop_and_dpi()

# Optional UIA
try:
    import uiautomation as auto
    _UIA = True
except ImportError:
    auto = None
    _UIA = False

# Optional PIL
try:
    import PIL.Image
    import PIL.ImageGrab
    import PIL.ImageOps
    import PIL.ImageChops
    _PIL = True
except ImportError:
    _PIL = False

# Optional PyTesseract
try:
    import pytesseract
    _PYTESSERACT = True
except ImportError:
    pytesseract = None
    _PYTESSERACT = False

# Optional PyAutoGUI
try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    pyautogui = None
    _PYAUTOGUI = False

# Optional OpenCV
try:
    import cv2
    import numpy as np
    _CV2 = True
except ImportError:
    cv2 = None
    np = None
    _CV2 = False


# ── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class ScreenElement:
    text: str
    element_type: str        # 'button', 'edit', 'checkbox', 'radio_button', 'link', 'tab', 'menu_item', 'heading', 'dialog', 'icon', 'text', 'pane', 'window'
    bbox: tuple[int, int, int, int]    # (left, top, right, bottom)
    center: tuple[int, int]            # (center_x, center_y)
    confidence: float = 1.0            # 0.0 – 1.0
    enabled: bool = True
    selected: bool = False
    visible: bool = True
    interaction: str = "click"         # 'click', 'double_click', 'type', 'toggle', 'select', 'read'
    automation_id: str = ""
    class_name: str = ""
    window_title: str = ""
    source: str = "uia"                # 'uia', 'ocr', 'vision', 'heuristic'
    is_container: bool = False
    category: str = ""                 # 'profile', 'settings', 'search', 'close', 'minimize', 'maximize', etc.

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        d["center"] = list(self.center)
        return d


@dataclass
class ScreenState:
    timestamp: float
    window_title: str
    process_name: str
    hwnd: int
    window_rect: tuple[int, int, int, int]
    screen_size: tuple[int, int]
    elements: list[ScreenElement] = field(default_factory=list)
    raw_text: str = ""
    screenshot: Optional[Any] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "window_title": self.window_title,
            "process_name": self.process_name,
            "hwnd": self.hwnd,
            "window_rect": list(self.window_rect),
            "screen_size": list(self.screen_size),
            "elements_count": len(self.elements),
            "elements": [e.to_dict() for e in self.elements],
            "raw_text": self.raw_text[:2000],
        }


# ── Active Window Context ────────────────────────────────────────────────────

def get_active_window_info() -> dict[str, Any]:
    """Return active foreground window details (title, process, HWND, bounding rect)."""
    ensure_desktop_and_dpi()
    info = {
        "title": "Desktop",
        "process_name": "",
        "hwnd": 0,
        "rect": (0, 0, 1920, 1080),
        "is_dialog": False,
    }
    if platform.system() != "Windows":
        return info

    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return info
        info["hwnd"] = int(hwnd)

        # Title
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            info["title"] = buf.value.strip() or "Untitled Window"

        # Rect
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            info["rect"] = (rect.left, rect.top, rect.right, rect.bottom)

        # Process ID & Name
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            try:
                import psutil
                proc = psutil.Process(pid.value)
                info["process_name"] = proc.name()
            except Exception:
                pass

        # Check if dialog style
        style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
        WS_DLGFRAME = 0x00400000
        if style & WS_DLGFRAME:
            info["is_dialog"] = True

    except Exception as e:
        print(f"[ScreenReader] get_active_window_info error: {e}")

    return info


# ── UI Automation Extraction ─────────────────────────────────────────────────

_UIA_TYPE_MAP = {
    "ButtonControl": ("button", "click", False),
    "EditControl": ("edit", "type", False),
    "CheckBoxControl": ("checkbox", "toggle", False),
    "RadioButtonControl": ("radio_button", "select", False),
    "ComboBoxControl": ("dropdown", "click", False),
    "HyperlinkControl": ("link", "click", False),
    "TabItemControl": ("tab", "click", False),
    "MenuItemControl": ("menu_item", "click", False),
    "ListItemControl": ("list_item", "click", False),
    "TreeItemControl": ("tree_item", "click", False),
    "ScrollBarControl": ("scrollbar", "scroll", False),
    "ImageControl": ("icon", "click", False),
    "TextControl": ("text", "read", False),
    "GroupControl": ("group", "read", True),
    "HeaderControl": ("heading", "read", True),
    "HeaderItemControl": ("heading", "read", False),
    "WindowControl": ("window", "read", True),
    "PaneControl": ("pane", "read", True),
    "ToolBarControl": ("toolbar", "read", True),
}

_COMMON_ICON_PATTERNS = {
    "profile": re.compile(r"\b(profile|account|user|avatar|login|sign[ -]?in|me)\b", re.I),
    "settings": re.compile(r"\b(settings|preferences|gear|config|options|setup)\b", re.I),
    "search": re.compile(r"\b(search|find|lookup|query)\b", re.I),
    "close": re.compile(r"\b(close|exit|quit|cancel)\b", re.I),
    "minimize": re.compile(r"\b(minimize|min)\b", re.I),
    "maximize": re.compile(r"\b(maximize|restore)\b", re.I),
    "back": re.compile(r"\b(back|previous|backward)\b", re.I),
    "forward": re.compile(r"\b(forward|next)\b", re.I),
    "refresh": re.compile(r"\b(refresh|reload)\b", re.I),
    "download": re.compile(r"\b(download|downloads)\b", re.I),
    "menu": re.compile(r"\b(menu|navigation|hamburger|more)\b", re.I),
    "folder": re.compile(r"\b(folder|directory)\b", re.I),
    "save": re.compile(r"\b(save|commit)\b", re.I),
    "edit": re.compile(r"\b(edit|modify|rename)\b", re.I),
}


def _detect_icon_category(name: str, automation_id: str, class_name: str = "") -> str:
    haystack = f"{name} {automation_id} {class_name}".lower()
    for cat, pattern in _COMMON_ICON_PATTERNS.items():
        if pattern.search(haystack):
            return cat
    return ""


def extract_uia_elements(max_elements: int = 150) -> list[ScreenElement]:
    """Query active foreground window controls via Windows UI Automation."""
    ensure_desktop_and_dpi()
    elements: list[ScreenElement] = []
    if not _UIA or auto is None:
        return elements

    try:
        fg = auto.GetForegroundControl()
        if not fg or not fg.Exists(0, 0):
            fg = auto.GetRootControl()

        if not fg:
            return elements

        win_info = get_active_window_info()
        win_title = win_info.get("title", "")
        win_rect = win_info.get("rect", (0, 0, 1920, 1080))
        win_w = max(1, win_rect[2] - win_rect[0])
        win_h = max(1, win_rect[3] - win_rect[1])

        count = 0
        for ctrl, depth in auto.WalkControl(fg, maxDepth=8):
            if count >= max_elements:
                break

            try:
                rect = ctrl.BoundingRectangle
                if not rect:
                    continue
                w = rect.width()
                h = rect.height()
                if w <= 4 or h <= 4:
                    continue
                if rect.right <= 0 or rect.bottom <= 0:
                    continue

                name = (ctrl.Name or "").strip()
                auto_id = (ctrl.AutomationId or "").strip()
                class_name = (ctrl.ClassName or "").strip()
                ctrl_type_name = ctrl.ControlTypeName

                type_info = _UIA_TYPE_MAP.get(ctrl_type_name, ("unknown", "click", False))
                mapped_type, interaction, is_container = type_info

                # If container covers > 60% of the active window, ensure is_container=True
                if w >= win_w * 0.75 and h >= win_h * 0.75:
                    is_container = True

                # Categorize icons / buttons
                cat = _detect_icon_category(name, auto_id, class_name)
                if cat:
                    if mapped_type in ("image", "icon", "button", "unknown"):
                        mapped_type = "icon"
                        if not name:
                            name = f"{cat.capitalize()} Icon"

                # Position-based heuristic for top-right profile icons in browsers/apps:
                # If it's a button or image near top-right corner of the window with small size
                if not cat and mapped_type in ("icon", "button", "unknown"):
                    rel_x = rect.left - win_rect[0]
                    rel_y = rect.top - win_rect[1]
                    if rel_x >= win_w * 0.70 and rel_y <= 120 and 16 <= w <= 80 and 16 <= h <= 80:
                        # Potential profile or window button
                        if any(w_name in name.lower() for w_name in ("close", "min", "max")):
                            pass
                        elif not name or "profile" in name.lower() or "user" in name.lower() or "account" in name.lower():
                            cat = "profile"
                            mapped_type = "icon"
                            if not name:
                                name = "Profile Icon"

                # Filter out pure containers with no name and no interaction
                if is_container and not name and not auto_id:
                    continue

                is_enabled = True
                try:
                    is_enabled = bool(ctrl.IsEnabled)
                except Exception:
                    pass

                is_selected = False
                try:
                    tp = ctrl.GetTogglePattern()
                    if tp:
                        is_selected = (tp.ToggleState == 1)
                except Exception:
                    pass

                cx = int(rect.left + w / 2)
                cy = int(rect.top + h / 2)

                elem = ScreenElement(
                    text=name or auto_id or f"[{mapped_type}]",
                    element_type=mapped_type,
                    bbox=(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
                    center=(cx, cy),
                    confidence=1.0,
                    enabled=is_enabled,
                    selected=is_selected,
                    visible=True,
                    interaction=interaction,
                    automation_id=auto_id,
                    class_name=class_name,
                    window_title=win_title,
                    source="uia",
                    is_container=is_container,
                    category=cat,
                )
                elements.append(elem)
                count += 1
            except Exception:
                continue

    except Exception as e:
        print(f"[ScreenReader] UIA walk error: {e}")

    return elements


# ── Tesseract OCR Screen Reading ─────────────────────────────────────────────

def _get_tesseract_cmd() -> Optional[str]:
    """Find installed tesseract.exe path."""
    common = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for p in common:
        path = Path(p)
        if path.exists():
            return str(path)
    return None


def extract_ocr_elements(
    image: Any,
    min_confidence: int = 25,
    lang: str = "eng",
    offset: tuple[int, int] = (0, 0),
) -> list[ScreenElement]:
    """
    Run Tesseract OCR on a PIL image and return structured words and multi-word phrases.
    Groups horizontally adjacent words into phrases so buttons like 'Sign In' or 'Desktop Assistant' match cleanly.
    """
    elements: list[ScreenElement] = []
    if not _PYTESSERACT or image is None:
        return elements

    exe = _get_tesseract_cmd()
    if exe:
        pytesseract.pytesseract.tesseract_cmd = exe

    try:
        # Preprocessing: convert to grayscale for speed & crisp text edges
        gray = image.convert("L") if hasattr(image, "convert") else image
        data = pytesseract.image_to_data(
            gray,
            lang=lang,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 11",
        )

        n_boxes = len(data.get("text", []))
        ox, oy = offset

        # Map by (block_num, par_num, line_num) to group lines/phrases
        lines: dict[tuple[int, int, int], list[dict[str, Any]]] = {}

        for i in range(n_boxes):
            word = (data["text"][i] or "").strip()
            conf = float(data["conf"][i]) if "conf" in data else 0.0

            if not word or conf < min_confidence:
                continue

            x = int(data["left"][i]) + ox
            y = int(data["top"][i]) + oy
            w = int(data["width"][i])
            h = int(data["height"][i])

            if w <= 3 or h <= 4:
                continue

            cx = int(x + w / 2)
            cy = int(y + h / 2)

            elem_type = "text"
            interaction = "read"
            lower_word = word.lower()
            if any(lower_word == btn for btn in ("ok", "cancel", "save", "submit", "apply", "close", "yes", "no", "edit", "search", "profile", "account", "login", "view", "help", "file", "tools")):
                elem_type = "button"
                interaction = "click"

            cat = _detect_icon_category(word, "")

            elem = ScreenElement(
                text=word,
                element_type=elem_type,
                bbox=(x, y, x + w, y + h),
                center=(cx, cy),
                confidence=conf / 100.0,
                enabled=True,
                selected=False,
                visible=True,
                interaction=interaction,
                source="ocr",
                is_container=False,
                category=cat,
            )
            elements.append(elem)

            # Store for line phrase grouping
            b = data.get("block_num", [0])[i]
            p = data.get("par_num", [0])[i]
            l = data.get("line_num", [0])[i]
            key = (b, p, l)
            if key not in lines:
                lines[key] = []
            lines[key].append({"word": word, "x": x, "y": y, "w": w, "h": h, "conf": conf})

        # Add multi-word phrases for lines with > 1 word
        for key, w_list in lines.items():
            if len(w_list) > 1:
                # Sort horizontally by x
                w_list.sort(key=lambda item: item["x"])
                phrase_text = " ".join([item["word"] for item in w_list]).strip()
                min_x = min(item["x"] for item in w_list)
                min_y = min(item["y"] for item in w_list)
                max_r = max(item["x"] + item["w"] for item in w_list)
                max_b = max(item["y"] + item["h"] for item in w_list)
                avg_conf = sum(item["conf"] for item in w_list) / len(w_list)
                pcx = int((min_x + max_r) / 2)
                pcy = int((min_y + max_b) / 2)

                p_cat = _detect_icon_category(phrase_text, "")
                p_type = "button" if any(k in phrase_text.lower() for k in ("button", "save as", "sign in", "log in", "submit", "settings", "profile", "account")) else "text"

                phrase_elem = ScreenElement(
                    text=phrase_text,
                    element_type=p_type,
                    bbox=(min_x, min_y, max_r, max_b),
                    center=(pcx, pcy),
                    confidence=avg_conf / 100.0,
                    enabled=True,
                    selected=False,
                    visible=True,
                    interaction="click" if p_type == "button" else "read",
                    source="ocr",
                    is_container=False,
                    category=p_cat,
                )
                elements.append(phrase_elem)

    except Exception as e:
        print(f"[ScreenReader] OCR extraction error: {e}")

    return elements


# ── Visual Analysis for Unlabeled Icons ─────────────────────────────────────

def find_visual_icon(
    category: str,
    image: Any,
    window_rect: tuple[int, int, int, int],
) -> Optional[ScreenElement]:
    """
    Heuristic visual analysis for common unlabeled icons (Profile avatar, Settings, etc.)
    using geometry, position, and contour features.
    """
    if not _CV2 or image is None:
        return None

    cat = category.strip().lower()
    win_l, win_t, win_r, win_b = window_rect
    win_w = max(1, win_r - win_l)
    win_h = max(1, win_b - win_t)

    try:
        img_np = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

        # Profile / User Avatar: Look for circular avatar in top 150px of window, especially top-right
        if cat in ("profile", "account", "user", "avatar"):
            # Header crop
            crop_y2 = min(img_np.shape[0], win_t + 160)
            crop_y1 = max(0, win_t)
            crop_x1 = max(0, win_l)
            crop_x2 = min(img_np.shape[1], win_r)

            if crop_y2 > crop_y1 and crop_x2 > crop_x1:
                header_gray = gray[crop_y1:crop_y2, crop_x1:crop_x2]
                circles = cv2.HoughCircles(
                    header_gray,
                    cv2.HOUGH_GRADIENT,
                    dp=1.2,
                    minDist=20,
                    param1=50,
                    param2=28,
                    minRadius=10,
                    maxRadius=36,
                )
                if circles is not None:
                    circles = np.uint16(np.around(circles))
                    # Sort candidates: prefer top-right corner of the active window
                    scored_circles = []
                    for c in circles[0]:
                        cx_in_header, cy_in_header, r = c[0], c[1], c[2]
                        abs_cx = crop_x1 + cx_in_header
                        abs_cy = crop_y1 + cy_in_header
                        # Distance to top-right
                        dist_to_top_right = abs(abs_cx - (win_l + win_w * 0.90)) + abs_cy - win_t
                        scored_circles.append((dist_to_top_right, abs_cx, abs_cy, r))

                    scored_circles.sort(key=lambda item: item[0])
                    best = scored_circles[0]
                    _, cx, cy, r = best
                    return ScreenElement(
                        text="Profile Avatar Icon",
                        element_type="icon",
                        bbox=(cx - r, cy - r, cx + r, cy + r),
                        center=(cx, cy),
                        confidence=0.85,
                        enabled=True,
                        selected=False,
                        visible=True,
                        interaction="click",
                        source="vision",
                        category="profile",
                    )

    except Exception as e:
        print(f"[ScreenReader] Visual icon heuristic error: {e}")

    return None


def visual_locate_with_gemini(
    description: str,
    image: Any,
    window_rect: tuple[int, int, int, int],
) -> Optional[ScreenElement]:
    """
    Fallback Visual Analysis using Gemini Multimodal Vision when target has no text label.
    Takes active window screenshot and returns accurate pixel coordinates.
    """
    if image is None:
        return None

    try:
        from core import gemini
        from google.genai import types as gtypes

        win_l, win_t, win_r, win_b = window_rect
        w = max(1, win_r - win_l)
        h = max(1, win_b - win_t)

        # Crop to active window for higher precision
        crop_box = (max(0, win_l), max(0, win_t), min(image.width, win_r), min(image.height, win_b))
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            cropped = image
            crop_offset_x = 0
            crop_offset_y = 0
        else:
            cropped = image.crop(crop_box)
            crop_offset_x = crop_box[0]
            crop_offset_y = crop_box[1]

        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        img_bytes = buf.getvalue()

        prompt = (
            f"You are a computer vision assistant. Look at this window screenshot ({cropped.width}x{cropped.height} px). "
            f"Locate the UI element or icon: '{description}' (such as profile avatar, settings gear, search bar, close button). "
            f"Return ONLY a JSON object: {{\"found\": true, \"x\": <center_x_in_px>, \"y\": <center_y_in_px>, \"confidence\": <0.0-1.0>}} "
            f"If it is not visible on this screen, reply ONLY: {{\"found\": false}}"
        )

        response = gemini.call(
            [
                gtypes.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                prompt,
            ],
            tier=gemini.FAST,
            timeout_ms=18_000,
        )

        if response and response.text:
            cleaned = response.text.strip()
            # Extract JSON
            m = re.search(r"\{[^{}]*\}", cleaned)
            if m:
                data = json.loads(m.group(0))
                if data.get("found"):
                    abs_cx = int(data["x"]) + crop_offset_x
                    abs_cy = int(data["y"]) + crop_offset_y
                    conf = float(data.get("confidence", 0.85))
                    return ScreenElement(
                        text=f"{description} (Visual)",
                        element_type="icon",
                        bbox=(abs_cx - 15, abs_cy - 15, abs_cx + 15, abs_cy + 15),
                        center=(abs_cx, abs_cy),
                        confidence=conf,
                        enabled=True,
                        selected=False,
                        visible=True,
                        interaction="click",
                        source="vision",
                        category=_detect_icon_category(description, ""),
                    )
    except Exception as e:
        print(f"[ScreenReader] visual_locate_with_gemini error: {e}")

    return None


# ── Full Screen State Assembly ───────────────────────────────────────────────

def capture_screen_image() -> tuple[Optional[Any], tuple[int, int], tuple[int, int]]:
    """Capture current screen into a PIL Image safely with monitor offset support."""
    ensure_desktop_and_dpi()
    img = None
    size = (1920, 1080)
    offset = (0, 0)

    # 1. Primary: MSS
    try:
        import mss
        mss_cls = getattr(mss, "MSS", mss.mss)
        with mss_cls() as sct:
            monitors = sct.monitors
            # Determine monitor based on foreground window
            win_info = get_active_window_info()
            wl, wt, wr, wb = win_info.get("rect", (0, 0, 1920, 1080))
            wcx = (wl + wr) // 2
            wcy = (wt + wb) // 2

            target = monitors[1] if len(monitors) > 1 else monitors[0]
            for m in monitors[1:]:
                if m["left"] <= wcx <= m["left"] + m["width"] and m["top"] <= wcy <= m["top"] + m["height"]:
                    target = m
                    break

            shot = sct.grab(target)
            img = PIL.Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            size = (target["width"], target["height"])
            offset = (target["left"], target["top"])
            return img, size, offset
    except Exception as e:
        pass

    # 2. Secondary: PIL ImageGrab
    try:
        if _PIL and hasattr(PIL.ImageGrab, "grab"):
            img = PIL.ImageGrab.grab(all_screens=True)
            return img, img.size, (0, 0)
    except Exception:
        pass

    # 3. Tertiary: PyAutoGUI screenshot
    try:
        if _PYAUTOGUI and pyautogui:
            img = pyautogui.screenshot()
            return img, img.size, (0, 0)
    except Exception:
        pass

    return None, size, offset


def capture_screen_state(
    include_ocr: bool = True,
    ocr_min_confidence: int = 25,
) -> ScreenState:
    """Capture comprehensive structured screen state combining UIA and OCR."""
    t0 = time.time()
    ensure_desktop_and_dpi()
    win_info = get_active_window_info()
    img, screen_size, offset = capture_screen_image()

    elements: list[ScreenElement] = []

    # 1. UI Automation controls
    uia_elems = extract_uia_elements(max_elements=140)
    elements.extend(uia_elems)

    # 2. OCR words & phrases
    ocr_raw_text = ""
    if include_ocr and img is not None:
        ocr_elems = extract_ocr_elements(img, min_confidence=ocr_min_confidence, offset=offset)
        ocr_words = [e.text for e in ocr_elems]
        ocr_raw_text = " ".join(ocr_words)

        # Merge OCR elements that aren't already covered by UIA interactive elements
        interactive_uia = [ue for ue in uia_elems if not ue.is_container]
        for oe in ocr_elems:
            ox, oy = oe.center
            covered = False
            for ue in interactive_uia:
                l, t, r, b = ue.bbox
                if l <= ox <= r and t <= oy <= b and ue.text.strip():
                    covered = True
                    break
            if not covered:
                elements.append(oe)

    if not ocr_raw_text:
        ocr_raw_text = " ".join([e.text for e in elements if e.text and not e.text.startswith("[")])

    return ScreenState(
        timestamp=t0,
        window_title=win_info.get("title", "Desktop"),
        process_name=win_info.get("process_name", ""),
        hwnd=win_info.get("hwnd", 0),
        window_rect=win_info.get("rect", (0, 0, screen_size[0], screen_size[1])),
        screen_size=screen_size,
        elements=elements,
        raw_text=ocr_raw_text,
        screenshot=img,
    )


# ── Target Localization & Disambiguation Engine ──────────────────────────────

def _score_element_match(target: str, elem: ScreenElement, win_rect: tuple[int, int, int, int]) -> float:
    """Calculate match confidence score between query and candidate ScreenElement."""
    t_clean = target.strip().lower()
    e_text = elem.text.strip().lower()
    if not t_clean or not e_text:
        return 0.0

    score = 0.0
    match_found = False

    # Match types with conciseness weighting
    word_count = len(e_text.split())
    if e_text == t_clean:
        score += 100.0
        match_found = True
    elif re.search(rf"\b{re.escape(t_clean)}\b", e_text, re.I):
        if word_count <= 3:
            score += 90.0
        else:
            # Word appears inside a long sentence or code snippet
            score += 35.0
        match_found = True
    elif t_clean in e_text and len(t_clean) >= 3:
        if word_count <= 2:
            score += 60.0
            match_found = True
        elif len(e_text) <= len(t_clean) * 2.5:
            score += 30.0
            match_found = True
    elif difflib.SequenceMatcher(None, t_clean, e_text).ratio() >= 0.78:
        score += 55.0 * difflib.SequenceMatcher(None, t_clean, e_text).ratio()
        match_found = True

    # Category matching (e.g. user asks for 'profile' and elem is categorized as 'profile')
    t_cat = _detect_icon_category(target, "")
    if t_cat and elem.category == t_cat:
        score += 80.0
        match_found = True

    if not match_found:
        return 0.0

    # Source bonus: Windows UI Automation is native accessibility and strictly preferred over OCR
    if elem.source == "uia":
        score += 65.0
    elif elem.source == "ocr":
        # Reject OCR text that is source code, string literals, or syntax in editor buffers
        code_syntax = ('"', "'", '“', '”', '`', '{', '}', '[', ']', '(', ')', ';', 'def ', 'import ', 'class ', 'return ', 'if ', '->', ':=', '==', '!=', '.py', '.js', '//', '/*')
        has_code = any(c in elem.text for c in code_syntax)
        target_has_code = any(c in target for c in ('"', "'", '`', '{', '}', '(', ')'))
        if has_code and not target_has_code:
            return 0.0

    # Type bonuses — INTERACTIVE LEAF CONTROLS WIN!
    if elem.element_type in ("button", "icon", "menu_item", "tab", "link"):
        score += 45.0
    elif elem.element_type in ("edit", "checkbox", "radio_button"):
        score += 35.0
    elif elem.element_type == "text":
        score += 15.0
    elif elem.is_container or elem.element_type in ("pane", "group", "window"):
        # Heavily penalize giant window-sized layout panes!
        score -= 90.0

    # Spatial sanity check: element inside active window bounds
    wl, wt, wr, wb = win_rect
    cx, cy = elem.center
    if wl <= cx <= wr and wt <= cy <= wb:
        score += 25.0
    else:
        score -= 30.0

    # Status bar filter (bottom 55px of active window)
    if cy >= wb - 55:
        if t_clean in ("settings", "search", "profile", "file", "edit", "view"):
            # Never match status bar items when searching for primary navigation controls
            return 0.0
        score -= 90.0

    # Top header / navigation toolbar bonus (top 110px of window)
    if wt <= cy <= wt + 110:
        score += 40.0

    # Left activity / sidebar bonus (left 75px of window)
    if wl <= cx <= wl + 75:
        score += 40.0

    # Profile icon spatial bonus: top-right corner of active window
    if t_cat == "profile":
        win_w = max(1, wr - wl)
        if cx >= wl + win_w * 0.65 and cy <= wt + 140:
            score += 35.0

    return score


def locate_target(
    description: str,
    screen_state: Optional[ScreenState] = None,
    index: int = 1,
    min_confidence: float = 40.0,
) -> dict[str, Any]:
    """
    Locates the requested target following the strict 4-tier priority order:
      1. Windows UI Automation (interactive controls) - strictly preferred
      2. Visual analysis for unlabeled icons & symbols (profile, settings, search)
      3. Local Tesseract OCR (filtered for UI controls, rejecting code/status bar)
      4. Multimodal Vision Fallback for completely unlabeled controls

    Returns dict with:
      'found': bool
      'element': ScreenElement or None
      'center': (x, y)
      'confidence': float
      'method': 'uia' | 'ocr' | 'vision' | 'none'
      'candidates_count': int
      'clarification': str (if ambiguous or not found)
    """
    desc = str(description or "").strip()
    if not desc:
        return {
            "found": False,
            "element": None,
            "center": (0, 0),
            "confidence": 0.0,
            "method": "none",
            "candidates_count": 0,
            "clarification": "No target description provided to locate.",
        }

    if screen_state is None:
        screen_state = capture_screen_state(include_ocr=True)

    win_info = {
        "title": screen_state.window_title,
        "rect": screen_state.window_rect,
    }
    win_rect = screen_state.window_rect
    elems = screen_state.elements

    # Check for ordinal in description (e.g. 'third matching result' or '2nd profile')
    ordinal_match = re.search(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b", desc, re.I)
    if ordinal_match:
        ord_word = ordinal_match.group(1).lower()
        ord_map = {
            "first": 1, "1st": 1,
            "second": 2, "2nd": 2,
            "third": 3, "3rd": 3,
            "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5,
        }
        index = ord_map.get(ord_word, index)
        desc = re.sub(rf"\b{re.escape(ordinal_match.group(1))}\b", "", desc, flags=re.I).strip()
        desc = re.sub(r"\b(matching\s+result|result|item|button)\b", "", desc, flags=re.I).strip()
        if not desc:
            desc = description

    # =========================================================================
    # PRIORITY 1: Windows UI Automation (interactive controls)
    # =========================================================================
    uia_scored: list[tuple[float, ScreenElement]] = []
    for elem in elems:
        if elem.source != "uia":
            continue
        w = elem.bbox[2] - elem.bbox[0]
        h = elem.bbox[3] - elem.bbox[1]
        win_w = max(1, win_rect[2] - win_rect[0])
        win_h = max(1, win_rect[3] - win_rect[1])
        if (elem.is_container or elem.element_type in ("pane", "window")) and (w >= win_w * 0.70 or h >= win_h * 0.70):
            continue

        s = _score_element_match(desc, elem, win_rect)
        if s >= min_confidence:
            uia_scored.append((s, elem))

    if uia_scored:
        uia_scored.sort(key=lambda x: x[0], reverse=True)
        idx = max(0, min(index - 1, len(uia_scored) - 1))
        best_score, best_elem = uia_scored[idx]
        if best_score >= 60.0:
            print(f"[ScreenReader] [TARGET] Target '{description}' matched [UIA] '{best_elem.text}' "
                  f"at {best_elem.center} (score: {best_score:.1f}, candidate {idx+1}/{len(uia_scored)})")
            return {
                "found": True,
                "element": best_elem,
                "center": best_elem.center,
                "confidence": min(1.0, best_score / 150.0),
                "method": "uia",
                "candidates_count": len(uia_scored),
                "clarification": "",
            }

    # =========================================================================
    # PRIORITY 2: Visual Icon Analysis for unlabeled icons (profile, settings, search)
    # =========================================================================
    cat = _detect_icon_category(desc, "")
    if cat and screen_state.screenshot is not None:
        vis_elem = find_visual_icon(cat, screen_state.screenshot, win_rect)
        if vis_elem:
            print(f"[ScreenReader] [TARGET] Target '{description}' found via local visual analysis at {vis_elem.center}")
            return {
                "found": True,
                "element": vis_elem,
                "center": vis_elem.center,
                "confidence": vis_elem.confidence,
                "method": "vision",
                "candidates_count": 1,
                "clarification": "",
            }

    # =========================================================================
    # PRIORITY 3: Local Tesseract OCR (filtered for genuine UI controls)
    # =========================================================================
    ocr_scored: list[tuple[float, ScreenElement]] = []
    for elem in elems:
        if elem.source != "ocr":
            continue
        s = _score_element_match(desc, elem, win_rect)
        if s >= max(min_confidence, 65.0):
            ocr_scored.append((s, elem))

    if ocr_scored:
        ocr_scored.sort(key=lambda x: x[0], reverse=True)
        idx = max(0, min(index - 1, len(ocr_scored) - 1))
        best_score, best_elem = ocr_scored[idx]
        print(f"[ScreenReader] [TARGET] Target '{description}' matched [OCR] '{best_elem.text}' "
              f"at {best_elem.center} (score: {best_score:.1f}, candidate {idx+1}/{len(ocr_scored)})")
        return {
            "found": True,
            "element": best_elem,
            "center": best_elem.center,
            "confidence": min(1.0, best_score / 150.0),
            "method": "ocr",
            "candidates_count": len(ocr_scored),
            "clarification": "",
        }

    # =========================================================================
    # PRIORITY 4: Multimodal Vision Fallback for completely unlabeled controls
    # =========================================================================
    if cat and screen_state.screenshot is not None:
        gemini_vis = visual_locate_with_gemini(desc, screen_state.screenshot, win_rect)
        if gemini_vis:
            print(f"[ScreenReader] [TARGET] Target '{description}' found via Gemini Vision at {gemini_vis.center}")
            return {
                "found": True,
                "element": gemini_vis,
                "center": gemini_vis.center,
                "confidence": gemini_vis.confidence,
                "method": "vision",
                "candidates_count": 1,
                "clarification": "",
            }

    # Not found: construct short clarification with visible options
    available_btns = [e.text for e in elems if e.element_type in ("button", "icon") and not e.is_container][:6]
    opts = f" Available options: {', '.join(available_btns)}" if available_btns else ""
    clarification = f"Could not find '{description}' in '{screen_state.window_title}'.{opts}"
    print(f"[ScreenReader] [WARN] {clarification}")

    return {
        "found": False,
        "element": None,
        "center": (0, 0),
        "confidence": 0.0,
        "method": "none",
        "candidates_count": 0,
        "clarification": clarification,
    }


def find_element_by_text(
    target_text: str,
    elements: list[ScreenElement],
    exact_first: bool = True,
    fuzzy_threshold: float = 0.65,
) -> Optional[ScreenElement]:
    """Find the best matching screen element by text label using accuracy rules."""
    win_info = get_active_window_info()
    win_rect = win_info.get("rect", (0, 0, 1920, 1080))
    scored = []
    for elem in elements:
        s = _score_element_match(target_text, elem, win_rect)
        if s > 0:
            scored.append((s, elem))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return scored[0][1]
    return None


def find_all_matching_elements(
    target_text: str,
    elements: list[ScreenElement],
) -> list[ScreenElement]:
    """Return all matching elements (used for disambiguation)."""
    win_info = get_active_window_info()
    win_rect = win_info.get("rect", (0, 0, 1920, 1080))
    scored = []
    for elem in elements:
        s = _score_element_match(target_text, elem, win_rect)
        if s >= 40.0:
            scored.append((s, elem))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored]


# ── Observe-Plan-Act-Verify State Verification ───────────────────────────────

def verify_action_effect(
    before_state: ScreenState,
    after_state: ScreenState,
    clicked_center: Optional[tuple[int, int]] = None,
) -> tuple[bool, str]:
    """
    Observe updated screen and verify whether the requested action succeeded.
    Checks window focus, title change, dialog appearance, element changes, and pixel differences.
    """
    # 1. Window or application change
    if after_state.hwnd != before_state.hwnd and after_state.hwnd != 0:
        return True, f"Active window changed to '{after_state.window_title}' ({after_state.process_name})."

    if after_state.window_title != before_state.window_title and after_state.window_title:
        return True, f"Window updated to '{after_state.window_title}'."

    # 2. Check if a new menu, popup, or dialog opened
    before_texts = {e.text.strip().lower() for e in before_state.elements if e.text}
    after_texts = {e.text.strip().lower() for e in after_state.elements if e.text}
    new_texts = after_texts - before_texts

    if new_texts:
        new_sample = list(new_texts)[:4]
        return True, f"UI updated: new options appeared ({', '.join(new_sample)})."

    # 3. Check pixel difference if screenshots are available
    if before_state.screenshot is not None and after_state.screenshot is not None and _PIL:
        try:
            diff = PIL.ImageChops.difference(
                before_state.screenshot.convert("RGB"),
                after_state.screenshot.convert("RGB"),
            )
            bbox = diff.getbbox()
            if bbox:
                diff_w = bbox[2] - bbox[0]
                diff_h = bbox[3] - bbox[1]
                if diff_w > 10 and diff_h > 10:
                    return True, f"Screen content updated in region {bbox}."
        except Exception:
            pass

    # No discernible change
    return False, "Action executed, but no visible change was detected on screen."


# ── Screen Question-Answering ────────────────────────────────────────────────

def answer_screen_question(
    query: str,
    screen_state: Optional[ScreenState] = None,
) -> str:
    """Analyze the screen and answer a user question clearly without performing clicks."""
    if screen_state is None:
        screen_state = capture_screen_state(include_ocr=True)

    q = (query or "").lower().strip()
    win_title = screen_state.window_title
    proc = screen_state.process_name or "Unknown application"
    elems = screen_state.elements

    buttons = [e for e in elems if e.element_type in ("button", "icon") and not e.is_container]
    inputs = [e for e in elems if e.element_type == "edit"]
    tabs = [e for e in elems if e.element_type == "tab"]
    links = [e for e in elems if e.element_type == "link"]

    if any(k in q for k in ("what is on", "read the current", "what's on my screen", "explain the screen", "what do you see", "window contains")):
        summary_lines = [
            f"Active Window: '{win_title}' ({proc}).",
            f"Detected {len(elems)} interactive elements across the screen.",
        ]
        if buttons:
            btn_labels = [b.text for b in buttons if b.text and not b.text.startswith("[")][:8]
            if btn_labels:
                summary_lines.append(f"Buttons: {', '.join(btn_labels)}.")
        if inputs:
            inp_labels = [i.text for i in inputs if i.text and not i.text.startswith("[")][:4]
            summary_lines.append(f"Text Input Fields: {', '.join(inp_labels) if inp_labels else str(len(inputs)) + ' fields'}.")
        if tabs:
            tab_labels = [t.text for t in tabs if t.text][:6]
            if tab_labels:
                summary_lines.append(f"Tabs: {', '.join(tab_labels)}.")
        if screen_state.raw_text:
            preview = screen_state.raw_text[:280].replace("\n", " ").strip()
            summary_lines.append(f"Visible Text Preview: \"{preview}...\"")
        return "\n".join(summary_lines)

    if any(k in q for k in ("what buttons", "clickable", "options available", "which buttons")):
        if not buttons:
            return f"In the active window '{win_title}', I did not detect labeled buttons. Text present: {screen_state.raw_text[:160]}"
        items = []
        for b in buttons[:12]:
            label = b.text
            if label and not label.startswith("["):
                items.append(f"• {label} (at {b.center[0]}, {b.center[1]})")
        return f"Buttons available in '{win_title}':\n" + "\n".join(items)

    m_find = re.search(r"find\s+(the\s+)?([a-zA-Z0-9_\s]+)", q)
    if m_find and ("icon" in q or "button" in q or "find" in q):
        target = m_find.group(2).replace("icon", "").replace("button", "").strip()
        res = locate_target(target, screen_state=screen_state)
        if res["found"]:
            elem = res["element"]
            return (
                f"Found '{elem.text}' ({elem.element_type}) in '{win_title}' at coordinates "
                f"({res['center'][0]}, {res['center'][1]}), bounding box {elem.bbox} via {res['method'].upper()}."
            )
        else:
            return res.get("clarification") or f"Could not locate '{target}' in '{win_title}'."

    res = locate_target(query, screen_state=screen_state)
    if res["found"]:
        elem = res["element"]
        return f"Found '{elem.text}' at position ({res['center'][0]}, {res['center'][1]}) in '{win_title}'."

    return (
        f"Active window is '{win_title}' ({proc}). "
        f"Visible content summary: {screen_state.raw_text[:350]}."
    )
