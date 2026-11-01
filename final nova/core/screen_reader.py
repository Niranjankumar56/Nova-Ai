"""
core/screen_reader.py — Advanced Screen Reading, UI Element Detection,
Visual Understanding & Context-Aware Grounding for NOVA.

Provides:
  • Hybrid 4-Tier Architecture:
      1. Windows UI Automation (native accessibility tree & states)
      2. Adaptive Local OCR (Tesseract with CLAHE, upscaling, unsharp mask, Otsu)
      3. Local Computer Vision (contour & shape analysis for icons, toggles, controls)
      4. Gemini Multimodal Vision fallback for stylized/unlabeled scenes
  • Full UI Element Taxonomy:
      Buttons, Icons, Edit fields, Search bars, Checkboxes, Radio buttons, Dropdowns,
      Tabs, Menus, Toggles, Dialogs, Window controls, Scrollbars, Links.
  • Context-Aware Disambiguation:
      Container/section matching, nearby text association, spatial & ordinal selectors,
      and safe ambiguity rejection to prevent blind clicks.
  • Observe-Plan-Act-Verify post-action state verification.
  • Natural voice QA answering for screen exploration.
  • Windows DPI awareness and multi-monitor offset handling.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import difflib
import io
import json
import math
import os
import platform
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, List, Dict

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def ensure_desktop_and_dpi() -> None:
    """Ensure current thread is attached to interactive desktop and DPI aware."""
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
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

ensure_desktop_and_dpi()

# Redirect comtypes typelib cache to avoid Windows permission/cache conflicts
_temp_gen = Path(tempfile.gettempdir()) / "nova_comtypes_gen"
try:
    _temp_gen.mkdir(parents=True, exist_ok=True)
    import comtypes.client
    comtypes.client.gen_dir = str(_temp_gen)
    import comtypes.gen
    if str(_temp_gen) not in comtypes.gen.__path__:
        comtypes.gen.__path__.insert(0, str(_temp_gen))
except Exception:
    pass

try:
    import uiautomation as auto
    _UIA = True
except Exception:
    auto = None
    _UIA = False

try:
    import PIL.Image
    import PIL.ImageGrab
    import PIL.ImageOps
    import PIL.ImageChops
    import PIL.ImageFilter
    _PIL = True
except ImportError:
    _PIL = False

try:
    import pytesseract
    _PYTESSERACT = True
except ImportError:
    pytesseract = None
    _PYTESSERACT = False

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    pyautogui = None
    _PYAUTOGUI = False

try:
    import cv2
    import numpy as np
    _CV2 = True
except ImportError:
    cv2 = None
    np = None
    _CV2 = False


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class ScreenElement:
    text: str
    element_type: str               # button, icon, edit, search_bar, checkbox, radio_button, dropdown, tab, menu_item, list_item, scrollbar, toggle, heading, text, error_message, status_text, link, dialog, window_control
    bbox: tuple[int, int, int, int] # (left, top, right, bottom)
    center: tuple[int, int]         # (cx, cy)
    confidence: float = 1.0
    enabled: bool = True
    selected: bool = False
    visible: bool = True
    interaction: str = "click"      # click, double_click, right_click, type, toggle, select, read, scroll
    automation_id: str = ""
    class_name: str = ""
    window_title: str = ""
    source: str = "uia"             # uia, ocr, vision, gemini
    is_container: bool = False
    category: str = ""              # profile, settings, search, close, minimize, maximize, save, edit, chevron, menu, etc.
    section: str = "main_content"   # title_bar, menu_ribbon, sidebar_left, sidebar_right, main_content, status_bar, dialog
    parent_name: str = ""
    state_info: dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return max(0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> int:
        return max(0, self.bbox[3] - self.bbox[1])

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        d["center"] = list(self.center)
        d["width"] = self.width
        d["height"] = self.height
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
    sections: dict[str, list[ScreenElement]] = field(default_factory=dict)
    dialog_info: Optional[dict[str, Any]] = None
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
            "dialog": self.dialog_info,
        }


# ============================================================================
# ACTIVE WINDOW & CONTEXT DISCOVERY
# ============================================================================

def get_active_window_info() -> dict[str, Any]:
    """Return active foreground window details (title, process, HWND, bounding rect, is_dialog)."""
    ensure_desktop_and_dpi()
    info = {
        "title": "Desktop",
        "process_name": "",
        "hwnd": 0,
        "rect": (0, 0, 1920, 1080),
        "is_dialog": False,
        "is_maximized": False,
    }
    if platform.system() != "Windows":
        return info

    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return info
        info["hwnd"] = int(hwnd)

        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            info["title"] = buf.value.strip() or "Untitled Window"

        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            info["rect"] = (rect.left, rect.top, rect.right, rect.bottom)

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            try:
                import psutil
                proc = psutil.Process(pid.value)
                info["process_name"] = proc.name()
            except Exception:
                pass

        style = user32.GetWindowLongW(hwnd, -16)
        WS_DLGFRAME = 0x00400000
        WS_POPUP = 0x80000000
        WS_MAXIMIZE = 0x01000000
        if style & WS_DLGFRAME or (style & WS_POPUP and length > 0):
            info["is_dialog"] = True
        if style & WS_MAXIMIZE:
            info["is_maximized"] = True

    except Exception as e:
        print(f"[ScreenReader] get_active_window_info error: {e}")

    return info


def classify_screen_section(
    cx: int,
    cy: int,
    win_rect: tuple[int, int, int, int],
    dialog_rect: Optional[tuple[int, int, int, int]] = None,
) -> str:
    """Classify the spatial section of an element within a window."""
    wl, wt, wr, wb = win_rect
    win_w = max(1, wr - wl)
    win_h = max(1, wb - wt)

    if dialog_rect:
        dl, dt, dr, db = dialog_rect
        if dl <= cx <= dr and dt <= cy <= db:
            return "dialog"

    rel_y = cy - wt
    rel_x = cx - wl

    if rel_y <= 42:
        return "title_bar"
    if 42 < rel_y <= 110:
        return "menu_ribbon"
    if rel_y >= win_h - 45:
        return "status_bar"

    sidebar_w = min(280, max(160, int(win_w * 0.24)))
    if rel_x <= sidebar_w:
        return "sidebar_left"
    if rel_x >= win_w - sidebar_w:
        return "sidebar_right"

    return "main_content"


# ============================================================================
# TIER 1: WINDOWS UI AUTOMATION (UIA)
# ============================================================================

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
    "SliderControl": ("slider", "drag", False),
    "ImageControl": ("icon", "click", False),
    "TextControl": ("text", "read", False),
    "GroupControl": ("group", "read", True),
    "HeaderControl": ("heading", "read", True),
    "HeaderItemControl": ("heading", "read", False),
    "WindowControl": ("dialog", "read", True),
    "PaneControl": ("pane", "read", True),
    "ToolBarControl": ("toolbar", "read", True),
    "MenuBarControl": ("menubar", "read", True),
}

_COMMON_ICON_PATTERNS = {
    "profile": re.compile(r"\b(profile|account|user|avatar|login|sign[ -]?in|me)\b", re.I),
    "settings": re.compile(r"\b(settings|preferences|gear|config|options|setup)\b", re.I),
    "search": re.compile(r"\b(search|find|lookup|query)\b", re.I),
    "close": re.compile(r"\b(close|exit|quit|cancel)\b", re.I),
    "minimize": re.compile(r"\b(minimize|min)\b", re.I),
    "maximize": re.compile(r"\b(maximize|restore|max)\b", re.I),
    "back": re.compile(r"\b(back|previous|backward)\b", re.I),
    "forward": re.compile(r"\b(forward|next)\b", re.I),
    "refresh": re.compile(r"\b(refresh|reload)\b", re.I),
    "download": re.compile(r"\b(download|downloads)\b", re.I),
    "menu": re.compile(r"\b(menu|navigation|hamburger|more)\b", re.I),
    "save": re.compile(r"\b(save|commit)\b", re.I),
    "edit": re.compile(r"\b(edit|modify|rename|pencil)\b", re.I),
    "delete": re.compile(r"\b(delete|trash|remove|bin)\b", re.I),
    "add": re.compile(r"\b(add|plus|new|create)\b", re.I),
    "dropdown": re.compile(r"\b(dropdown|combo|select|chevron|expand)\b", re.I),
}


def _detect_icon_category(name: str, automation_id: str, class_name: str = "") -> str:
    haystack = f"{name} {automation_id} {class_name}".lower()
    for cat, pattern in _COMMON_ICON_PATTERNS.items():
        if pattern.search(haystack):
            return cat
    return ""


def safe_get_foreground_control() -> Optional[Any]:
    """Retrieve foreground UIA control with robust fallback."""
    if not _UIA or auto is None:
        return None
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl and ctrl.Exists(0, 0):
                return ctrl
    except Exception:
        pass
    try:
        fg = auto.GetForegroundControl()
        if fg and fg.Exists(0, 0):
            return fg
    except Exception:
        pass
    try:
        root = auto.GetRootControl()
        if root and root.Exists(0, 0):
            return root
    except Exception:
        pass
    return None


def extract_uia_elements(max_elements: int = 160) -> list[ScreenElement]:
    """Query active foreground window controls via Windows UI Automation."""
    ensure_desktop_and_dpi()
    elements: list[ScreenElement] = []
    if not _UIA or auto is None:
        return elements

    fg = safe_get_foreground_control()
    if not fg:
        return elements

    win_info = get_active_window_info()
    win_title = win_info.get("title", "")
    win_rect = win_info.get("rect", (0, 0, 1920, 1080))
    win_w = max(1, win_rect[2] - win_rect[0])
    win_h = max(1, win_rect[3] - win_rect[1])

    try:
        count = 0
        for ctrl, depth in auto.WalkControl(fg, maxDepth=9):
            if count >= max_elements:
                break
            try:
                rect = ctrl.BoundingRectangle
                if not rect:
                    continue
                w = rect.width()
                h = rect.height()
                if w <= 4 or h <= 4 or rect.right <= 0 or rect.bottom <= 0:
                    continue

                name = (ctrl.Name or "").strip()
                auto_id = (ctrl.AutomationId or "").strip()
                class_name = (ctrl.ClassName or "").strip()
                ctrl_type = ctrl.ControlTypeName

                mapped_type, interaction, is_container = _UIA_TYPE_MAP.get(
                    ctrl_type, ("unknown", "click", False)
                )

                if w >= win_w * 0.75 and h >= win_h * 0.75:
                    is_container = True

                cat = _detect_icon_category(name, auto_id, class_name)
                if cat:
                    if mapped_type in ("image", "icon", "button", "unknown"):
                        mapped_type = "icon"
                        if not name:
                            name = f"{cat.capitalize()} Icon"

                # Profile icon heuristic in header/top-right
                if not cat and mapped_type in ("icon", "button", "unknown"):
                    rel_x = rect.left - win_rect[0]
                    rel_y = rect.top - win_rect[1]
                    if rel_x >= win_w * 0.70 and rel_y <= 130 and 16 <= w <= 80 and 16 <= h <= 80:
                        if not any(w_name in name.lower() for w_name in ("close", "min", "max")):
                            cat = "profile"
                            mapped_type = "icon"
                            if not name:
                                name = "Profile Icon"

                # Search bar classification
                if mapped_type == "edit" and ("search" in name.lower() or "search" in auto_id.lower()):
                    mapped_type = "search_bar"

                if is_container and not name and not auto_id:
                    continue

                is_enabled = True
                try:
                    is_enabled = bool(ctrl.IsEnabled)
                except Exception:
                    pass

                state_info: dict[str, Any] = {"enabled": is_enabled}
                is_selected = False
                try:
                    tp = ctrl.GetTogglePattern()
                    if tp:
                        is_selected = (tp.ToggleState == 1)
                        state_info["checked"] = is_selected
                except Exception:
                    pass

                try:
                    vp = ctrl.GetValuePattern()
                    if vp:
                        state_info["value"] = vp.Value
                except Exception:
                    pass

                try:
                    exp = ctrl.GetExpandCollapsePattern()
                    if exp:
                        state_info["expanded"] = (exp.ExpandCollapseState == 1)
                except Exception:
                    pass

                cx = int(rect.left + w / 2)
                cy = int(rect.top + h / 2)
                section = classify_screen_section(cx, cy, win_rect)

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
                    section=section,
                    state_info=state_info,
                )
                elements.append(elem)
                count += 1
            except Exception:
                continue

    except Exception as e:
        print(f"[ScreenReader] UIA walk error: {e}")

    return elements


# ============================================================================
# TIER 2: ADAPTIVE LOCAL OCR PREPROCESSING & EXTRACTION
# ============================================================================

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


def preprocess_image_for_ocr(
    image: Any,
    region: Optional[tuple[int, int, int, int]] = None,
    upscale_small: bool = True,
) -> tuple[Any, dict[str, Any]]:
    """
    Adaptive Computer Vision Preprocessing for OCR:
      • Analyzes mean brightness and contrast standard deviation.
      • Upscales small regions / text (<15px) 2x via bicubic interpolation.
      • Applies CLAHE (Contrast Limited Adaptive Histogram Equalization) for dark-mode/low-contrast.
      • Applies Bilateral Filter to suppress noise without blurring font edges.
      • Applies Unsharp Masking for crisp glyph definition.
    """
    stats: dict[str, Any] = {
        "clahe_applied": False,
        "upscaled": False,
        "unsharp_applied": False,
        "contrast": 0.0,
        "mean": 0.0,
    }
    if not _CV2 or image is None:
        return image, stats

    try:
        # Convert PIL to CV2 grayscale
        if region:
            w = region[2] - region[0]
            h = region[3] - region[1]
            if w > 0 and h > 0:
                image = image.crop(region)

        img_np = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

        h, w = gray.shape[:2]
        mean_b = float(np.mean(gray))
        contrast = float(np.std(gray))
        stats["mean"] = mean_b
        stats["contrast"] = contrast

        # 1. Bicubic upscaling for small text or low resolution
        if upscale_small and (h < 50 or w < 80 or (h <= 1080 and contrast < 35)):
            if h < 600 and w < 1200:
                gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
                stats["upscaled"] = True

        # 2. CLAHE for dark mode or low-contrast
        if contrast < 42 or mean_b < 90:
            clahe = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
            stats["clahe_applied"] = True

        # 3. Bilateral filter for noise reduction
        gray = cv2.bilateralFilter(gray, d=5, sigmaColor=35, sigmaSpace=35)

        # 4. Unsharp Masking for crisp font edges
        blurred = cv2.GaussianBlur(gray, (0, 0), 1.8)
        gray = cv2.addWeighted(gray, 1.45, blurred, -0.45, 0)
        stats["unsharp_applied"] = True

        res_pil = PIL.Image.fromarray(gray)
        return res_pil, stats

    except Exception as e:
        print(f"[ScreenReader] Preprocessing error: {e}")
        return image, stats


def compute_iou(b1: tuple[int, int, int, int], b2: tuple[int, int, int, int]) -> float:
    """Compute Intersection-over-Union between two bounding boxes."""
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


def sort_elements_reading_order(elements: list[ScreenElement]) -> list[ScreenElement]:
    """Sort elements in natural spatial reading order (top-to-bottom, left-to-right)."""
    if not elements:
        return []
    sorted_y = sorted(elements, key=lambda e: (e.bbox[1], e.bbox[0]))
    bands: list[list[ScreenElement]] = []
    current_band: list[ScreenElement] = []
    current_y: Optional[int] = None
    line_threshold = 12

    for e in sorted_y:
        y = e.bbox[1]
        if current_y is None or abs(y - current_y) <= line_threshold:
            current_band.append(e)
            current_y = y if current_y is None else min(current_y, y)
        else:
            bands.append(sorted(current_band, key=lambda item: item.bbox[0]))
            current_band = [e]
            current_y = y
    if current_band:
        bands.append(sorted(current_band, key=lambda item: item.bbox[0]))

    result: list[ScreenElement] = []
    for band in bands:
        result.extend(band)
    return result


def _classify_ocr_text_role(text: str, h: int, win_rect: tuple[int, int, int, int], cy: int) -> tuple[str, str]:
    """Classify OCR text into element_type and interaction."""
    t_lower = text.strip().lower()
    
    # 1. Error messages
    error_keywords = ("error", "failed", "warning", "exception", "could not", "access denied", "timed out", "invalid")
    if any(k in t_lower for k in error_keywords) and len(t_lower) > 6:
        return "error_message", "read"

    # 2. Buttons
    btn_words = (
        "ok", "cancel", "save", "save as", "submit", "apply", "close", "yes", "no",
        "edit", "search", "profile", "account", "login", "sign in", "view", "help",
        "file", "tools", "next", "back", "continue", "finish", "done", "delete",
        "open", "browse", "select", "download", "install", "retry", "discard", "confirm"
    )
    if t_lower in btn_words or any(t_lower == f"[{w}]" for w in btn_words):
        return "button", "click"
    if any(t_lower.startswith(w + " ") or t_lower.endswith(" " + w) for w in ("save", "submit", "click", "download", "sign in")):
        return "button", "click"

    # 3. Headings (large font height or section title)
    if h >= 22 or (h >= 17 and any(t_lower.startswith(s) for s in ("settings", "profile", "general", "advanced", "display", "privacy"))):
        return "heading", "read"

    # 4. Links
    if t_lower.startswith(("http", "www.", "https://")) or t_lower in ("learn more", "visit", "click here"):
        return "link", "click"

    # 5. Status bar
    wb = win_rect[3]
    if cy >= wb - 45:
        return "status_text", "read"

    return "text", "read"


def extract_ocr_elements(
    image: Any,
    min_confidence: int = 25,
    lang: str = "eng",
    offset: tuple[int, int] = (0, 0),
    win_rect: tuple[int, int, int, int] = (0, 0, 1920, 1080),
) -> list[ScreenElement]:
    """
    Run Adaptive Tesseract OCR on a PIL image and return structured words and multi-word phrases.
    Groups horizontally adjacent words into phrases so buttons like 'Sign In' or 'Display Settings' match cleanly.
    """
    elements: list[ScreenElement] = []
    if not _PYTESSERACT or image is None:
        return elements

    exe = _get_tesseract_cmd()
    if exe:
        pytesseract.pytesseract.tesseract_cmd = exe

    try:
        prep_img, _ = preprocess_image_for_ocr(image, upscale_small=False)
        data = pytesseract.image_to_data(
            prep_img,
            lang=lang,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 11",
        )

        n_boxes = len(data.get("text", []))
        ox, oy = offset
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

            elem_type, interaction = _classify_ocr_text_role(word, h, win_rect, cy)
            cat = _detect_icon_category(word, "")
            section = classify_screen_section(cx, cy, win_rect)

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
                section=section,
            )
            elements.append(elem)

            b = data.get("block_num", [0])[i]
            p = data.get("par_num", [0])[i]
            l = data.get("line_num", [0])[i]
            key = (b, p, l)
            if key not in lines:
                lines[key] = []
            lines[key].append({"word": word, "x": x, "y": y, "w": w, "h": h, "conf": conf})

        # Multi-word phrase grouping
        for key, w_list in lines.items():
            if len(w_list) > 1:
                w_list.sort(key=lambda item: item["x"])
                phrase_text = " ".join([item["word"] for item in w_list]).strip()
                min_x = min(item["x"] for item in w_list)
                min_y = min(item["y"] for item in w_list)
                max_r = max(item["x"] + item["w"] for item in w_list)
                max_b = max(item["y"] + item["h"] for item in w_list)
                avg_conf = sum(item["conf"] for item in w_list) / len(w_list)
                pcx = int((min_x + max_r) / 2)
                pcy = int((min_y + max_b) / 2)
                ph = max_b - min_y

                p_type, p_interaction = _classify_ocr_text_role(phrase_text, ph, win_rect, pcy)
                p_cat = _detect_icon_category(phrase_text, "")
                p_section = classify_screen_section(pcx, pcy, win_rect)

                phrase_elem = ScreenElement(
                    text=phrase_text,
                    element_type=p_type,
                    bbox=(min_x, min_y, max_r, max_b),
                    center=(pcx, pcy),
                    confidence=avg_conf / 100.0,
                    enabled=True,
                    selected=False,
                    visible=True,
                    interaction=p_interaction,
                    source="ocr",
                    is_container=False,
                    category=p_cat,
                    section=p_section,
                )
                elements.append(phrase_elem)

    except Exception as e:
        print(f"[ScreenReader] OCR extraction error: {e}")

    return sort_elements_reading_order(elements)


# ============================================================================
# TIER 3: LOCAL COMPUTER VISION (ICONS, CONTROLS & GEOMETRY)
# ============================================================================

def detect_visual_controls(
    image: Any,
    window_rect: tuple[int, int, int, int],
) -> list[ScreenElement]:
    """
    Fast local OpenCV contour and geometry detection for unlabeled controls:
      • Profile avatar badges (circles in header/sidebar)
      • Window controls (Close, Minimize, Maximize)
      • Checkboxes & Radio buttons
      • Toggle switches
      • Dropdown chevrons
      • Action icons (Settings gear, Search magnifying glass, Plus, Save floppy)
    """
    elements: list[ScreenElement] = []
    if not _CV2 or image is None:
        return elements

    win_l, win_t, win_r, win_b = window_rect
    win_w = max(1, win_r - win_l)
    win_h = max(1, win_b - win_t)

    try:
        img_np = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < 12 or h < 12 or w > 90 or h > 90:
                continue
            if not (win_l <= x <= win_r and win_t <= y <= win_b):
                continue

            aspect = float(w) / float(h)
            cx = int(x + w / 2)
            cy = int(y + h / 2)
            rel_x = cx - win_l
            rel_y = cy - win_t
            section = classify_screen_section(cx, cy, window_rect)

            # 1. Window controls at top-right
            if rel_y <= 40 and rel_x >= win_w - 140 and 12 <= w <= 46 and 12 <= h <= 40:
                if rel_x >= win_w - 48:
                    elements.append(ScreenElement(
                        text="Close Button",
                        element_type="window_control",
                        bbox=(x, y, x + w, y + h),
                        center=(cx, cy),
                        confidence=0.92,
                        category="close",
                        section="title_bar",
                        source="vision",
                    ))
                elif rel_x >= win_w - 95:
                    elements.append(ScreenElement(
                        text="Maximize Button",
                        element_type="window_control",
                        bbox=(x, y, x + w, y + h),
                        center=(cx, cy),
                        confidence=0.88,
                        category="maximize",
                        section="title_bar",
                        source="vision",
                    ))
                else:
                    elements.append(ScreenElement(
                        text="Minimize Button",
                        element_type="window_control",
                        bbox=(x, y, x + w, y + h),
                        center=(cx, cy),
                        confidence=0.88,
                        category="minimize",
                        section="title_bar",
                        source="vision",
                    ))
                continue

            # 2. Toggle Switch (pill rectangle: aspect 1.6-2.5)
            if 1.6 <= aspect <= 2.6 and 26 <= w <= 64 and 14 <= h <= 32:
                elements.append(ScreenElement(
                    text="Toggle Switch",
                    element_type="toggle",
                    bbox=(x, y, x + w, y + h),
                    center=(cx, cy),
                    confidence=0.82,
                    interaction="toggle",
                    category="toggle",
                    section=section,
                    source="vision",
                ))
                continue

            # 3. Checkbox (square outline: aspect 0.9-1.1, size 12-24)
            if 0.85 <= aspect <= 1.15 and 12 <= w <= 24 and 12 <= h <= 24:
                elements.append(ScreenElement(
                    text="Checkbox",
                    element_type="checkbox",
                    bbox=(x, y, x + w, y + h),
                    center=(cx, cy),
                    confidence=0.80,
                    interaction="toggle",
                    category="checkbox",
                    section=section,
                    source="vision",
                ))
                continue

            # 4. Circular Profile Avatar Badge (aspect 0.9-1.1 in header or top-right)
            if 0.88 <= aspect <= 1.12 and 22 <= w <= 60 and rel_y <= 120 and rel_x >= win_w * 0.60:
                elements.append(ScreenElement(
                    text="Profile Avatar Icon",
                    element_type="icon",
                    bbox=(x, y, x + w, y + h),
                    center=(cx, cy),
                    confidence=0.88,
                    category="profile",
                    section=section,
                    source="vision",
                ))
                continue

    except Exception as e:
        print(f"[ScreenReader] detect_visual_controls error: {e}")

    return elements


def find_visual_icon(
    category: str,
    image: Any,
    window_rect: tuple[int, int, int, int],
) -> Optional[ScreenElement]:
    """Targeted visual analysis for unlabeled icons using Hough circles and geometry."""
    if not _CV2 or image is None:
        return None

    cat = category.strip().lower()
    win_l, win_t, win_r, win_b = window_rect
    win_w = max(1, win_r - win_l)
    win_h = max(1, win_b - win_t)

    try:
        img_np = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

        # Profile Avatar Icon
        if cat in ("profile", "account", "user", "avatar"):
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
                    scored_circles = []
                    for c in circles[0]:
                        cx_in_header, cy_in_header, r = c[0], c[1], c[2]
                        abs_cx = crop_x1 + cx_in_header
                        abs_cy = crop_y1 + cy_in_header
                        dist_to_top_right = abs(abs_cx - (win_l + win_w * 0.90)) + (abs_cy - win_t)
                        scored_circles.append((dist_to_top_right, abs_cx, abs_cy, r))

                    scored_circles.sort(key=lambda item: item[0])
                    _, cx, cy, r = scored_circles[0]
                    return ScreenElement(
                        text="Profile Avatar Icon",
                        element_type="icon",
                        bbox=(cx - r, cy - r, cx + r, cy + r),
                        center=(cx, cy),
                        confidence=0.88,
                        enabled=True,
                        visible=True,
                        interaction="click",
                        source="vision",
                        category="profile",
                        section="title_bar" if cy <= win_t + 50 else "menu_ribbon",
                    )

        # Settings gear icon (often top-right or sidebar bottom)
        if cat in ("settings", "preferences", "gear"):
            controls = detect_visual_controls(image, window_rect)
            for ctrl in controls:
                if ctrl.category == "settings":
                    return ctrl

    except Exception as e:
        print(f"[ScreenReader] Visual icon heuristic error: {e}")

    return None


# ============================================================================
# TIER 4: GEMINI VISION FALLBACK FOR STYLIZED / UNLABELED CONTROLS
# ============================================================================

def visual_locate_with_gemini(
    description: str,
    image: Any,
    window_rect: tuple[int, int, int, int],
) -> Optional[ScreenElement]:
    """
    Tier 4 Fallback using Gemini Multimodal Vision when target has no text label or UIA node.
    """
    if image is None:
        return None

    try:
        from core import gemini
        from google.genai import types as gtypes

        win_l, win_t, win_r, win_b = window_rect
        crop_box = (max(0, win_l), max(0, win_t), min(image.width, win_r), min(image.height, win_b))
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            cropped = image
            crop_offset_x, crop_offset_y = 0, 0
        else:
            cropped = image.crop(crop_box)
            crop_offset_x, crop_offset_y = crop_box[0], crop_box[1]

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
                        bbox=(abs_cx - 16, abs_cy - 16, abs_cx + 16, abs_cy + 16),
                        center=(abs_cx, abs_cy),
                        confidence=conf,
                        enabled=True,
                        visible=True,
                        interaction="click",
                        source="gemini",
                        category=_detect_icon_category(description, ""),
                        section=classify_screen_section(abs_cx, abs_cy, window_rect),
                    )
    except Exception as e:
        print(f"[ScreenReader] visual_locate_with_gemini error: {e}")

    return None


# ============================================================================
# DIALOG & MODAL POPUP RECOGNITION
# ============================================================================

def detect_modal_dialog(screen_state: ScreenState) -> Optional[dict[str, Any]]:
    """Detect modal dialogs or error popups on screen and extract title, message, and buttons."""
    win_info = get_active_window_info()
    is_dialog = win_info.get("is_dialog", False)
    title = screen_state.window_title

    error_keywords = ("error", "failed", "warning", "confirm", "alert", "notice", "save changes", "unsaved")
    has_dialog_title = any(k in title.lower() for k in error_keywords)

    if not is_dialog and not has_dialog_title:
        # Check if any element is an error message
        err_elems = [e for e in screen_state.elements if e.element_type == "error_message"]
        if not err_elems:
            return None

    dialog_buttons = [
        e for e in screen_state.elements
        if e.element_type in ("button", "window_control") and e.text.lower() in ("ok", "cancel", "yes", "no", "close", "retry", "discard", "apply")
    ]
    message_lines = [
        e.text for e in screen_state.elements
        if e.element_type in ("error_message", "text") and len(e.text) > 8 and not e.is_container
    ]

    return {
        "title": title,
        "is_modal": True,
        "buttons": [b.text for b in dialog_buttons],
        "message": " ".join(message_lines[:4]) if message_lines else "No specific message body detected.",
    }


# ============================================================================
# COMPREHENSIVE SCREEN STATE CAPTURE & FUSION
# ============================================================================

def capture_screen_image() -> tuple[Optional[Any], tuple[int, int], tuple[int, int]]:
    """Capture current screen into a PIL Image safely with monitor offset support."""
    ensure_desktop_and_dpi()
    img = None
    size = (1920, 1080)
    offset = (0, 0)

    try:
        import mss
        mss_cls = getattr(mss, "MSS", mss.mss)
        with mss_cls() as sct:
            monitors = sct.monitors
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
    except Exception:
        pass

    try:
        if _PIL and hasattr(PIL.ImageGrab, "grab"):
            img = PIL.ImageGrab.grab(all_screens=True)
            return img, img.size, (0, 0)
    except Exception:
        pass

    try:
        if _PYAUTOGUI and pyautogui:
            img = pyautogui.screenshot()
            return img, img.size, (0, 0)
    except Exception:
        pass

    return None, size, offset


def capture_screen_state(
    include_ocr: bool = True,
    include_vision: bool = True,
    ocr_min_confidence: int = 25,
) -> ScreenState:
    """
    Capture comprehensive structured screen state fusing UIA, Preprocessed OCR, and Vision.
    Performs spatial IoU deduplication and organizes elements into functional sections.
    """
    t0 = time.time()
    ensure_desktop_and_dpi()
    win_info = get_active_window_info()
    img, screen_size, offset = capture_screen_image()
    win_rect = win_info.get("rect", (0, 0, screen_size[0], screen_size[1]))

    elements: list[ScreenElement] = []

    # Priority 1: UIA
    uia_elems = extract_uia_elements(max_elements=150)
    elements.extend(uia_elems)

    # Priority 2: Preprocessed OCR
    ocr_raw_text = ""
    if include_ocr and img is not None:
        ocr_elems = extract_ocr_elements(
            img,
            min_confidence=ocr_min_confidence,
            offset=offset,
            win_rect=win_rect,
        )
        ocr_raw_text = " ".join([e.text for e in ocr_elems])

        interactive_uia = [ue for ue in uia_elems if not ue.is_container]
        for oe in ocr_elems:
            # IoU suppression against existing UIA elements
            covered = False
            for ue in interactive_uia:
                if compute_iou(oe.bbox, ue.bbox) > 0.50 or (
                    ue.bbox[0] <= oe.center[0] <= ue.bbox[2] and ue.bbox[1] <= oe.center[1] <= ue.bbox[3] and ue.text.strip()
                ):
                    covered = True
                    break
            if not covered:
                elements.append(oe)

    # Priority 3: Local Computer Vision
    if include_vision and img is not None:
        vis_elems = detect_visual_controls(img, win_rect)
        for ve in vis_elems:
            covered = False
            for ex in elements:
                if compute_iou(ve.bbox, ex.bbox) > 0.40:
                    covered = True
                    break
            if not covered:
                elements.append(ve)

    elements = sort_elements_reading_order(elements)

    if not ocr_raw_text:
        ocr_raw_text = " ".join([e.text for e in elements if e.text and not e.text.startswith("[")])

    sections: dict[str, list[ScreenElement]] = {}
    for e in elements:
        sec = e.section or "main_content"
        sections.setdefault(sec, []).append(e)

    st = ScreenState(
        timestamp=t0,
        window_title=win_info.get("title", "Desktop"),
        process_name=win_info.get("process_name", ""),
        hwnd=win_info.get("hwnd", 0),
        window_rect=win_rect,
        screen_size=screen_size,
        elements=elements,
        raw_text=ocr_raw_text,
        sections=sections,
        screenshot=img,
    )
    st.dialog_info = detect_modal_dialog(st)
    _GLOBAL_SCREEN_REGISTRY.update(st)
    return st


# ============================================================================
# CONTEXT-AWARE TARGET DISAMBIGUATION & LOCALIZATION
# ============================================================================

def parse_query_qualifiers(query: str) -> dict[str, Any]:
    """Parse natural language query into target, ordinal, section, and spatial qualifiers."""
    q = query.strip()

    # 1. Strip leading action verbs
    q = re.sub(r"^(?:click|press|select|open|choose|find|where is|locate)\s+(?:on\s+|the\s+)?", "", q, flags=re.I).strip()

    # 2. Ordinal ("first", "second", "third", "4th", "last")
    ord_match = re.search(r"\b(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\b", q, re.I)
    ordinal = None
    if ord_match:
        ord_word = ord_match.group(1).lower()
        ord_map = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1}
        ordinal = ord_map.get(ord_word)
        q = re.sub(rf"\b{re.escape(ord_match.group(1))}\b", "", q, flags=re.I).strip()

    # 3. Section / Container ("in profile", "in settings", "in header", "in sidebar", "in dialog")
    sec_match = re.search(r"\b(?:in|under|inside)\s+(?:the\s+)?([a-zA-Z0-9_\s]+?)(?:\s+section|\s+panel|\s+tab|\s+window|\s+box|$)", q, re.I)
    section = None
    if sec_match:
        section = sec_match.group(1).strip().lower()
        q = re.sub(rf"\b(?:in|under|inside)\s+(?:the\s+)?{re.escape(sec_match.group(1))}(?:\s+section|\s+panel|\s+tab|\s+window|\s+box)?\b", "", q, flags=re.I).strip()

    # 4. Spatial ("at the top", "at the bottom", "on the left", "on the right")
    spatial = None
    if re.search(r"\b(at\s+(the\s+)?top|top\s+side)\b", q, re.I):
        spatial = "top"
        q = re.sub(r"\b(at\s+(the\s+)?top|top\s+side)\b", "", q, flags=re.I).strip()
    elif re.search(r"\b(at\s+(the\s+)?bottom|bottom\s+side)\b", q, re.I):
        spatial = "bottom"
        q = re.sub(r"\b(at\s+(the\s+)?bottom|bottom\s+side)\b", "", q, flags=re.I).strip()
    elif re.search(r"\b(on\s+(the\s+)?left|left\s+side)\b", q, re.I):
        spatial = "left"
        q = re.sub(r"\b(on\s+(the\s+)?left|left\s+side)\b", "", q, flags=re.I).strip()
    elif re.search(r"\b(on\s+(the\s+)?right|right\s+side)\b", q, re.I):
        spatial = "right"
        q = re.sub(r"\b(on\s+(the\s+)?right|right\s+side)\b", "", q, flags=re.I).strip()

    # Clean target noun
    clean_target = re.sub(r"\b(button|icon|option|item|link|field)\b", "", q, flags=re.I).strip()
    if not clean_target:
        clean_target = q

    return {
        "raw_query": query,
        "target": clean_target.strip(" ?."),
        "ordinal": ordinal,
        "section": section,
        "spatial": spatial,
    }


def _score_candidate(
    target: str,
    elem: ScreenElement,
    win_rect: tuple[int, int, int, int],
    qualifiers: dict[str, Any],
) -> float:
    """Calculate multi-factor match score for a candidate element."""
    t_clean = target.strip().lower()
    e_text = elem.text.strip().lower()
    if not t_clean or not e_text:
        return 0.0

    score = 0.0
    match_found = False

    word_count = len(e_text.split())
    if e_text == t_clean:
        score += 100.0
        match_found = True
    elif re.search(rf"\b{re.escape(t_clean)}\b", e_text, re.I):
        score += 90.0 if word_count <= 3 else 40.0
        match_found = True
    elif t_clean in e_text and len(t_clean) >= 3:
        score += 65.0 if word_count <= 2 else 35.0
        match_found = True
    elif difflib.SequenceMatcher(None, t_clean, e_text).ratio() >= 0.78:
        score += 55.0 * difflib.SequenceMatcher(None, t_clean, e_text).ratio()
        match_found = True

    # Category matching (e.g. "profile" -> profile icon)
    t_cat = _detect_icon_category(target, "")
    if t_cat and elem.category == t_cat:
        score += 85.0
        match_found = True

    if not match_found:
        return 0.0

    # Source tier preference
    if elem.source == "uia":
        score += 65.0
    elif elem.source == "vision":
        score += 55.0
    elif elem.source == "ocr":
        code_syntax = ('"', "'", '`', '{', '}', '[', ']', ';', 'def ', 'import ', 'return ', '->')
        if any(c in elem.text for c in code_syntax) and not any(c in target for c in ('"', "'", '`')):
            return 0.0

    # Type preference
    if elem.element_type in ("button", "icon", "menu_item", "tab", "link", "window_control"):
        score += 45.0
    elif elem.element_type in ("edit", "search_bar", "checkbox", "radio_button", "toggle", "dropdown"):
        score += 35.0
    elif elem.element_type == "text":
        score += 15.0
    elif elem.is_container:
        score -= 90.0

    # Section / Container qualifier boost
    sec_q = qualifiers.get("section")
    if sec_q:
        if sec_q in (elem.section or "").lower() or sec_q in (elem.parent_name or "").lower():
            score += 50.0
        else:
            score -= 20.0

    # Spatial qualifier boost
    spat_q = qualifiers.get("spatial")
    wl, wt, wr, wb = win_rect
    win_w = max(1, wr - wl)
    win_h = max(1, wb - wt)
    cx, cy = elem.center

    if spat_q == "top":
        if cy <= wt + win_h * 0.35:
            score += 40.0
        else:
            score -= 30.0
    elif spat_q == "bottom":
        if cy >= wb - win_h * 0.35:
            score += 40.0
        else:
            score -= 30.0
    elif spat_q == "left":
        if cx <= wl + win_w * 0.35:
            score += 40.0
        else:
            score -= 30.0
    elif spat_q == "right":
        if cx >= wr - win_w * 0.35:
            score += 40.0
        else:
            score -= 30.0

    # Window bounds check
    if wl <= cx <= wr and wt <= cy <= wb:
        score += 20.0
    else:
        score -= 40.0

    return score


class ScreenElementRegistry:
    """
    Short-lived screen-element registry maintaining:
      • Element text and accessible name
      • Element role
      • Bounding rectangle
      • Parent window and section
      • Detection method
      • Confidence score
      • Enabled / disabled status
      • Screen-state identifier and timestamp
    Enables instant and 100% reliable clicking of options that NOVA has just read aloud.
    """
    def __init__(self, ttl_seconds: float = 30.0):
        self.ttl = ttl_seconds
        self.last_state: Optional[ScreenState] = None
        self.timestamp: float = 0.0
        self.hwnd: int = 0
        self.window_title: str = ""
        self.elements: list[ScreenElement] = []
        self.state_id: str = ""

    def update(self, state: ScreenState) -> None:
        self.last_state = state
        self.timestamp = time.time()
        self.hwnd = state.hwnd
        self.window_title = state.window_title
        self.elements = list(state.elements)
        self.state_id = f"{state.hwnd}:{state.window_title}:{len(state.elements)}:{state.raw_text[:60]}"

    def is_valid(self, max_age: Optional[float] = None) -> bool:
        ttl = max_age if max_age is not None else self.ttl
        if not self.elements or (time.time() - self.timestamp) > ttl:
            return False
        return True

    def invalidate(self) -> None:
        self.timestamp = 0.0
        self.elements.clear()

    def find_target(self, description: str, index: int = 1) -> Optional[dict[str, Any]]:
        if not self.is_valid():
            return None

        win_rect = self.last_state.window_rect if self.last_state else (0, 0, 1920, 1080)
        qualifiers = parse_query_qualifiers(description)
        target_clean = qualifiers["target"]
        if qualifiers["ordinal"] is not None:
            index = qualifiers["ordinal"]

        scored = []
        for elem in self.elements:
            if elem.is_container:
                continue
            s = _score_candidate(target_clean, elem, win_rect, qualifiers)
            if s >= 40.0:
                scored.append((s, elem))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)

        # Ambiguity check
        if len(scored) > 1 and qualifiers["ordinal"] is None and qualifiers["section"] is None and qualifiers["spatial"] is None:
            s0, e0 = scored[0]
            s1, e1 = scored[1]
            if abs(s0 - s1) < 25.0 and e0.text.strip().lower() == e1.text.strip().lower():
                clarification = (
                    f"Multiple '{e0.text}' controls detected on screen: "
                    f"one at {e0.center} in {e0.section} and another at {e1.center} in {e1.section}. "
                    f"Please specify which one to click (e.g., 'first {e0.text}', 'second {e0.text}', or '{e0.text} in {e0.section}')."
                )
                return {
                    "found": False,
                    "element": None,
                    "center": (0, 0),
                    "confidence": 0.0,
                    "method": "ambiguous",
                    "candidates_count": len(scored),
                    "clarification": clarification,
                    "from_registry": True,
                }

        if index == -1:
            best_score, best_elem = scored[-1]
            chosen_idx = len(scored)
        else:
            chosen_idx = max(0, min(index - 1, len(scored) - 1))
            best_score, best_elem = scored[chosen_idx]

        if not getattr(best_elem, "enabled", True):
            return {
                "found": False,
                "element": best_elem,
                "center": best_elem.center,
                "confidence": 0.0,
                "method": "disabled",
                "candidates_count": len(scored),
                "clarification": f"Control '{best_elem.text}' is currently disabled and cannot be activated.",
                "from_registry": True,
            }

        return {
            "found": True,
            "element": best_elem,
            "center": best_elem.center,
            "confidence": min(1.0, best_score / 150.0),
            "method": best_elem.source,
            "candidates_count": len(scored),
            "clarification": "",
            "from_registry": True,
        }


_GLOBAL_SCREEN_REGISTRY = ScreenElementRegistry(ttl_seconds=30.0)


def locate_target(
    description: str,
    screen_state: Optional[ScreenState] = None,
    index: int = 1,
    min_confidence: float = 40.0,
) -> dict[str, Any]:
    """
    Locates target following strict 4-tier priority order:
      1. Active Screen Element Registry (previously read screen results)
      2. Windows UI Automation (interactive controls)
      3. Adaptive Local OCR (Tesseract with CLAHE, upscaling, unsharp mask)
      4. Local Computer Vision (contour & shape analysis)
      5. Multimodal Vision Fallback

    Includes ambiguity protection: if multiple close matches exist without qualifiers,
    returns safe clarification rather than blind clicking.
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

    # Requirement 4: Click after reading using short-lived element registry
    if screen_state is None:
        reg_match = _GLOBAL_SCREEN_REGISTRY.find_target(desc, index=index)
        if reg_match is not None and reg_match.get("found"):
            elem = reg_match["element"]
            # Requirement 5: Check disabled status
            if elem and not getattr(elem, "enabled", True):
                print(f"[ScreenReader] [DISABLED CONTROL] Target '{desc}' is disabled.")
                return {
                    "found": False,
                    "element": elem,
                    "center": elem.center,
                    "confidence": 0.0,
                    "method": "disabled",
                    "candidates_count": 1,
                    "clarification": f"Control '{elem.text}' is currently disabled and cannot be activated.",
                }
            print(f"[ScreenReader] [REGISTRY HIT] Target '{desc}' matched previously read '{elem.text}' at {elem.center} (method: {reg_match['method']})")
            return reg_match
        elif reg_match is not None and reg_match.get("method") == "ambiguous":
            return reg_match

        screen_state = capture_screen_state(include_ocr=True, include_vision=True)

    win_rect = screen_state.window_rect
    qualifiers = parse_query_qualifiers(desc)
    target_clean = qualifiers["target"]
    if qualifiers["ordinal"] is not None:
        index = qualifiers["ordinal"]

    # Score all elements in screen_state
    scored_candidates: list[tuple[float, ScreenElement]] = []
    for elem in screen_state.elements:
        if elem.is_container:
            continue
        s = _score_candidate(target_clean, elem, win_rect, qualifiers)
        if s >= min_confidence:
            scored_candidates.append((s, elem))

    # Sort descending by score
    scored_candidates.sort(key=lambda item: item[0], reverse=True)

    # Check for Ambiguity (Multiple close matches without ordinal/section)
    if len(scored_candidates) > 1 and qualifiers["ordinal"] is None and qualifiers["section"] is None and qualifiers["spatial"] is None:
        s0, e0 = scored_candidates[0]
        s1, e1 = scored_candidates[1]
        # If top 2 candidates have very close scores (< 15% diff) and identical/similar text
        if abs(s0 - s1) < 25.0 and e0.text.strip().lower() == e1.text.strip().lower():
            clarification = (
                f"Multiple '{e0.text}' buttons detected on screen: "
                f"one at ({e0.center[0]}, {e0.center[1]}) in {e0.section} and "
                f"another at ({e1.center[0]}, {e1.center[1]}) in {e1.section}. "
                f"Please specify which one to click (e.g., 'first {e0.text}', 'second {e0.text}', or '{e0.text} in {e0.section}')."
            )
            print(f"[ScreenReader] [AMBIGUITY PROTECTED] {clarification}")
            return {
                "found": False,
                "element": None,
                "center": (0, 0),
                "confidence": 0.0,
                "method": "ambiguous",
                "candidates_count": len(scored_candidates),
                "clarification": clarification,
            }

    if scored_candidates:
        if index == -1:
            best_score, best_elem = scored_candidates[-1]
            chosen_idx = len(scored_candidates)
        else:
            chosen_idx = max(0, min(index - 1, len(scored_candidates) - 1))
            best_score, best_elem = scored_candidates[chosen_idx]

        if not getattr(best_elem, "enabled", True):
            print(f"[ScreenReader] [DISABLED CONTROL] Target '{desc}' matched '{best_elem.text}' which is currently disabled.")
            return {
                "found": False,
                "element": best_elem,
                "center": best_elem.center,
                "confidence": 0.0,
                "method": "disabled",
                "candidates_count": len(scored_candidates),
                "clarification": f"Control '{best_elem.text}' is currently disabled and cannot be activated.",
            }

        conf = min(1.0, best_score / 150.0)
        print(f"[ScreenReader] [TARGET] Target '{desc}' matched [{best_elem.source.upper()}] '{best_elem.text}' "
              f"at {best_elem.center} (score: {best_score:.1f}, candidate {chosen_idx + 1}/{len(scored_candidates)})")
        return {
            "found": True,
            "element": best_elem,
            "center": best_elem.center,
            "confidence": conf,
            "method": best_elem.source,
            "candidates_count": len(scored_candidates),
            "clarification": "",
        }

    # Vision icon heuristic fallback
    cat = _detect_icon_category(target_clean, "")
    if cat and screen_state.screenshot is not None:
        vis_elem = find_visual_icon(cat, screen_state.screenshot, win_rect)
        if vis_elem:
            print(f"[ScreenReader] [TARGET] Target '{desc}' found via vision icon heuristic at {vis_elem.center}")
            return {
                "found": True,
                "element": vis_elem,
                "center": vis_elem.center,
                "confidence": vis_elem.confidence,
                "method": "vision",
                "candidates_count": 1,
                "clarification": "",
            }

    # Gemini Vision fallback
    if screen_state.screenshot is not None and cat:
        gemini_vis = visual_locate_with_gemini(desc, screen_state.screenshot, win_rect)
        if gemini_vis:
            print(f"[ScreenReader] [TARGET] Target '{desc}' found via Gemini Vision at {gemini_vis.center}")
            return {
                "found": True,
                "element": gemini_vis,
                "center": gemini_vis.center,
                "confidence": gemini_vis.confidence,
                "method": "gemini",
                "candidates_count": 1,
                "clarification": "",
            }

    available_btns = [e.text for e in screen_state.elements if e.element_type in ("button", "icon") and not e.is_container][:6]
    opts = f" Available options: {', '.join(available_btns)}" if available_btns else ""
    clarification = f"Could not find '{description}' in '{screen_state.window_title}'.{opts}"
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
) -> Optional[ScreenElement]:
    """Find best matching element by text label."""
    win_info = get_active_window_info()
    win_rect = win_info.get("rect", (0, 0, 1920, 1080))
    qualifiers = parse_query_qualifiers(target_text)
    scored = []
    for elem in elements:
        s = _score_candidate(qualifiers["target"], elem, win_rect, qualifiers)
        if s > 0:
            scored.append((s, elem))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else None


def find_all_matching_elements(
    target_text: str,
    elements: list[ScreenElement],
) -> list[ScreenElement]:
    """Return all matching elements (used for disambiguation lists)."""
    win_info = get_active_window_info()
    win_rect = win_info.get("rect", (0, 0, 1920, 1080))
    qualifiers = parse_query_qualifiers(target_text)
    scored = []
    for elem in elements:
        s = _score_candidate(qualifiers["target"], elem, win_rect, qualifiers)
        if s >= 40.0:
            scored.append((s, elem))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored]


# ============================================================================
# OBSERVE-PLAN-ACT-VERIFY POST-ACTION STATE VERIFICATION
# ============================================================================

def invalidate_screen_registry() -> None:
    """Invalidate cached screen elements when the screen changes."""
    _GLOBAL_SCREEN_REGISTRY.invalidate()


def activate_via_uia_patterns(elem: ScreenElement) -> tuple[bool, str]:
    """
    Priority 1 Interaction: Native Windows UI Automation control patterns.
    Invokes or selects controls directly via COM without mouse movement.
    """
    if not _UIA or auto is None:
        return False, "UIA not loaded"

    try:
        # Check enabled state
        if not getattr(elem, "enabled", True):
            return False, f"Control '{elem.text}' is disabled"

        cx, cy = elem.center
        ctrl = auto.ControlFromPoint(cx, cy)
        if not ctrl or not ctrl.Exists(0, 0):
            fg = safe_get_foreground_control()
            if fg and elem.automation_id:
                ctrl = fg.Control(AutomationId=elem.automation_id, searchDepth=8)
            elif fg and elem.text and not elem.text.startswith("["):
                ctrl = fg.Control(Name=elem.text, searchDepth=8)

        if not ctrl or not ctrl.Exists(0, 0):
            return False, "Control handle not found"

        if hasattr(ctrl, "IsEnabled") and not ctrl.IsEnabled:
            return False, f"Control '{elem.text}' is disabled"

        # 1. InvokePattern (Buttons, MenuItems, Links)
        if hasattr(ctrl, "GetInvokePattern"):
            try:
                inv = ctrl.GetInvokePattern()
                if inv:
                    inv.Invoke()
                    return True, f"Invoked '{elem.text}' via UIA InvokePattern"
            except Exception:
                pass

        # 2. TogglePattern (Checkboxes, Toggles)
        if hasattr(ctrl, "GetTogglePattern"):
            try:
                tog = ctrl.GetTogglePattern()
                if tog:
                    tog.Toggle()
                    return True, f"Toggled '{elem.text}' via UIA TogglePattern"
            except Exception:
                pass

        # 3. SelectionItemPattern (RadioButtons, Tabs, ListItems)
        if hasattr(ctrl, "GetSelectionItemPattern"):
            try:
                sel = ctrl.GetSelectionItemPattern()
                if sel:
                    sel.Select()
                    return True, f"Selected '{elem.text}' via UIA SelectionItemPattern"
            except Exception:
                pass

        # 4. ExpandCollapsePattern (Dropdowns, ComboBoxes)
        if hasattr(ctrl, "GetExpandCollapsePattern"):
            try:
                exp = ctrl.GetExpandCollapsePattern()
                if exp:
                    exp.Expand()
                    return True, f"Expanded '{elem.text}' via UIA ExpandCollapsePattern"
            except Exception:
                pass

    except Exception as e:
        return False, f"UIA activation error: {e}"

    return False, "No direct UIA pattern supported on control"


def verify_action_effect(
    before_state: ScreenState,
    after_state: ScreenState,
    clicked_center: Optional[tuple[int, int]] = None,
) -> tuple[bool, str]:
    """
    Observe updated screen and verify whether the requested action succeeded:
      • Window focus / process change
      • Window title change
      • Modal dialog appearance / disappearance
      • New interactive options appeared
      • Pixel difference in active window region
    """
    if after_state.hwnd != before_state.hwnd and after_state.hwnd != 0:
        return True, f"Active window changed to '{after_state.window_title}' ({after_state.process_name})."

    if after_state.window_title != before_state.window_title and after_state.window_title:
        return True, f"Window title updated to '{after_state.window_title}'."

    # Modal dialog change
    if not before_state.dialog_info and after_state.dialog_info:
        return True, f"Dialog appeared: '{after_state.dialog_info.get('title')}' ({after_state.dialog_info.get('message', '')[:60]})."
    if before_state.dialog_info and not after_state.dialog_info:
        return True, "Dialog was closed successfully."

    # Element appearance change
    before_texts = {e.text.strip().lower() for e in before_state.elements if e.text}
    after_texts = {e.text.strip().lower() for e in after_state.elements if e.text}
    new_texts = after_texts - before_texts
    if new_texts:
        sample = list(new_texts)[:4]
        return True, f"UI updated: new options appeared ({', '.join(sample)})."

    # Pixel difference verification
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
                if diff_w > 8 and diff_h > 8:
                    return True, f"Screen content updated in region ({bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]})."
        except Exception:
            pass

    return False, "Action executed, but no visible change was detected on screen."


# ============================================================================
# NATURAL SCREEN READING VOICE COMMANDS
# ============================================================================

def get_current_selection(screen_state: Optional[ScreenState] = None) -> str:
    """Identify the currently selected or focused element, text, or item on screen."""
    if screen_state is None:
        screen_state = capture_screen_state(include_ocr=False, include_vision=False)

    win_title = screen_state.window_title or "Active Application"

    # 1. Check UI Automation for focused control and selection
    if _UIA and auto is not None:
        try:
            focused = auto.GetFocusedElement()
            if focused:
                f_name = focused.Name or ""
                f_type = focused.ControlTypeName or "Control"

                # Check SelectionItemPattern
                try:
                    if hasattr(focused, "GetSelectionItemPattern"):
                        sel_pat = focused.GetSelectionItemPattern()
                        if sel_pat and getattr(sel_pat, "IsSelected", False):
                            return f"Currently selected item: '{f_name}' ({f_type}) in '{win_title}'."
                except Exception:
                    pass

                # Check TextPattern selection
                try:
                    if hasattr(focused, "GetTextPattern"):
                        text_pat = focused.GetTextPattern()
                        if text_pat:
                            sel_ranges = text_pat.GetSelection()
                            if sel_ranges and len(sel_ranges) > 0:
                                sel_text = sel_ranges[0].GetText(-1)
                                if sel_text and sel_text.strip():
                                    preview = sel_text.strip().replace("\n", " ")
                                    if len(preview) > 80:
                                        preview = preview[:77] + "..."
                                    return f"Currently selected text in '{f_name}': \"{preview}\""
                except Exception:
                    pass

                # Check ValuePattern or general focus name
                try:
                    if hasattr(focused, "GetValuePattern"):
                        vp = focused.GetValuePattern()
                        if vp and getattr(vp, "Value", None):
                            return f"Currently active in '{f_name}' ({f_type}) with value: \"{vp.Value}\"."
                except Exception:
                    pass

                if f_name:
                    return f"Currently focused: '{f_name}' ({f_type}) in '{win_title}'."
        except Exception:
            pass

    # 2. Check screen_state elements marked selected
    selected_elems = [e for e in screen_state.elements if getattr(e, "selected", False)]
    if selected_elems:
        labels = [e.text for e in selected_elems if e.text]
        if labels:
            return f"Currently selected: {', '.join(labels)} in '{win_title}'."

    return f"No item or text is currently selected in active window '{win_title}'."


def answer_screen_question(
    query: str,
    screen_state: Optional[ScreenState] = None,
) -> str:
    """Analyze the screen and answer natural user questions clearly."""
    if screen_state is None:
        screen_state = capture_screen_state(include_ocr=True, include_vision=True)

    q = (query or "").lower().strip()
    win_title = screen_state.window_title
    proc = screen_state.process_name or "application"
    elems = screen_state.elements

    # 0. Selection query
    if any(k in q for k in ("what is selected", "what is currently selected", "what's selected", "current selection", "what is highlighted", "what's highlighted", "which item is selected")):
        return get_current_selection(screen_state=screen_state)

    buttons = [e for e in elems if e.element_type in ("button", "icon", "window_control") and not e.is_container]
    inputs = [e for e in elems if e.element_type in ("edit", "search_bar")]
    tabs = [e for e in elems if e.element_type == "tab"]

    # 1. Dialog / Error Message Question
    if any(k in q for k in ("error", "error message", "dialog", "popup", "what does the error say", "read the error")):
        if screen_state.dialog_info:
            d = screen_state.dialog_info
            btns = ", ".join(d.get("buttons", [])) or "None"
            return f"Dialog Title: '{d.get('title')}'\nMessage: {d.get('message')}\nAvailable Buttons: {btns}"
        err_elems = [e for e in elems if e.element_type == "error_message"]
        if err_elems:
            return f"Detected error message in '{win_title}': \"{err_elems[0].text}\""
        return f"No error dialog or error message was detected in the active window '{win_title}'."

    # 2. General Screen Overview / "Read my screen" / "What is on my screen"
    if any(k in q for k in ("read my screen", "what is on", "what's on my screen", "explain the screen", "summarize", "tell me what this window is showing", "what do you see")):
        lines = [f"Active Window: '{win_title}' ({proc})."]
        if screen_state.dialog_info:
            lines.append(f"Modal Dialog Present: '{screen_state.dialog_info.get('title')}' with message: \"{screen_state.dialog_info.get('message')}\"")
        if buttons:
            btn_labels = [b.text for b in buttons if b.text and not b.text.startswith("[")][:8]
            if btn_labels:
                lines.append(f"Buttons & Actions: {', '.join(btn_labels)}.")
        if inputs:
            inp_labels = [i.text for i in inputs if i.text and not i.text.startswith("[")][:4]
            lines.append(f"Input Fields: {', '.join(inp_labels) if inp_labels else str(len(inputs)) + ' fields'}.")
        if tabs:
            tab_labels = [t.text for t in tabs if t.text][:6]
            if tab_labels:
                lines.append(f"Tabs: {', '.join(tab_labels)}.")
        if screen_state.raw_text:
            preview = screen_state.raw_text[:300].replace("\n", " ").strip()
            lines.append(f"Visible Text Preview: \"{preview}...\"")
        return "\n".join(lines)

    # 3. Available Options / Buttons
    if any(k in q for k in ("what buttons", "clickable", "options", "what can i click", "which options", "what options", "available actions", "what to click")):
        # Check if user asked for a specific section (e.g. "options on the left" or "buttons in title bar")
        filter_sec = None
        if "top" in q or "header" in q:
            filter_sec = ("title_bar", "menu_ribbon")
        elif "left" in q or "sidebar" in q:
            filter_sec = ("sidebar_left",)
        elif "right" in q:
            filter_sec = ("sidebar_right",)
        elif "bottom" in q or "footer" in q:
            filter_sec = ("status_bar",)

        active_buttons = buttons
        if filter_sec:
            active_buttons = [b for b in buttons if b.section in filter_sec]

        if not active_buttons:
            sec_desc = f" in {filter_sec}" if filter_sec else ""
            return f"In the active window '{win_title}', no interactive buttons were detected{sec_desc}."
        items = []
        for b in active_buttons[:15]:
            label = b.text
            if label and not label.startswith("["):
                items.append(f"• {label} (in {b.section} at {b.center[0]}, {b.center[1]})")
        return f"Options available in '{win_title}':\n" + "\n".join(items)

    # 4. Find button / where is
    m_find = re.search(r"(?:find|where is|locate)\s+(?:the\s+)?(.*?)(?:\s+button|\s+option|\s+icon|\s+item|\?|$)", q)
    if m_find:
        target = m_find.group(1).strip()
        res = locate_target(target, screen_state=screen_state)
        if res["found"]:
            elem = res["element"]
            return (
                f"Found '{elem.text}' ({elem.element_type}) in {elem.section} of '{win_title}' "
                f"at coordinates ({res['center'][0]}, {res['center'][1]}), bbox {elem.bbox} via {res['method'].upper()}."
            )
        else:
            return res.get("clarification") or f"Could not locate '{target}' in '{win_title}'."

    # General target query
    res = locate_target(query, screen_state=screen_state)
    if res["found"]:
        elem = res["element"]
        return f"Found '{elem.text}' at ({res['center'][0]}, {res['center'][1]}) in {elem.section}."

    return f"Active window is '{win_title}' ({proc}). Visible content: {screen_state.raw_text[:280]}."
