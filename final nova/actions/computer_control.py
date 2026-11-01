
import io
import hashlib
import json
import platform
import re
import string
import difflib
import subprocess
import sys
import time
import random
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple, Union

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


if platform.system() == "Windows":
    _WIN_HIDE = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE = {}


try:
    from core.screen_reader import (
        capture_screen_state,
        find_element_by_text,
        find_all_matching_elements,
        answer_screen_question,
        get_active_window_info,
        locate_target,
        verify_action_effect,
        ensure_desktop_and_dpi,
        invalidate_screen_registry,
        activate_via_uia_patterns,
    )
    from core.action_planner import (
        ActionPlanner,
        request_stop as planner_request_stop,
        is_stop_requested as planner_is_stop_requested,
    )
    from core import confirm
    _MODULES_LOADED = True
except Exception as _mod_err:
    print(f"[ComputerControl] Note: planner/screen_reader import: {_mod_err}")
    confirm = None
    _MODULES_LOADED = False


try:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True

except ImportError:
    pyautogui = None
    _PYAUTOGUI = False


def _require_pyautogui():
    if not _PYAUTOGUI or pyautogui is None:
        raise RuntimeError("pyautogui is not installed. Run: pip install pyautogui")


try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    pyperclip = None
    _PYPERCLIP = False


try:
    import pytesseract
    from PIL import Image, ImageOps, ImageEnhance

    _OCR = True

except ImportError:
    pytesseract = None
    Image = None
    ImageOps = None
    ImageEnhance = None

    _OCR = False


_TESSERACT_START_MENU = Path(
    r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Tesseract-OCR"
)


def _find_tesseract_exe():

    direct = _TESSERACT_START_MENU / "tesseract.exe"

    if direct.exists():
        return str(direct)

    if platform.system() == "Windows" and _TESSERACT_START_MENU.exists():

        try:

            for shortcut in _TESSERACT_START_MENU.glob("*.lnk"):

                script = (
                    '$ws = New-Object -ComObject WScript.Shell; '
                    '$sc = $ws.CreateShortcut("'
                    + str(shortcut)
                    + '"); '
                    '$sc.TargetPath'
                )

                result = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        script,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **_WIN_HIDE,
                )

                target = result.stdout.strip()

                if target:

                    target_path = Path(target)

                    if (
                        target_path.exists()
                        and target_path.name.lower() == "tesseract.exe"
                    ):
                        return str(target_path)

        except Exception as e:

            print(
                f"[OCR] Could not resolve Tesseract shortcut: {e}"
            )

    try:

        if _TESSERACT_START_MENU.exists():

            for exe in _TESSERACT_START_MENU.rglob(
                "tesseract.exe"
            ):
                return str(exe)

    except Exception:
        pass

    common_paths = [

        Path(
            r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        ),

        Path(
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
        ),

        Path.home()
        / "AppData"
        / "Local"
        / "Programs"
        / "Tesseract-OCR"
        / "tesseract.exe",
    ]

    for exe in common_paths:

        if exe.exists():
            return str(exe)

    try:

        result = subprocess.run(
            ["where", "tesseract"],
            capture_output=True,
            text=True,
            timeout=5,
            **_WIN_HIDE,
        )

        if result.returncode == 0:

            output = result.stdout.strip()

            if output:

                return output.splitlines()[0].strip()

    except Exception:
        pass

    return None


def _require_ocr():

    if not _OCR:

        raise RuntimeError(
            "pytesseract is not installed. "
            "Run: pip install pytesseract pillow"
        )

    exe = _find_tesseract_exe()

    if not exe:

        raise RuntimeError(
            "Tesseract OCR executable not found. "
            f"Checked: {_TESSERACT_START_MENU}"
        )

    pytesseract.pytesseract.tesseract_cmd = exe

    try:

        pytesseract.get_tesseract_version()

    except Exception as e:

        raise RuntimeError(
            f"Tesseract was found but could not be started: {e}"
        )


_OCR_CACHE = {
    "signature": None,
    "timestamp": 0.0,
    "lang": None,
    "min_confidence": None,
    "items": [],
}

_OCR_CACHE_TTL = 1.2


def _ocr_screen_signature(img):

    try:

        sample = ImageOps.grayscale(img).resize(
            (64, 36),
            Image.Resampling.BILINEAR,
        )

        return hashlib.md5(
            sample.tobytes()
        ).hexdigest()

    except Exception:

        return None


def _invalidate_ocr_cache():

    _OCR_CACHE["signature"] = None
    _OCR_CACHE["timestamp"] = 0.0
    _OCR_CACHE["items"] = []
    _OCR_CACHE["lang"] = None
    _OCR_CACHE["min_confidence"] = None
    if _MODULES_LOADED:
        try:
            invalidate_screen_registry()
        except Exception:
            pass


def _ocr_items(
    lang="eng",
    min_confidence=30,
    force=False,
):

    _require_pyautogui()
    _require_ocr()

    now = time.monotonic()

    if (
        not force
        and _OCR_CACHE["items"]
        and _OCR_CACHE["lang"] == (lang or "eng")
        and _OCR_CACHE["min_confidence"]
        == float(min_confidence)
        and now - _OCR_CACHE["timestamp"]
        < _OCR_CACHE_TTL
    ):

        return _OCR_CACHE["items"]


    img = pyautogui.screenshot()

    signature = _ocr_screen_signature(img)

    if (
        not force
        and signature is not None
        and _OCR_CACHE["signature"] == signature
        and _OCR_CACHE["lang"] == (lang or "eng")
        and _OCR_CACHE["min_confidence"]
        == float(min_confidence)
    ):

        _OCR_CACHE["timestamp"] = now

        return _OCR_CACHE["items"]


    gray = ImageOps.grayscale(img)

    width, height = gray.size

    max_width = 1280

    if width > max_width:

        scale = max_width / width

        new_width = int(width * scale)
        new_height = int(height * scale)

        gray = gray.resize(
            (new_width, new_height),
            Image.Resampling.BILINEAR,
        )

    else:

        scale = 1.0

    gray = ImageEnhance.Contrast(gray).enhance(1.10)


    data = pytesseract.image_to_data(
        gray,
        lang=lang or "eng",

        config="--oem 3 --psm 11",

        output_type=pytesseract.Output.DICT,
    )

    items = []

    count = len(data.get("text", []))

    for i in range(count):

        text = str(
            data["text"][i]
        ).strip()

        if not text:
            continue

        try:

            confidence = float(
                data["conf"][i]
            )

        except Exception:

            confidence = 0.0

        if confidence < float(min_confidence):
            continue

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])

        if scale != 1.0:

            x = int(x / scale)
            y = int(y / scale)
            w = int(w / scale)
            h = int(h / scale)

        center = (
            x + w // 2,
            y + h // 2,
        )

        items.append(
            {
                "text": text,
                "confidence": confidence,
                "box": [x, y, w, h],
                "center": center,
            }
        )


    _OCR_CACHE["signature"] = signature
    _OCR_CACHE["timestamp"] = time.monotonic()
    _OCR_CACHE["lang"] = lang or "eng"
    _OCR_CACHE["min_confidence"] = float(
        min_confidence
    )
    _OCR_CACHE["items"] = items

    return items


def _norm_ocr_text(text):

    text = str(text or "").lower().strip()

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def _ocr_find_text(
    description,
    items,
):

    target = _norm_ocr_text(
        description
    )

    if not target:
        return None

    best = None
    best_score = 0.0

    target_words = target.split()

    for item in items:

        text = _norm_ocr_text(
            item["text"]
        )

        if not text:
            continue

        score = 0.0

        if text == target:

            score = 1.0

        elif target in text:

            score = 0.95

        elif text in target:

            score = 0.90

        else:

            ratio = difflib.SequenceMatcher(
                None,
                target,
                text,
            ).ratio()

            score = ratio

        if len(target_words) > 1:

            text_words = text.split()

            matched = sum(
                1
                for word in target_words
                if any(
                    difflib.SequenceMatcher(
                        None,
                        word,
                        tw,
                    ).ratio()
                    >= 0.80
                    for tw in text_words
                )
            )

            if target_words:

                word_score = (
                    matched
                    / len(target_words)
                )

                score = max(
                    score,
                    word_score,
                )

        if score > best_score:

            best_score = score
            best = dict(item)

            best["match_score"] = score

    if best is not None:

        threshold = (
            0.55
            if len(target) >= 4
            else 0.70
        )

        if best_score >= threshold:

            return best

    return None


def _ocr_read_screen(
    lang="eng",
    min_confidence=35,
):

    items = _ocr_items(
        lang=lang,
        min_confidence=min_confidence,
    )

    if not items:

        return (
            "No readable text found "
            "on the current screen."
        )

    parts = []

    for item in items:

        parts.append(
            f'{item["text"]}'
            f'@{item["center"][0]},'
            f'{item["center"][1]}'
        )

    return " | ".join(parts)


def _ocr_find(
    description,
    lang="eng",
    min_confidence=35,
):

    items = _ocr_items(
        lang=lang,
        min_confidence=min_confidence,
    )

    return _ocr_find_text(
        description,
        items,
    )


def _execute_smart_click(
    description="",
    x=None,
    y=None,
    button="left",
    clicks=1,
    index=1,
    verify=True,
    lang="eng",
    min_confidence=30,
    allow_scroll=True,
):
    _require_pyautogui()
    if _MODULES_LOADED:
        try:
            ensure_desktop_and_dpi()
        except Exception:
            pass

    started = time.monotonic()

    if x is not None and y is not None:
        before_state = None
        if verify and _MODULES_LOADED:
            try:
                before_state = capture_screen_state(include_ocr=False)
            except Exception:
                before_state = None

        pyautogui.moveTo(int(x), int(y), duration=0.15)
        pyautogui.click(x=int(x), y=int(y), clicks=int(clicks), button=button)
        _invalidate_ocr_cache()

        verif_msg = ""
        if verify and before_state and _MODULES_LOADED:
            time.sleep(0.35)
            try:
                after_state = capture_screen_state(include_ocr=False)
                _, reason = verify_action_effect(before_state, after_state, clicked_center=(int(x), int(y)))
                verif_msg = f" Verification: {reason}"
            except Exception:
                pass

        elapsed = time.monotonic() - started
        return f"Clicked at ({int(x)}, {int(y)}) [{button} x{clicks}] in {elapsed:.2f}s.{verif_msg}"

    desc = str(description or "").strip()
    if not desc:
        cur_x, cur_y = pyautogui.position()
        pyautogui.click(x=cur_x, y=cur_y, clicks=int(clicks), button=button)
        _invalidate_ocr_cache()
        elapsed = time.monotonic() - started
        return f"Clicked at current pointer ({cur_x}, {cur_y}) [{button} x{clicks}] in {elapsed:.2f}s."

    before_state = None
    target_info = None

    if _MODULES_LOADED:
        try:
            # First attempt: Check active screen element registry (instant click for read options!)
            target_info = locate_target(
                desc,
                screen_state=None,
                index=int(index or 1),
                min_confidence=float(min_confidence or 30),
            )
        except Exception as e:
            print(f"[ComputerControl] locate_target error: {e}")

    # Check for Ambiguity protection early
    if target_info and target_info.get("method") == "ambiguous":
        elapsed = time.monotonic() - started
        return f"{target_info['clarification']} ({elapsed:.2f}s)"

    # Scroll-and-Find retry if not found
    if (not target_info or not target_info.get("found")) and _MODULES_LOADED and allow_scroll:
        print(f"[ComputerControl] '{desc}' not found on visible screen. Attempting scroll-and-find...")
        for scroll_attempt in range(1, 3):
            if before_state and getattr(before_state, "window_rect", None):
                wl, wt, wr, wb = before_state.window_rect
                scroll_x, scroll_y = (wl + wr) // 2, (wt + wb) // 2
            else:
                sw, sh = pyautogui.size()
                scroll_x, scroll_y = sw // 2, sh // 2

            pyautogui.moveTo(scroll_x, scroll_y, duration=0.1)
            pyautogui.scroll(-550)
            time.sleep(0.4)
            scrolled_state = capture_screen_state(include_ocr=True, include_vision=True)
            scrolled_target = locate_target(
                desc,
                screen_state=scrolled_state,
                index=int(index or 1),
                min_confidence=float(min_confidence or 30),
            )
            if scrolled_target and scrolled_target.get("found"):
                target_info = scrolled_target
                before_state = scrolled_state
                print(f"[ComputerControl] Target '{desc}' discovered after scroll attempt {scroll_attempt}!")
                break

    if target_info is None:
        item = _ocr_find(desc, lang=lang, min_confidence=min_confidence)
        if not item:
            item = _ocr_find(desc, lang=lang, min_confidence=20)
        if item:
            cx, cy = item["center"]
            target_info = {
                "found": True,
                "center": (cx, cy),
                "method": "ocr",
                "element": None,
                "clarification": "",
            }

    if not target_info or not target_info.get("found"):
        clarification = target_info.get("clarification") if target_info else f"Element '{desc}' was not found on screen."
        elapsed = time.monotonic() - started
        return f"{clarification} ({elapsed:.2f}s)"

    elem = target_info.get("element")
    # Check disabled control status (Requirement 5)
    if target_info.get("method") == "disabled" or (elem and not getattr(elem, "enabled", True)):
        elapsed = time.monotonic() - started
        elem_name = elem.text if elem else desc
        return f"Click aborted for '{desc}': Control '{elem_name}' is currently disabled and cannot be activated. ({elapsed:.2f}s)"

    confidence = float(target_info.get("confidence", 0.0))
    if confidence < 0.50:
        elapsed = time.monotonic() - started
        return f"Click aborted for '{desc}': confidence too low ({confidence:.2f} < 0.50). Avoided blind click. ({elapsed:.2f}s)"

    # Priority 1: Windows UI Automation control patterns (Invoke, Toggle, SelectionItem, Expand)
    if _MODULES_LOADED and elem and getattr(elem, "source", "") == "uia":
        try:
            activated, act_msg = activate_via_uia_patterns(elem)
            if activated:
                _invalidate_ocr_cache()
                verif_msg = ""
                if verify and _MODULES_LOADED:
                    time.sleep(0.35)
                    try:
                        after_state = capture_screen_state(include_ocr=False)
                        if before_state:
                            ok, reason = verify_action_effect(before_state, after_state, clicked_center=elem.center)
                            verif_msg = f" Verification: {reason}"
                    except Exception:
                        pass
                elapsed = time.monotonic() - started
                return f"Successfully activated '{desc}' ({elem.text}) via Windows UI Automation in {elapsed:.2f}s.{verif_msg}"
        except Exception as e:
            print(f"[ComputerControl] UIA activation attempt: {e}")

    cx, cy = target_info["center"]
    sw, sh = pyautogui.size()
    if not (0 <= cx < sw and 0 <= cy < sh):
        elapsed = time.monotonic() - started
        return f"Click aborted for '{desc}': coordinates ({cx}, {cy}) are outside screen bounds ({sw}x{sh}). ({elapsed:.2f}s)"

    if before_state and getattr(before_state, "window_rect", None):
        wl, wt, wr, wb = before_state.window_rect
        if not (wl - 60 <= cx <= wr + 60 and wt - 60 <= cy <= wb + 60):
            elapsed = time.monotonic() - started
            return f"Click aborted for '{desc}': coordinates ({cx}, {cy}) are outside active window bounds. ({elapsed:.2f}s)"

    method = target_info.get("method", "ocr").upper()
    elem_text = target_info["element"].text if target_info.get("element") else desc

    pyautogui.moveTo(cx, cy, duration=0.15)
    pyautogui.click(x=cx, y=cy, clicks=int(clicks), button=button)
    _invalidate_ocr_cache()

    verif_msg = ""
    if verify and before_state and _MODULES_LOADED:
        time.sleep(0.35)
        try:
            after_state = capture_screen_state(include_ocr=False)
            ok, reason = verify_action_effect(before_state, after_state, clicked_center=(cx, cy))
            verif_msg = f" Verification: {reason}"
        except Exception as e:
            verif_msg = f" Verification check error: {e}"

    elapsed = time.monotonic() - started
    return (
        f"Successfully clicked '{desc}' ({elem_text}) at ({cx}, {cy}) via {method} in {elapsed:.2f}s.{verif_msg}"
    )


def _execute_dropdown_selection(dropdown_name: str, option_name: str) -> str:
    """Expand dropdown and select matching option item."""
    started = time.monotonic()
    click_res = _execute_smart_click(description=dropdown_name, verify=False, allow_scroll=False)
    if "aborted" in click_res.lower() or "not found" in click_res.lower() or "detected on screen" in click_res.lower():
        return f"Failed to open dropdown '{dropdown_name}': {click_res}"
    time.sleep(0.4)
    expanded_state = capture_screen_state(include_ocr=True, include_vision=True)
    opt_info = locate_target(option_name, screen_state=expanded_state)
    if not opt_info.get("found"):
        elapsed = time.monotonic() - started
        return f"Dropdown '{dropdown_name}' opened, but option '{option_name}' was not found in the expanded options. ({elapsed:.2f}s)"
    cx, cy = opt_info["center"]
    pyautogui.moveTo(cx, cy, duration=0.15)
    pyautogui.click(cx, cy)
    time.sleep(0.3)
    elapsed = time.monotonic() - started
    return f"Successfully selected '{option_name}' from dropdown '{dropdown_name}' at ({cx}, {cy}) in {elapsed:.2f}s."


def _ocr_click(
    description,
    button="left",
    clicks=1,
    lang="eng",
    min_confidence=30,
    index=1,
    verify=True,
):
    return _execute_smart_click(
        description=description,
        button=button,
        clicks=clicks,
        lang=lang,
        min_confidence=min_confidence,
        index=index,
        verify=verify,
    )


_BASE_DIR = Path(__file__).resolve().parent.parent

_CONFIG_PATH = (
    _BASE_DIR
    / "config"
    / "config.json"
)

_MEMORY_PATH = (
    _BASE_DIR
    / "memory"
    / "memory.json"
)


def _base_dir():

    return _BASE_DIR


def _load_config():

    try:

        return json.loads(
            _CONFIG_PATH.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return {}


def _platform_os():

    return platform.system()


def _get_os():

    system = platform.system().lower()

    if "windows" in system:
        return "windows"

    if "darwin" in system:
        return "mac"

    if "linux" in system:
        return "linux"

    return system


def _get_api_key():

    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):

        value = (
            __import__("os")
            .environ
            .get(name)
        )

        if value:
            return value

    config = _load_config()

    possible = [
        config.get("GEMINI_API_KEY"),
        config.get("GOOGLE_API_KEY"),
        config.get("api_key"),
    ]

    for value in possible:

        if value:
            return value

    return None


def _safe_screenshot_path(path=None):

    if not path:

        path = (
            _BASE_DIR
            / "screen.png"
        )

    else:

        path = Path(path)

        if not path.is_absolute():

            path = (
                _BASE_DIR
                / path
            )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def _require_pyautogui():

    if not _PYAUTOGUI:

        raise RuntimeError(
            "PyAutoGUI is not installed. "
            "Run: pip install pyautogui"
        )


_FIRST_NAMES = [
    "Arun",
    "Rahul",
    "Karthik",
    "Vijay",
    "Ajay",
    "Naveen",
    "Arjun",
    "Suresh",
    "Prakash",
    "Ravi",
    "Nithin",
    "Surya",
]

_LAST_NAMES = [
    "Kumar",
    "Raj",
    "Sharma",
    "Singh",
    "Patel",
    "Reddy",
    "Das",
    "Iyer",
    "Menon",
    "Krishnan",
]

_DOMAINS = [
    "gmail.com",
    "outlook.com",
    "yahoo.com",
    "example.com",
]


def _random_data(
    data_type
):

    dt = str(
        data_type
    ).lower().strip()

    if dt == "first_name":

        return random.choice(
            _FIRST_NAMES
        )

    if dt == "last_name":

        return random.choice(
            _LAST_NAMES
        )

    if dt == "name":

        return (
            random.choice(
                _FIRST_NAMES
            )
            + " "
            + random.choice(
                _LAST_NAMES
            )
        )

    if dt == "email":

        first = random.choice(
            _FIRST_NAMES
        ).lower()

        last = random.choice(
            _LAST_NAMES
        ).lower()

        number = random.randint(
            10,
            999,
        )

        return (
            f"{first}.{last}"
            f"{number}@"
            f"{random.choice(_DOMAINS)}"
        )

    if dt == "username":

        return (
            random.choice(
                _FIRST_NAMES
            ).lower()
            + str(
                random.randint(
                    100,
                    9999,
                )
            )
        )

    if dt == "password":

        chars = (
            string.ascii_letters
            + string.digits
            + "!@#$%"
        )

        raw = (
            random.choice(
                string.ascii_uppercase
            )
            + random.choice(
                string.digits
            )
            + random.choice(
                "!@#$%"
            )
            + "".join(
                random.choices(
                    chars,
                    k=9,
                )
            )
        )

        return "".join(
            random.sample(
                raw,
                len(raw),
            )
        )

    if dt == "phone":

        return (
            "+1"
            + str(
                random.randint(
                    200,
                    999,
                )
            )
            + str(
                random.randint(
                    1_000_000,
                    9_999_999,
                )
            )
        )

    if dt == "birthday":

        y = random.randint(
            1980,
            2000,
        )

        m = random.randint(
            1,
            12,
        )

        d = random.randint(
            1,
            28,
        )

        return (
            f"{m:02d}/"
            f"{d:02d}/"
            f"{y}"
        )

    if dt == "address":

        number = random.randint(
            100,
            9999,
        )

        street = random.choice(
            [
                "Main St",
                "Oak Ave",
                "Park Blvd",
                "Elm St",
                "Cedar Ln",
            ]
        )

        return (
            f"{number} {street}"
        )

    if dt == "zip_code":

        return str(
            random.randint(
                10000,
                99999,
            )
        )

    if dt == "city":

        return random.choice(
            [
                "Chennai",
                "Bangalore",
                "Mumbai",
                "Delhi",
                "Hyderabad",
            ]
        )

    return (
        f"random_{data_type}_"
        f"{random.randint(1000, 9999)}"
    )


def _user_profile():

    try:

        if _MEMORY_PATH.exists():

            data = json.loads(
                _MEMORY_PATH.read_text(
                    encoding="utf-8"
                )
            )

            identity = data.get(
                "identity",
                {},
            )

            return {
                k: (
                    v.get("value", "")
                    if isinstance(v, dict)
                    else v
                )
                for k, v in identity.items()
            }

    except Exception:

        pass

    return {}


def _type(
    text,
    interval=0.03,
):

    _require_pyautogui()

    time.sleep(0.15)

    pyautogui.write(
        str(text),
        interval=interval,
    )

    return (
        f"Typed: "
        f"{str(text)[:60]}"
        f"{'…' if len(str(text)) > 60 else ''}"
    )


def _smart_type(
    text,
    clear_first=False,
):

    _require_pyautogui()

    text = str(text)

    if clear_first:

        _clear_field()

        time.sleep(0.05)

    if (
        (len(text) > 12 or "\n" in text or not text.isascii())
        and _PYPERCLIP
    ):
        try:
            pyperclip.copy(text)
            paste_key = (
                "command"
                if _get_os() == "mac"
                else "ctrl"
            )
            pyautogui.hotkey(
                paste_key,
                "v",
            )
            return (
                f"Smart-typed "
                f"(clipboard): "
                f"{text[:60]}"
                f"{'…' if len(text) > 60 else ''}"
            )
        except Exception:
            # Clipboard restricted or busy; fall back to line-by-line typing
            pass

    for line_idx, line in enumerate(text.split("\n")):
        if line_idx > 0:
            pyautogui.press("enter")
        if line:
            pyautogui.write(line, interval=0.01)

    return (
        f"Smart-typed: "
        f"{text[:60]}"
        f"{'…' if len(text) > 60 else ''}"
    )


def _click(
    x=None,
    y=None,
    button="left",
    clicks=1,
):

    _require_pyautogui()

    if x is None or y is None:

        pyautogui.click(
            button=button,
            clicks=clicks,
        )

        return (
            f"Clicked at current "
            f"position [{button}]"
        )

    pyautogui.click(
        x=int(x),
        y=int(y),
        button=button,
        clicks=clicks,
    )

    return (
        f"Clicked at "
        f"({int(x)}, {int(y)})"
    )


def _hotkey(*keys):

    _require_pyautogui()

    clean_keys = []

    for key in keys:

        if key is None:
            continue

        key = str(key).strip().lower()

        if not key:
            continue

        aliases = {
            "control": "ctrl",
            "ctl": "ctrl",
            "return": "enter",
            "escape": "esc",
            "windows": "win",
            "super": "win",
            "option": "alt",
            "pgup": "pageup",
            "pgdn": "pagedown",
        }

        key = aliases.get(
            key,
            key,
        )

        clean_keys.append(key)

    if not clean_keys:
        return "No keys supplied."

    import signal
    orig_handler = None
    is_win = platform.system() == "Windows"
    if is_win:
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
        except Exception:
            pass
    try:
        try:
            orig_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        except Exception:
            pass
        pyautogui.hotkey(*clean_keys)
    finally:
        if orig_handler is not None:
            try:
                signal.signal(signal.SIGINT, orig_handler)
            except Exception:
                pass
        if is_win:
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
            except Exception:
                pass

    return (
        "Pressed hotkey: "
        + "+".join(clean_keys)
    )


def _press(key):

    _require_pyautogui()

    key = str(
        key or "enter"
    ).strip().lower()

    aliases = {
        "return": "enter",
        "escape": "esc",
        "del": "delete",
        "pgup": "pageup",
        "pgdn": "pagedown",
    }

    key = aliases.get(
        key,
        key,
    )

    pyautogui.press(key)

    return (
        f"Pressed: {key}"
    )


def _scroll(
    direction="down",
    amount=3,
):

    _require_pyautogui()

    direction = str(
        direction
    ).lower()

    amount = abs(
        int(amount)
    )

    if direction in (
        "up",
        "left",
    ):

        amount = amount

    elif direction in (
        "down",
        "right",
    ):

        amount = -amount

    else:

        direction = "down"
        amount = -amount

    if direction in (
        "left",
        "right",
    ):

        pyautogui.hscroll(
            amount
        )

    else:

        pyautogui.scroll(
            amount
        )

    return (
        f"Scrolled {direction}"
    )


def _move(
    x=None,
    y=None,
    description=None,
    direction=None,
    distance=None,
):
    _require_pyautogui()
    if _MODULES_LOADED:
        try:
            ensure_desktop_and_dpi()
        except Exception:
            pass

    cur_x, cur_y = pyautogui.position()
    sw, sh = pyautogui.size()

    # Directional relative movement
    dir_str = str(direction or "").lower().strip()
    if not dir_str and description:
        m_dir = re.search(r"\b(left|right|up|down)\b(?:\s+(?:by\s+)?(\d+))?", str(description), flags=re.I)
        if m_dir:
            dir_str = m_dir.group(1).lower()
            if m_dir.group(2) and distance is None:
                distance = int(m_dir.group(2))

    if dir_str in ("left", "right", "up", "down"):
        dist = int(distance if distance is not None else 100)
        target_x, target_y = cur_x, cur_y
        if dir_str == "left":
            target_x = max(0, cur_x - dist)
        elif dir_str == "right":
            target_x = min(sw - 1, cur_x + dist)
        elif dir_str == "up":
            target_y = max(0, cur_y - dist)
        elif dir_str == "down":
            target_y = min(sh - 1, cur_y + dist)

        pyautogui.moveTo(target_x, target_y, duration=0.15)
        new_pos = pyautogui.position()
        return f"Moved mouse {dir_str} by {dist}px to ({new_pos.x}, {new_pos.y})."

    if description and (x is None or y is None):
        if _MODULES_LOADED:
            try:
                res = locate_target(description)
                if res.get("found"):
                    x, y = res["center"]
            except Exception:
                pass
        if x is None or y is None:
            item = _ocr_find(description)
            if item:
                x, y = item["center"]

    if x is not None and y is not None:
        target_x = max(0, min(sw - 1, int(x)))
        target_y = max(0, min(sh - 1, int(y)))
        pyautogui.moveTo(target_x, target_y, duration=0.15)
        new_pos = pyautogui.position()
        return f"Moved mouse to ({new_pos.x}, {new_pos.y})" + (f" [{description}]" if description else "")

    return "Move failed: no coordinates, direction, or target description provided."


def _drag(
    x1=None,
    y1=None,
    x2=None,
    y2=None,
    source=None,
    destination=None,
):
    _require_pyautogui()
    if _MODULES_LOADED:
        try:
            ensure_desktop_and_dpi()
        except Exception:
            pass

    cur_x, cur_y = pyautogui.position()
    sw, sh = pyautogui.size()

    # Source coordinate resolution
    if x1 is None or y1 is None:
        if source and _MODULES_LOADED:
            res1 = locate_target(source)
            if res1.get("found"):
                x1, y1 = res1["center"]
        if x1 is None or y1 is None:
            x1, y1 = cur_x, cur_y

    # Destination coordinate resolution
    if x2 is None or y2 is None:
        if destination:
            m_dir = re.search(r"\b(left|right|up|down)\b(?:\s+(?:by\s+)?(\d+))?", str(destination), flags=re.I)
            if m_dir:
                d_str = m_dir.group(1).lower()
                dist = int(m_dir.group(2)) if m_dir.group(2) else 150
                x2, y2 = int(x1), int(y1)
                if d_str == "left":
                    x2 = max(0, x2 - dist)
                elif d_str == "right":
                    x2 = min(sw - 1, x2 + dist)
                elif d_str == "up":
                    y2 = max(0, y2 - dist)
                elif d_str == "down":
                    y2 = min(sh - 1, y2 + dist)
            elif _MODULES_LOADED:
                res2 = locate_target(destination)
                if res2.get("found"):
                    x2, y2 = res2["center"]

    if x1 is None or y1 is None or x2 is None or y2 is None:
        return f"Drag failed: missing source ({x1}, {y1}) or destination ({x2}, {y2}) coordinates."

    x1, y1 = max(0, min(sw - 1, int(x1))), max(0, min(sh - 1, int(y1)))
    x2, y2 = max(0, min(sw - 1, int(x2))), max(0, min(sh - 1, int(y2)))

    pyautogui.moveTo(x1, y1, duration=0.15)
    pyautogui.dragTo(x2, y2, duration=0.35, button="left")
    _invalidate_ocr_cache()

    new_pos = pyautogui.position()
    desc_info = ""
    if source or destination:
        desc_info = f" ['{source or 'current position'}' -> '{destination}']"
    return f"Dragged from ({x1}, {y1}) to ({new_pos.x}, {new_pos.y}){desc_info}."


def _select_item(target="", x=None, y=None) -> str:
    _require_pyautogui()
    if _MODULES_LOADED:
        try:
            ensure_desktop_and_dpi()
        except Exception:
            pass

    started = time.monotonic()
    t = str(target or "").strip()

    if t and _MODULES_LOADED:
        loc = locate_target(t)
        if loc.get("found") and loc.get("element"):
            elem = loc["element"]
            try:
                ok, msg = activate_via_uia_patterns(elem)
                if ok:
                    _invalidate_ocr_cache()
                    elapsed = time.monotonic() - started
                    return f"Selected '{elem.text}' via UI Automation in {elapsed:.2f}s."
            except Exception:
                pass
            x, y = elem.center

    if x is None or y is None:
        cur_x, cur_y = pyautogui.position()
        x, y = cur_x, cur_y

    pyautogui.moveTo(int(x), int(y), duration=0.15)
    pyautogui.doubleClick(int(x), int(y))
    _invalidate_ocr_cache()
    elapsed = time.monotonic() - started
    return f"Selected item at ({int(x)}, {int(y)}) in {elapsed:.2f}s."


def _delete_line() -> str:
    _require_pyautogui()
    pyautogui.press("home")
    time.sleep(0.04)
    pyautogui.hotkey("shift", "end")
    time.sleep(0.04)
    pyautogui.press("backspace")
    time.sleep(0.04)
    pyautogui.press("delete")
    _invalidate_ocr_cache()
    return "Deleted current line."


def _move_to_next_line() -> str:
    _require_pyautogui()
    pyautogui.press("down")
    time.sleep(0.04)
    pyautogui.press("home")
    return "Moved cursor to the next line."


def _save_document() -> str:
    _require_pyautogui()
    pyautogui.hotkey("ctrl", "s")
    return "Saved document (Ctrl+S)."


def _select_all() -> str:
    _require_pyautogui()
    pyautogui.hotkey("ctrl", "a")
    return "Selected all content (Ctrl+A)."


def _copy_text() -> str:
    _require_pyautogui()
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.05)
    clip = ""
    try:
        import pyperclip
        clip = pyperclip.paste()
    except Exception:
        pass
    sample = f" (copied: '{clip[:40]}...')" if clip else ""
    return f"Copied selected text to clipboard (Ctrl+C){sample}."


def _paste_text() -> str:
    _require_pyautogui()
    pyautogui.hotkey("ctrl", "v")
    return "Pasted clipboard content (Ctrl+V)."


def _undo_action() -> str:
    try:
        from core.undo import can_undo, undo_last
        if can_undo():
            return undo_last()
    except Exception:
        pass
    _require_pyautogui()
    pyautogui.hotkey("ctrl", "z")
    return "Undone in active application (Ctrl+Z)."


def _hover(x=None, y=None, description=None):
    _require_pyautogui()
    if _MODULES_LOADED:
        try:
            ensure_desktop_and_dpi()
        except Exception:
            pass

    if description and (x is None or y is None):
        if _MODULES_LOADED:
            try:
                res = locate_target(description)
                if res["found"]:
                    x, y = res["center"]
            except Exception:
                pass
        if x is None or y is None:
            item = _ocr_find(description)
            if item:
                x, y = item["center"]

    if x is not None and y is not None:
        pyautogui.moveTo(int(x), int(y), duration=0.15)
        return f"Hovering over ({int(x)}, {int(y)})" + (f" [{description}]" if description else "")

    return f"Hover failed: could not locate '{description}'." if description else "Hover failed: no coordinates provided."


def _middle_click(x=None, y=None):
    _require_pyautogui()
    return _click(x, y, button="middle", clicks=1)


def _return_mouse_safe():
    _require_pyautogui()
    try:
        sw, sh = pyautogui.size()
        pyautogui.moveTo(sw - 20, sh - 20, duration=0.1)
        return "Mouse returned to safe desktop margin."
    except Exception as e:
        return f"Could not move mouse to safe position: {e}"


def _clipboard_get():

    if not _PYPERCLIP:

        return (
            "pyperclip is not installed. "
            "Run: pip install pyperclip"
        )

    try:

        return pyperclip.paste()

    except Exception as e:

        return (
            f"Clipboard read failed: {e}"
        )


def _clipboard_paste(
    text="",
):

    _require_pyautogui()

    if not _PYPERCLIP:

        return (
            "pyperclip is not installed. "
            "Run: pip install pyperclip"
        )

    try:

        pyperclip.copy(
            str(text)
        )

        pyautogui.hotkey(
            "ctrl",
            "v",
        )

        return (
            "Pasted clipboard text."
        )

    except Exception as e:

        return (
            f"Paste failed: {e}"
        )


def _key_action(
    name
):

    mapping = {

        "select_all": (
            "hotkey",
            ("ctrl", "a"),
        ),

        "copy": (
            "hotkey",
            ("ctrl", "c"),
        ),

        "cut": (
            "hotkey",
            ("ctrl", "x"),
        ),

        "paste": (
            "hotkey",
            ("ctrl", "v"),
        ),

        "undo": (
            "hotkey",
            ("ctrl", "z"),
        ),

        "redo": (
            "hotkey",
            ("ctrl", "y"),
        ),

        "save": (
            "hotkey",
            ("ctrl", "s"),
        ),

        "find": (
            "hotkey",
            ("ctrl", "f"),
        ),

        "new_tab": (
            "hotkey",
            ("ctrl", "t"),
        ),

        "close_tab": (
            "hotkey",
            ("ctrl", "w"),
        ),

        "reopen_tab": (
            "hotkey",
            (
                "ctrl",
                "shift",
                "t",
            ),
        ),

        "next_tab": (
            "hotkey",
            ("ctrl", "tab"),
        ),

        "previous_tab": (
            "hotkey",
            (
                "ctrl",
                "shift",
                "tab",
            ),
        ),

        "escape": (
            "press",
            ("esc",),
        ),

        "enter": (
            "press",
            ("enter",),
        ),

        "tab": (
            "press",
            ("tab",),
        ),

        "shift_tab": (
            "hotkey",
            (
                "shift",
                "tab",
            ),
        ),

        "backspace": (
            "press",
            ("backspace",),
        ),

        "delete": (
            "press",
            ("delete",),
        ),

        "space": (
            "press",
            ("space",),
        ),

        "up": (
            "press",
            ("up",),
        ),

        "down": (
            "press",
            ("down",),
        ),

        "left": (
            "press",
            ("left",),
        ),

        "right": (
            "press",
            ("right",),
        ),

        "home": (
            "press",
            ("home",),
        ),

        "end": (
            "press",
            ("end",),
        ),

        "page_up": (
            "press",
            ("pageup",),
        ),

        "page_down": (
            "press",
            ("pagedown",),
        ),
    }

    if name not in mapping:

        return (
            f"Unknown key action: "
            f"{name}"
        )

    kind, keys = mapping[name]

    if kind == "hotkey":

        result = _hotkey(*keys)

    else:

        result = _press(
            keys[0]
        )

    _invalidate_ocr_cache()

    return result


def _windows_system_action(
    action
):

    _require_pyautogui()

    if platform.system() != "Windows":

        return (
            f"{action} is currently "
            "implemented for Windows."
        )

    if action == "volume_up":

        pyautogui.press(
            "volumeup"
        )

        return "Volume increased."

    if action == "volume_down":

        pyautogui.press(
            "volumedown"
        )

        return "Volume decreased."

    if action in (
        "volume_mute",
        "mute",
    ):

        pyautogui.press(
            "volumemute"
        )

        return (
            "Volume mute toggled."
        )

    if action == "brightness_up":

        try:

            pyautogui.press(
                "brightnessup"
            )

            return (
                "Brightness increased."
            )

        except Exception:

            return (
                "Brightness-up key is "
                "not supported by this "
                "keyboard."
            )

    if action == "brightness_down":

        try:

            pyautogui.press(
                "brightnessdown"
            )

            return (
                "Brightness decreased."
            )

        except Exception:

            return (
                "Brightness-down key is "
                "not supported by this "
                "keyboard."
            )

    if action == "play_pause":

        pyautogui.press(
            "playpause"
        )

        return (
            "Media play/pause toggled."
        )

    if action == "next_track":

        pyautogui.press(
            "nexttrack"
        )

        return "Next track."

    if action == "previous_track":

        pyautogui.press(
            "prevtrack"
        )

        return "Previous track."

    if action == "minimize":

        pyautogui.hotkey(
            "alt",
            "space",
        )

        time.sleep(0.05)

        pyautogui.press("n")

        return (
            "Current window minimized."
        )

    if action == "maximize":

        pyautogui.hotkey(
            "alt",
            "space",
        )

        time.sleep(0.05)

        pyautogui.press("x")

        return (
            "Current window maximized."
        )

    if action == "close_window":

        pyautogui.hotkey(
            "alt",
            "f4",
        )

        _invalidate_ocr_cache()

        return (
            "Current window close "
            "command sent."
        )

    if action == "switch_window":

        pyautogui.hotkey(
            "alt",
            "tab",
        )

        _invalidate_ocr_cache()

        return (
            "Switched window."
        )

    if action == "lock_screen":

        pyautogui.hotkey(
            "win",
            "l",
        )

        _invalidate_ocr_cache()

        return (
            "Windows lock command sent."
        )

    return (
        f"Unknown Windows "
        f"system action: {action}"
    )


def _screenshot(
    path=None,
):

    _require_pyautogui()

    output = _safe_screenshot_path(
        path
    )

    image = pyautogui.screenshot()

    image.save(
        str(output)
    )

    return (
        f"Screenshot saved to "
        f"{output}"
    )


def _clear_field():

    _require_pyautogui()

    if _get_os() == "mac":

        pyautogui.hotkey(
            "command",
            "a",
        )

    else:

        pyautogui.hotkey(
            "ctrl",
            "a",
        )

    pyautogui.press(
        "backspace"
    )

    _invalidate_ocr_cache()

    return (
        "Current field cleared."
    )


def _focus_window(
    title
):

    _require_pyautogui()

    title = str(
        title or ""
    ).strip()

    if not title:

        return (
            "No window title provided."
        )

    if platform.system() == "Windows":

        try:

            import ctypes

            user32 = ctypes.windll.user32

            hwnd = None

            def enum_callback(
                handle,
                _,
            ):

                nonlocal hwnd

                length = (
                    user32.GetWindowTextLengthW(
                        handle
                    )
                )

                if length <= 0:
                    return True

                buffer = (
                    ctypes.create_unicode_buffer(
                        length + 1
                    )
                )

                user32.GetWindowTextW(
                    handle,
                    buffer,
                    length + 1,
                )

                window_title = (
                    buffer.value
                )

                if title.lower() in (
                    window_title.lower()
                ):

                    hwnd = handle

                    return False

                return True

            callback_type = (
                ctypes.WINFUNCTYPE(
                    ctypes.c_bool,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                )
            )

            callback = callback_type(
                enum_callback
            )

            user32.EnumWindows(
                callback,
                0,
            )

            if hwnd:

                user32.ShowWindow(
                    hwnd,
                    9,
                )

                user32.SetForegroundWindow(
                    hwnd
                )

                _invalidate_ocr_cache()

                return (
                    f"Focused window: "
                    f"{title}"
                )

        except Exception as e:

            return (
                f"Could not focus "
                f"window: {e}"
            )

    return (
        "Window focusing is "
        "currently supported "
        "primarily on Windows."
    )


def _run_system_command(command: str, cwd: str = None, timeout: int = 30) -> str:
    cmd_str = str(command or "").strip()
    if not cmd_str:
        return "No command provided."

    work_dir = None
    if cwd:
        p = Path(cwd).expanduser()
        if p.exists() and p.is_dir():
            work_dir = str(p)

    try:
        if platform.system() == "Windows":
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_str],
                capture_output=True,
                text=True,
                cwd=work_dir,
                timeout=timeout,
                **_WIN_HIDE,
            )
        else:
            res = subprocess.run(
                cmd_str,
                shell=True,
                capture_output=True,
                text=True,
                cwd=work_dir,
                timeout=timeout,
            )

        stdout = (res.stdout or "").strip()
        stderr = (res.stderr or "").strip()

        out_lines = []
        if stdout:
            if len(stdout) > 3000:
                out_lines.append(stdout[:3000] + "\n...[truncated]")
            else:
                out_lines.append(stdout)
        if stderr:
            if len(stderr) > 1000:
                out_lines.append("STDERR:\n" + stderr[:1000] + "\n...[truncated]")
            else:
                out_lines.append("STDERR:\n" + stderr)

        joined = "\n".join(out_lines).strip()
        if not joined:
            return f"Command executed successfully (exit code {res.returncode})."
        return f"Exit code {res.returncode}:\n{joined}"

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Command execution failed: {e}"


def _list_open_windows() -> str:
    windows = []
    if platform.system() == "Windows":
        try:
            import ctypes
            user32 = ctypes.windll.user32

            def enum_cb(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value.strip()
                        if title and title not in ("Default IME", "MSCTFIME UI", "Program Manager"):
                            windows.append(title)
                return True

            cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(cb_type(enum_cb), 0)
        except Exception as e:
            return f"Could not enumerate windows: {e}"
    elif platform.system() == "Darwin":
        try:
            script = 'tell application "System Events" to get name of every window of (every process whose visible is true)'
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            windows = [w.strip() for w in res.stdout.split(",") if w.strip()]
        except Exception as e:
            return f"Could not enumerate windows: {e}"
    else:
        try:
            res = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
            for line in res.stdout.splitlines():
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    windows.append(parts[3])
        except Exception:
            pass

    if not windows:
        return "No visible open application windows found."

    seen = set()
    deduped = []
    for w in windows:
        if w not in seen:
            seen.add(w)
            deduped.append(w)

    return f"Active Open Windows ({len(deduped)}):\n" + "\n".join(f"- {w}" for w in deduped[:25])


def _list_running_processes(limit: int = 15) -> str:
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(['name', 'pid', 'memory_percent', 'cpu_percent']):
            try:
                info = p.info
                if info['name']:
                    procs.append(info)
            except Exception:
                continue
        procs.sort(key=lambda x: x.get('memory_percent') or 0, reverse=True)
        top = procs[:limit]
        lines = ["Top Running Processes:"]
        for p in top:
            lines.append(f"  {p['name']} (PID: {p['pid']}, RAM: {p.get('memory_percent', 0):.1f}%)")
        return "\n".join(lines)
    except Exception:
        try:
            if platform.system() == "Windows":
                res = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=5, **_WIN_HIDE)
                lines = res.stdout.splitlines()[:limit + 3]
                return "\n".join(lines)
            else:
                res = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
                lines = res.stdout.splitlines()[:limit + 1]
                return "\n".join(lines)
        except Exception as e:
            return f"Could not list processes: {e}"


def _close_app_or_process(target: str) -> str:
    name = str(target or "").strip()
    if not name:
        return "No process or application name provided."

    if platform.system() == "Windows":
        proc_name = name if name.lower().endswith(".exe") else f"{name}.exe"
        try:
            res = subprocess.run(
                ["taskkill", "/IM", proc_name, "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                **_WIN_HIDE,
            )
            if res.returncode == 0:
                return f"Closed application: {name}"
            res2 = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f'Stop-Process -Name "{name.replace(".exe","")}" -Force -ErrorAction SilentlyContinue'],
                capture_output=True,
                text=True,
                timeout=5,
                **_WIN_HIDE,
            )
            return f"Sent close command for {name}."
        except Exception as e:
            return f"Failed to close {name}: {e}"
    else:
        try:
            subprocess.run(["pkill", "-f", name], capture_output=True, timeout=5)
            return f"Closed application: {name}"
        except Exception as e:
            return f"Failed to close {name}: {e}"


_WELL_KNOWN_FOLDERS = {
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "desktop": Path.home() / "Desktop",
    "pictures": Path.home() / "Pictures",
    "videos": Path.home() / "Videos",
    "music": Path.home() / "Music",
}

_EXT_MAP = {
    "pdf": [".pdf"],
    "word": [".docx", ".doc"],
    "docx": [".docx"],
    "doc": [".doc"],
    "excel": [".xlsx", ".xls", ".csv"],
    "spreadsheet": [".xlsx", ".xls", ".csv"],
    "xlsx": [".xlsx"],
    "csv": [".csv"],
    "powerpoint": [".pptx", ".ppt"],
    "presentation": [".pptx", ".ppt"],
    "pptx": [".pptx"],
    "text": [".txt", ".log", ".md", ".json", ".py"],
    "image": [".png", ".jpg", ".jpeg", ".bmp", ".gif"],
}


def _resolve_file_or_folder(target: str) -> dict[str, Any]:
    """
    Intelligently resolve a file or folder query into a verified filesystem Path.
    Searches relevant candidate directories (Downloads, Documents, Desktop, CWD)
    without performing an unnecessary full-drive scan.
    """
    raw = str(target or "").strip().strip('"').strip("'")
    if not raw:
        return {"type": "none", "path": None, "candidates": []}

    q = re.sub(r"^(?:open|show\s+me|find|view|explore|launch)\s+(?:the\s+)?", "", raw, flags=re.I).strip()
    q_lower = q.lower()

    # 1. Exact well-known folder match
    for f_name, f_path in _WELL_KNOWN_FOLDERS.items():
        if q_lower in (f_name, f"{f_name} folder", f"the {f_name} folder", f"my {f_name}", f"my {f_name} folder"):
            if f_path.exists():
                return {"type": "folder", "path": f_path, "candidates": []}

    # 2. Direct path check
    direct = Path(raw).expanduser()
    if direct.exists():
        return {"type": "folder" if direct.is_dir() else "file", "path": direct, "candidates": []}

    # 3. Target directory hints
    search_dirs = [Path.home() / "Downloads", Path.home() / "Documents", Path.home() / "Desktop", Path.cwd()]
    if "in downloads" in q_lower or "from downloads" in q_lower:
        search_dirs = [Path.home() / "Downloads"]
        q_lower = q_lower.replace("in downloads", "").replace("from downloads", "").strip()
    elif "in documents" in q_lower or "from documents" in q_lower:
        search_dirs = [Path.home() / "Documents"]
        q_lower = q_lower.replace("in documents", "").replace("from documents", "").strip()
    elif "in desktop" in q_lower or "on desktop" in q_lower:
        search_dirs = [Path.home() / "Desktop"]
        q_lower = q_lower.replace("in desktop", "").replace("on desktop", "").strip()

    # 4. "Latest" / "recent" qualifier
    is_latest = "latest" in q_lower or "recent" in q_lower
    if is_latest:
        q_lower = q_lower.replace("latest", "").replace("recent", "").replace("the", "").strip()

    # 5. Extension filter
    exts: list[str] = []
    for k, v in _EXT_MAP.items():
        if k in q_lower:
            exts.extend(v)
            break
    if not exts and "." in q_lower:
        exts = ["." + q_lower.split(".")[-1].strip()]

    # Clean target name for matching
    clean_name = q_lower
    for word in ("open", "my", "the", "file", "document", "folder", "spreadsheet", "presentation"):
        clean_name = " ".join([w for w in clean_name.split() if w != word])

    candidates: list[Path] = []
    for sdir in search_dirs:
        if not sdir.exists():
            continue
        try:
            for item in sdir.iterdir():
                if item.is_file():
                    if exts and item.suffix.lower() not in exts:
                        continue
                    if not clean_name or clean_name in item.name.lower() or item.stem.lower() in clean_name:
                        candidates.append(item)
                elif item.is_dir() and clean_name and (clean_name == item.name.lower() or clean_name in item.name.lower()):
                    candidates.append(item)
        except Exception:
            pass

    if not candidates:
        return {"type": "none", "path": None, "candidates": []}

    # If user asked for the latest file, sort by modification timestamp
    if is_latest:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return {"type": "folder" if candidates[0].is_dir() else "file", "path": candidates[0], "candidates": []}

    if len(candidates) == 1:
        return {"type": "folder" if candidates[0].is_dir() else "file", "path": candidates[0], "candidates": []}

    # Check for exact name match among multiple
    exact = [c for c in candidates if c.name.lower() == clean_name or c.stem.lower() == clean_name]
    if len(exact) == 1:
        return {"type": "folder" if exact[0].is_dir() else "file", "path": exact[0], "candidates": []}

    return {"type": "multiple", "path": None, "candidates": candidates[:5]}


def _open_path_or_url(target: str) -> str:
    path_str = str(target or "").strip().strip('"').strip("'")
    if not path_str:
        return "No path or URL provided."

    started = time.monotonic()

    try:
        import webbrowser
        if path_str.startswith("http://") or path_str.startswith("https://") or path_str.startswith("www."):
            webbrowser.open(path_str)
            elapsed = time.monotonic() - started
            return f"Opened URL: {path_str} in default browser. ({elapsed:.2f}s)"

        # Check if target is an installed desktop application
        try:
            target_ext = Path(path_str).suffix.lower()
            doc_exts = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".csv"}
            if target_ext not in doc_exts and "folder" not in path_str.lower():
                from actions.open_app import resolve_application, launch_application
                app_spec = resolve_application(path_str)
                if app_spec:
                    ok, app_msg = launch_application(path_str)
                    return app_msg
        except Exception:
            pass

        # Resolve file or folder
        res = _resolve_file_or_folder(path_str)
        res_type = res.get("type")

        if res_type in ("file", "folder") and res.get("path"):
            resolved = res["path"]
            if platform.system() == "Windows":
                import os
                try:
                    os.startfile(str(resolved))
                except Exception:
                    if resolved.is_dir():
                        subprocess.Popen(["explorer.exe", str(resolved)])
                    else:
                        subprocess.Popen(["cmd.exe", "/c", "start", "", str(resolved)])
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(resolved)])
            else:
                subprocess.Popen(["xdg-open", str(resolved)])

            elapsed = time.monotonic() - started
            kind = "folder" if resolved.is_dir() else "file"
            return f"Successfully opened {kind} '{resolved.name}' at {resolved} in {elapsed:.2f}s."

        elif res_type == "multiple":
            cands = res.get("candidates", [])
            cand_names = [f"'{c.name}' in {c.parent.name}" for c in cands]
            elapsed = time.monotonic() - started
            return (
                f"Multiple matching files found ({', '.join(cand_names)}). "
                f"Please specify which one you would like to open. ({elapsed:.2f}s)"
            )

        else:
            elapsed = time.monotonic() - started
            return f"Could not open '{path_str}': File, folder, or application was not found. ({elapsed:.2f}s)"

    except Exception as e:
        elapsed = time.monotonic() - started
        return f"Failed to open '{path_str}': {e} ({elapsed:.2f}s)"


def _snap_window(direction: str = "left") -> str:
    _require_pyautogui()
    d = str(direction or "left").lower().strip()
    key_map = {
        "left": ["win", "left"],
        "right": ["win", "right"],
        "up": ["win", "up"],
        "maximize": ["win", "up"],
        "down": ["win", "down"],
        "minimize": ["win", "down"],
    }
    keys = key_map.get(d, ["win", "left"])
    try:
        pyautogui.hotkey(*keys)
        return f"Snapped window {d}."
    except Exception as e:
        return f"Snap window failed: {e}"


def _show_desktop() -> str:
    _require_pyautogui()
    try:
        pyautogui.hotkey("win", "d")
        return "Toggled desktop display (show/hide desktop)."
    except Exception as e:
        return f"Show desktop failed: {e}"


def _screen_find(
    description
):

    api_key = _get_api_key()

    if not api_key:

        print(
            "[ComputerControl] "
            "⚠️ No API key for screen_find"
        )

        return None

    try:

        from google import genai
        from google.genai import types as gtypes

        _require_pyautogui()

        w, h = pyautogui.size()

        img = pyautogui.screenshot()

        buffer = io.BytesIO()

        img.save(
            buffer,
            format="PNG",
        )

        image_bytes = (
            buffer.getvalue()
        )

        prompt = (
            f"This is a screenshot of a "
            f"{w}×{h} pixel screen. "
            f"Locate the UI element described "
            f"as: '{description}'. "
            f"Reply with ONLY the center "
            f"coordinates as: x,y. "
            f"If the element is not visible, "
            f"reply: NOT_FOUND"
        )

        from core import gemini

        response = gemini.call(
            [
                gtypes.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/png",
                ),
                prompt,
            ],
            tier=gemini.FAST,
            timeout_ms=20_000,
        )

        if response is None:

            return None

        response_text = (
            response.text or ""
        ).strip()

        if (
            "NOT_FOUND"
            in response_text.upper()
        ):

            return None

        match = re.search(
            r"(\d+)\s*,\s*(\d+)",
            response_text,
        )

        if match:

            return (
                int(match.group(1)),
                int(match.group(2)),
            )

    except Exception as e:

        print(
            "[ComputerControl] "
            f"⚠️ screen_find failed: {e}"
        )

    return None


_RECENT_CMD_SIG = ""
_RECENT_CMD_TIME = 0.0


def computer_control(
    parameters=None,
    player=None,
):

    params = (
        parameters
        or {}
    )

    action = str(
        params.get(
            "action",
            "",
        )
        or ""
    ).lower().strip()

    if not action:

        return (
            "No action specified "
            "for computer_control."
        )

    global _RECENT_CMD_SIG, _RECENT_CMD_TIME
    now = time.monotonic()
    cmd_sig = f"{action}:{params.get('description')}:{params.get('text')}:{params.get('direction')}:{params.get('x')}:{params.get('y')}"
    if cmd_sig == _RECENT_CMD_SIG and (now - _RECENT_CMD_TIME) < 1.0 and action not in ("scroll", "wait"):
        print(f"[ComputerControl] ⏳ Duplicate command '{action}' suppressed ({now - _RECENT_CMD_TIME:.2f}s since last)")
        return f"Command '{action}' already executed."
    _RECENT_CMD_SIG = cmd_sig
    _RECENT_CMD_TIME = now

    _detail = str(params.get("description") or params.get("target") or params.get("text") or params.get("command") or params.get("app_name") or "").strip()
    if player:
        try:
            player.write_log(
                f"SYS: [Computer Control] {action}" + (f" -> '{_detail[:60]}'" if _detail else "")
            )
        except Exception:
            pass

    print(
        f"[ComputerControl] > Executing: "
        f"{action}" + (f" ('{_detail[:80]}')" if _detail else "") + f" | Params: {params}"
    )

    try:


        if action == "type":

            result = _type(
                params.get(
                    "text",
                    "",
                )
            )

            _invalidate_ocr_cache()

            return result


        if action == "smart_type":

            result = _smart_type(
                params.get(
                    "text",
                    "",
                ),
                clear_first=params.get(
                    "clear_first",
                    True,
                ),
            )

            _invalidate_ocr_cache()

            return result


        if action in (
            "click",
            "left_click",
        ):
            description = str(
                params.get("description") or params.get("target") or params.get("text") or ""
            ).strip()

            return _execute_smart_click(
                description=description,
                x=params.get("x"),
                y=params.get("y"),
                button="left",
                clicks=int(params.get("clicks", 1)),
                index=int(params.get("index", 1)),
                verify=params.get("verify", True),
                lang=params.get("lang", "eng"),
                min_confidence=float(params.get("min_confidence", 30)),
            )


        if action == "double_click":
            description = str(
                params.get("description") or params.get("target") or params.get("text") or ""
            ).strip()

            return _execute_smart_click(
                description=description,
                x=params.get("x"),
                y=params.get("y"),
                button="left",
                clicks=2,
                index=int(params.get("index", 1)),
                verify=params.get("verify", True),
                lang=params.get("lang", "eng"),
                min_confidence=float(params.get("min_confidence", 30)),
            )


        if action == "right_click":
            description = str(
                params.get("description") or params.get("target") or params.get("text") or ""
            ).strip()

            return _execute_smart_click(
                description=description,
                x=params.get("x"),
                y=params.get("y"),
                button="right",
                clicks=1,
                index=int(params.get("index", 1)),
                verify=params.get("verify", True),
                lang=params.get("lang", "eng"),
                min_confidence=float(params.get("min_confidence", 30)),
            )


        if action in ("move", "move_to", "move_mouse", "move_left", "move_right", "move_up", "move_down"):
            direction = params.get("direction")
            if action.startswith("move_") and action not in ("move_to", "move_mouse"):
                direction = action.replace("move_", "")
            return _move(
                x=params.get("x"),
                y=params.get("y"),
                description=params.get("description") or params.get("target") or params.get("text"),
                direction=direction,
                distance=params.get("distance") or params.get("amount") or params.get("pixels"),
            )

        if action in ("select_item", "select"):
            return _select_item(
                target=params.get("description") or params.get("target") or params.get("text") or "",
                x=params.get("x"),
                y=params.get("y"),
            )

        if action in ("delete_line", "delete_this_line"):
            return _delete_line()

        if action in ("next_line", "move_to_next_line", "move_to_the_next_line"):
            return _move_to_next_line()

        if action in ("save_document", "save_this_document"):
            return _save_document()

        if action in ("select_all", "select_all_content"):
            return _select_all()

        if action in ("copy_text", "copy_this_text"):
            return _copy_text()

        if action in ("paste_text", "paste_content"):
            return _paste_text()

        if action in ("undo", "undo_last", "undo_action"):
            return _undo_action()

        if action in ("rename_file", "rename"):
            try:
                from actions.file_controller import rename_file
                p = params.get("path") or params.get("target") or "desktop"
                n = params.get("name", "")
                nn = params.get("new_name") or params.get("to") or params.get("destination", "")
                return rename_file(path=p, name=n, new_name=nn)
            except Exception as e:
                return f"Rename failed: {e}"

        if action in ("copy_file", "copy_item"):
            try:
                from actions.file_controller import copy_file
                p = params.get("path") or params.get("target") or "desktop"
                n = params.get("name", "")
                dst = params.get("destination") or params.get("to", "")
                return copy_file(path=p, name=n, destination=dst)
            except Exception as e:
                return f"Copy failed: {e}"

        if action in ("move_file", "move_item"):
            try:
                from actions.file_controller import move_file
                p = params.get("path") or params.get("target") or "desktop"
                n = params.get("name", "")
                dst = params.get("destination") or params.get("to", "")
                return move_file(path=p, name=n, destination=dst)
            except Exception as e:
                return f"Move failed: {e}"

        if action in ("stop_automation", "cancel", "stop_operation", "stop_nova"):
            try:
                from core.action_planner import request_stop
                request_stop()
            except Exception:
                pass
            try:
                from core.confirm import pending_title, resolve
                if pending_title():
                    resolve(False)
            except Exception:
                pass
            return "Operation stopped. Ready for your next command."


        if action == "drag":
            return _drag(
                x1=params.get("x1"),
                y1=params.get("y1"),
                x2=params.get("x2"),
                y2=params.get("y2"),
                source=params.get("source") or params.get("from"),
                destination=params.get("destination") or params.get("target") or params.get("to"),
            )


        if action == "hotkey":

            raw = params.get(
                "keys",
                "",
            )

            if isinstance(
                raw,
                str,
            ):

                keys = [
                    k.strip()
                    for k in raw.split("+")
                ]

            else:

                keys = raw

            result = _hotkey(
                *keys
            )

            _invalidate_ocr_cache()

            return result


        if action == "press":

            result = _press(
                params.get(
                    "key",
                    "enter",
                )
            )

            _invalidate_ocr_cache()

            return result


        key_actions = {

            "select_all",
            "copy_key",
            "cut",
            "paste_key",
            "undo",
            "redo",
            "save",
            "find",

            "new_tab",
            "close_tab",
            "reopen_tab",
            "next_tab",
            "previous_tab",

            "escape",
            "enter",
            "tab",
            "shift_tab",
            "backspace",
            "delete",
            "space",

            "up",
            "down",
            "left",
            "right",
            "home",
            "end",
            "page_up",
            "page_down",
        }

        if action in key_actions:

            normalized = {
                "copy_key": "copy",
                "paste_key": "paste",
            }.get(
                action,
                action,
            )

            return _key_action(
                normalized
            )


        windows_actions = {

            "volume_up",
            "volume_down",
            "volume_mute",
            "mute",

            "brightness_up",
            "brightness_down",

            "play_pause",
            "next_track",
            "previous_track",

            "minimize",
            "maximize",
            "close_window",
            "switch_window",
            "lock_screen",
        }

        if action in windows_actions:

            return _windows_system_action(
                action
            )


        if action == "scroll":

            result = _scroll(
                direction=params.get(
                    "direction",
                    "down",
                ),
                amount=int(
                    params.get(
                        "amount",
                        3,
                    )
                ),
            )

            _invalidate_ocr_cache()

            return result


        if action == "copy":

            return _clipboard_get()


        if action == "paste":

            result = _clipboard_paste(
                params.get(
                    "text",
                    "",
                )
            )

            _invalidate_ocr_cache()

            return result


        if action == "screenshot":

            return _screenshot(
                params.get(
                    "path"
                )
            )


        if action == "ocr_read_fast":

            items = _ocr_items(
                lang=params.get(
                    "lang",
                    "eng",
                ),
                min_confidence=float(
                    params.get(
                        "min_confidence",
                        30,
                    )
                ),
            )

            if not items:

                return (
                    "No readable text found "
                    "on the current screen."
                )

            return " | ".join(
                f'{x["text"]}'
                f'@{x["center"][0]},'
                f'{x["center"][1]}'
                for x in items
            )


        if action == "ocr_read":

            return _ocr_read_screen(
                lang=params.get(
                    "lang",
                    "eng",
                ),
                min_confidence=float(
                    params.get(
                        "min_confidence",
                        35,
                    )
                ),
            )


        if action == "ocr_find":

            item = _ocr_find(
                params.get(
                    "description",
                    "",
                ),
                lang=params.get(
                    "lang",
                    "eng",
                ),
                min_confidence=float(
                    params.get(
                        "min_confidence",
                        35,
                    )
                ),
            )

            if item:

                return json.dumps(
                    item,
                    ensure_ascii=False,
                )

            return "NOT_FOUND"


        if action == "ocr_click":

            return _ocr_click(
                params.get(
                    "description",
                    "",
                ),
                button=params.get(
                    "button",
                    "left",
                ),
                clicks=int(
                    params.get(
                        "clicks",
                        1,
                    )
                ),
                lang=params.get(
                    "lang",
                    "eng",
                ),
                min_confidence=float(
                    params.get(
                        "min_confidence",
                        30,
                    )
                ),
            )


        if action == "screen_find":

            coords = _screen_find(
                params.get(
                    "description",
                    "",
                )
            )

            if coords:

                return (
                    f"{coords[0]},"
                    f"{coords[1]}"
                )

            return "NOT_FOUND"


        if action == "screen_click":

            description = params.get(
                "description",
                "",
            )

            coords = _screen_find(
                description
            )

            if coords:

                time.sleep(0.1)

                result = _click(
                    x=coords[0],
                    y=coords[1],
                    button="left",
                    clicks=1,
                )

                _invalidate_ocr_cache()

                return (
                    f"Clicked "
                    f"'{description}' "
                    f"at {coords}"
                )

            return (
                f"Element not found "
                f"on screen: "
                f"'{description}'"
            )


        if action == "wait":

            seconds = float(
                params.get(
                    "seconds",
                    1.0,
                )
            )

            seconds = max(
                0,
                min(
                    seconds,
                    30.0,
                ),
            )

            time.sleep(
                seconds
            )

            return (
                f"Waited {seconds}s"
            )


        if action == "clear_field":

            return _clear_field()


        if action == "focus_window":

            return _focus_window(
                params.get(
                    "title",
                    "",
                )
            )


        if action == "random_data":

            data_type = params.get(
                "type",
                "name",
            )

            result = _random_data(
                data_type
            )

            print(
                f"[ComputerControl] "
                f"🎲 random "
                f"{data_type} → "
                f"{result}"
            )

            return result


        if action == "user_data":

            field = params.get(
                "field",
                "name",
            )

            profile = _user_profile()

            value = profile.get(
                field,
                "",
            )

            if not value:

                value = _random_data(
                    field
                )

                print(
                    f"[ComputerControl] "
                    f"⚠️ No '{field}' "
                    f"in memory, using "
                    f"random: {value}"
                )

            return value


        if action in (
            "run_command",
            "command",
            "powershell",
            "cmd",
            "bash",
            "exec",
            "terminal",
        ):
            cmd_text = str(params.get("command") or params.get("text", "")).strip()
            cwd_path = params.get("cwd") or params.get("path")
            timeout_sec = int(params.get("timeout", 30))
            if not cmd_text:
                return "Error: No command provided to run_command."
            if confirm and not params.get("_confirmed_by_user"):
                if confirm.pending_title():
                    return ("There is already a confirmation waiting on screen. "
                            "Ask the user to answer that one first.")
                return confirm.request(
                    key="run_system_command",
                    title="Run Terminal Command",
                    detail=f"Execute: {cmd_text[:120]}",
                    run=lambda: _run_system_command(cmd_text, cwd=cwd_path, timeout=timeout_sec),
                )
            return _run_system_command(cmd_text, cwd=cwd_path, timeout=timeout_sec)


        if action in (
            "list_windows",
            "get_windows",
            "windows",
            "open_windows",
        ):
            return _list_open_windows()


        if action in (
            "list_processes",
            "get_processes",
            "processes",
            "task_manager",
        ):
            limit_count = int(params.get("limit") or params.get("count", 15))
            return _list_running_processes(limit=limit_count)


        if action in (
            "close_app",
            "kill_process",
            "close_process",
            "kill_app",
        ):
            app_target = str(
                params.get("app_name")
                or params.get("title")
                or params.get("name")
                or params.get("text", "")
            ).strip()
            if not app_target:
                return "Error: No target application or process name specified."
            if confirm and not params.get("_confirmed_by_user"):
                if confirm.pending_title():
                    return ("There is already a confirmation waiting on screen. "
                            "Ask the user to answer that one first.")
                return confirm.request(
                    key="close_app_or_process",
                    title="Close Application",
                    detail=f"Terminate application/process: '{app_target}'",
                    run=lambda: _close_app_or_process(app_target),
                )
            return _close_app_or_process(app_target)


        if action in (
            "open_path",
            "open_folder",
            "explore",
            "open_url",
        ):
            target_path = params.get("path") or params.get("url") or params.get("text", "")
            return _open_path_or_url(target_path)


        if action in (
            "snap_window",
            "snap_left",
            "snap_right",
            "snap_up",
            "snap_down",
        ):
            direction = params.get("direction") or action.replace("snap_", "") or "left"
            return _snap_window(direction)

        if action in (
            "show_desktop",
            "minimize_all",
        ):
            return _show_desktop()


        if action in (
            "batch_actions",
            "macro",
            "sequence",
        ):
            steps = params.get("actions") or params.get("steps") or []
            if isinstance(steps, str):
                try:
                    steps = json.loads(steps)
                except Exception:
                    steps = []
            if not steps or not isinstance(steps, list):
                return "No valid action steps list provided for batch_actions."

            results = []
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                step_action = step.get("action", "")
                sub_res = computer_control(step, player=player)
                results.append(f"Step {i+1} ({step_action}): {sub_res}")
                time.sleep(0.05)
            return "\n".join(results)


        if action == "hover":
            return _hover(
                params.get("x"),
                params.get("y"),
                description=params.get("description") or params.get("text"),
            )

        if action in ("middle_click", "click_middle"):
            return _middle_click(
                params.get("x"),
                params.get("y"),
            )

        if action in ("safe_mouse", "return_mouse_safe"):
            return _return_mouse_safe()


        if action in ("read_screen", "screen_read", "what_is_on_screen"):
            if _MODULES_LOADED:
                st = capture_screen_state(include_ocr=True)
                q = params.get("text") or params.get("query") or params.get("description") or ""
                if q:
                    return answer_screen_question(q, screen_state=st)
                return (
                    f"Active Window: '{st.window_title}' ({st.process_name}).\n"
                    f"Detected {len(st.elements)} interactive UI elements.\n"
                    f"Visible text: \"{st.raw_text[:400].replace(chr(10), ' ')}\""
                )
            return _ocr_read_screen(lang=params.get("lang", "eng"), min_confidence=float(params.get("min_confidence", 30)))

        if action in ("screen_qa", "explain_screen", "screen_question"):
            q = params.get("text") or params.get("query") or params.get("question") or "What is on my screen?"
            if _MODULES_LOADED:
                return answer_screen_question(q)
            return f"Screen question: '{q}'. Active window: {_list_open_windows()}"

        if action in ("find_button", "find_icon", "find_element"):
            target = params.get("description") or params.get("target") or params.get("text") or ""
            if _MODULES_LOADED:
                st = capture_screen_state(include_ocr=True, include_vision=True)
                loc_res = locate_target(target, screen_state=st)
                if loc_res.get("found"):
                    elem = loc_res["element"]
                    return (
                        f"Found '{elem.text}' ({elem.element_type}) in {elem.section} of '{st.window_title}' "
                        f"at coordinates ({loc_res['center'][0]}, {loc_res['center'][1]}), bbox {elem.bbox} via {loc_res['method'].upper()}."
                    )
                return loc_res.get("clarification") or f"Could not locate '{target}' in active window '{st.window_title}'."
            item = _ocr_find(target)
            if item:
                return f"Found '{target}' at ({item['center'][0]}, {item['center'][1]})."
            return f"'{target}' not found."

        if action in ("scroll_and_find", "scroll_and_click"):
            target = params.get("description") or params.get("target") or params.get("text") or ""
            return _execute_smart_click(
                description=target,
                button=params.get("button", "left"),
                clicks=int(params.get("clicks", 1)),
                index=int(params.get("index", 1)),
                verify=params.get("verify", True),
                allow_scroll=True,
            )

        if action in ("select_dropdown", "dropdown_select", "dropdown"):
            dd_name = str(params.get("dropdown") or params.get("target") or params.get("description") or "").strip()
            opt_name = str(params.get("option") or params.get("value") or params.get("text") or "").strip()
            if not dd_name or not opt_name:
                return "Error: select_dropdown requires both 'dropdown' (target dropdown name) and 'option' (option to select) parameters."
            return _execute_dropdown_selection(dd_name, opt_name)


        if action in ("execute_plan", "plan_and_execute", "navigate"):
            inst = params.get("instruction") or params.get("text") or params.get("command") or ""
            if _MODULES_LOADED and inst:
                planner = ActionPlanner(
                    executor_fn=lambda p: computer_control(p, player=player),
                    logger_fn=print,
                    status_fn=lambda s: player.write_log(f"ACT: {s}") if player else None,
                )
                steps = planner.parse_instruction_to_steps(inst)
                res = planner.execute_plan(steps)
                return res.summary
            return "No instruction provided for execute_plan."

        if action in ("stop_automation", "stop"):
            if _MODULES_LOADED:
                planner_request_stop()
            return "Ongoing automation stopped."


        return (
            f"Unknown action: '{action}'"
        )

    except Exception as e:

        print(
            f"[ComputerControl] ❌ {action}: {e}"
        )

        return (
            f"computer_control '{action}' failed: {e}"
        )


TOOL = {

    "name": "computer_control",

    "description": (
        "Complete autonomous system control and desktop navigation engine. "
        "Allows you to control the computer directly: run terminal/powershell commands, "
        "list open windows, list processes, close/kill applications, open files/folders/URLs, "
        "snap windows, minimize all/show desktop, type text, click/double-click/right-click UI elements, "
        "keyboard shortcuts, scroll, screenshots, OCR text finding & clicking, volume/brightness/media "
        "controls, clipboard operations, and execute batch action macros. "
        "IMPORTANT: click with description automatically uses local OCR to find and click buttons/text."
    ),

    "parameters": {

        "type": "OBJECT",

        "properties": {

            "action": {
                "type": "STRING",
                "description": (
                    "read_screen | screen_qa | find_button | find_icon | execute_plan | stop_automation | cancel | "
                    "click | double_click | right_click | middle_click | hover | move | drag | return_mouse_safe | select_item | "
                    "delete_line | next_line | save_document | copy_text | paste_text | rename_file | copy_file | move_file | "
                    "scroll_and_find | select_dropdown | "
                    "run_command | list_windows | list_processes | close_app | open_path | "
                    "snap_window | show_desktop | batch_actions | "
                    "type | smart_type | hotkey | press | scroll | copy | paste | screenshot | wait | clear_field | "
                    "focus_window | select_all | copy_key | cut | paste_key | undo | redo | "
                    "save | find | enter | tab | shift_tab | escape | backspace | delete | space | "
                    "up | down | left | right | home | end | page_up | page_down | new_tab | "
                    "close_tab | reopen_tab | next_tab | previous_tab | ocr_read | ocr_read_fast | "
                    "ocr_find | ocr_click | screen_find | screen_click | volume_up | volume_down | "
                    "volume_mute | mute | brightness_up | brightness_down | minimize | maximize | "
                    "close_window | switch_window | lock_screen | play_pause | next_track | "
                    "previous_track | random_data | user_data"
                ),
            },

            "instruction": {
                "type": "STRING",
                "description": "Multi-step natural language instruction for execute_plan (e.g. 'Open Chrome, search for Java collections, scroll down').",
            },

            "command": {
                "type": "STRING",
                "description": "Terminal / PowerShell / Shell command string to execute for run_command.",
            },

            "app_name": {
                "type": "STRING",
                "description": "Process name or application name for close_app / focus_window.",
            },

            "actions": {
                "type": "ARRAY",
                "description": "List of action objects for batch_actions / macro sequence.",
                "items": {"type": "OBJECT"}
            },

            "cwd": {
                "type": "STRING",
                "description": "Working directory path for run_command.",
            },

            "timeout": {
                "type": "INTEGER",
                "description": "Timeout in seconds for run_command (default 30).",
            },

            "text": {
                "type": "STRING",
                "description": "Text to type, paste, or command string.",
            },

            "x": {
                "type": "INTEGER",
                "description": "X screen coordinate.",
            },

            "y": {
                "type": "INTEGER",
                "description": "Y screen coordinate.",
            },

            "x1": {
                "type": "INTEGER",
                "description": "Drag start X.",
            },

            "y1": {
                "type": "INTEGER",
                "description": "Drag start Y.",
            },

            "x2": {
                "type": "INTEGER",
                "description": "Drag end X.",
            },

            "y2": {
                "type": "INTEGER",
                "description": "Drag end Y.",
            },

            "keys": {
                "type": "STRING",
                "description": "Key combination, for example ctrl+c, alt+tab, win+r, or ctrl+shift+t.",
            },

            "key": {
                "type": "STRING",
                "description": "Single key, for example enter, escape, tab, f5.",
            },

            "direction": {
                "type": "STRING",
                "description": "up | down | left | right | maximize | minimize for scroll/snap_window",
            },

            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount.",
            },

            "seconds": {
                "type": "NUMBER",
                "description": "Seconds to wait.",
            },

            "title": {
                "type": "STRING",
                "description": "Window title for focus_window or close_app.",
            },

            "description": {
                "type": "STRING",
                "description": (
                    "Visible UI text to find on screen. Examples: 'Save', 'Submit', 'Chrome', 'File'. "
                    "For click/double_click/right_click this automatically triggers local OCR."
                ),
            },

            "type": {
                "type": "STRING",
                "description": "Random data type.",
            },

            "field": {
                "type": "STRING",
                "description": "User data field: name, email, city, etc.",
            },

            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing. Default true.",
            },

            "path": {
                "type": "STRING",
                "description": "Path to file/folder or screenshot save path.",
            },

            "url": {
                "type": "STRING",
                "description": "Web URL to open for open_path.",
            },

            "lang": {
                "type": "STRING",
                "description": "OCR language. Normally eng.",
            },

            "min_confidence": {
                "type": "NUMBER",
                "description": "Minimum OCR confidence. Normally 30.",
            },

            "button": {
                "type": "STRING",
                "description": "Mouse button: left or right.",
            },

            "clicks": {
                "type": "INTEGER",
                "description": "Number of clicks.",
            },

            "index": {
                "type": "INTEGER",
                "description": "Candidate match index (1-based, e.g. 1 for first match, 3 for 'third matching result').",
            },

            "verify": {
                "type": "BOOLEAN",
                "description": "Whether to observe and verify the UI state changed after the action (default true).",
            },

            "source": {
                "type": "STRING",
                "description": "Source target or item name for drag operations.",
            },

            "destination": {
                "type": "STRING",
                "description": "Destination target or folder name for drag operations.",
            },

            "target": {
                "type": "STRING",
                "description": "Synonym for description: target element, button label or icon to interact with.",
            },
        },

        "required": [
            "action"
        ],
    },

    "handler": computer_control,
}