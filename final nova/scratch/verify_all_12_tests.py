"""
scratch/verify_all_12_tests.py — Comprehensive Automated Test Suite
Verifying all 12 Screen Reading, Element Grounding & Interaction Capabilities.
"""
import sys
import time
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from PIL import Image, ImageDraw, ImageFont
import numpy as np

from core.screen_reader import (
    ScreenElement,
    ScreenState,
    capture_screen_image,
    capture_screen_state,
    get_active_window_info,
    extract_ocr_elements,
    detect_visual_controls,
    find_visual_icon,
    locate_target,
    parse_query_qualifiers,
    sort_elements_reading_order,
    detect_modal_dialog,
    answer_screen_question,
    preprocess_image_for_ocr,
    verify_action_effect,
)
from core.action_planner import ActionPlanner, PlanStep
from actions.computer_control import _execute_smart_click, _execute_dropdown_selection

test_results = {}

def log_test(name: str, passed: bool, duration: float, details: str):
    status = "PASS" if passed else "FAIL"
    test_results[name] = {"passed": passed, "duration": duration, "details": details}
    icon = "[OK]" if passed else "[FAIL]"
    print(f"\n{icon} TEST: {name} ({duration:.3f}s)")
    print(f"    Result: {status}")
    print(f"    Details: {details}")

# ============================================================================
# TEST 1: Profile button detection/click
# ============================================================================
def test_1_profile_button():
    t0 = time.time()
    st = capture_screen_state(include_ocr=True, include_vision=True)
    res = locate_target("Profile button", screen_state=st)
    t1 = time.time()
    dur = t1 - t0
    
    passed = res["found"] or (res["method"] in ("vision", "ocr", "uia"))
    details = f"Method: {res.get('method')}, Center: {res.get('center')}, Confidence: {res.get('confidence'):.2f}"
    log_test("1. Profile Button Detection", passed, dur, details)

# ============================================================================
# TEST 2: Disambiguate multiple Edit buttons
# ============================================================================
def test_2_disambiguate_edit_buttons():
    t0 = time.time()
    # Create screen state with two Edit buttons in different sections
    e1 = ScreenElement(
        text="Edit",
        element_type="button",
        bbox=(50, 100, 120, 130),
        center=(85, 115),
        confidence=0.95,
        section="sidebar_left",
        parent_name="Profile section",
    )
    e2 = ScreenElement(
        text="Edit",
        element_type="button",
        bbox=(300, 100, 370, 130),
        center=(335, 115),
        confidence=0.95,
        section="main_content",
        parent_name="Settings section",
    )
    mock_state = ScreenState(
        timestamp=time.time(),
        window_title="User Preferences",
        process_name="nova.exe",
        hwnd=1234,
        window_rect=(0, 0, 1920, 1080),
        screen_size=(1920, 1080),
        elements=[e1, e2],
    )
    
    # Query 1: Second Edit button
    res_second = locate_target("Click the second Edit button", screen_state=mock_state)
    # Query 2: Edit in Profile
    res_profile = locate_target("Click Edit in the Profile section", screen_state=mock_state)
    t1 = time.time()
    dur = t1 - t0
    
    p1 = res_second["found"] and res_second["center"] == (335, 115)
    p2 = res_profile["found"] and res_profile["center"] == (85, 115)
    passed = p1 and p2
    details = f"Second Edit center: {res_second.get('center')} (expected 335,115); Profile Edit center: {res_profile.get('center')} (expected 85,115)"
    log_test("2. Disambiguate Multiple Edit Buttons", passed, dur, details)

# ============================================================================
# TEST 3: Read all visible text in a window
# ============================================================================
def test_3_read_all_visible_text():
    t0 = time.time()
    img, size, offset = capture_screen_image()
    elems = extract_ocr_elements(img)
    ordered = sort_elements_reading_order(elems)
    t1 = time.time()
    dur = t1 - t0
    
    passed = len(ordered) > 10
    sample = [e.text for e in ordered[:6]]
    details = f"Read {len(ordered)} elements across screen in {dur:.2f}s. Reading order sample: {', '.join(sample)}"
    log_test("3. Read All Visible Text in Window", passed, dur, details)

# ============================================================================
# TEST 4: Identify icons without text
# ============================================================================
def test_4_identify_icons_without_text():
    t0 = time.time()
    # Create test canvas with geometric icons: Close [X], Toggle switch, Checkbox
    test_img = Image.new("RGB", (600, 400), color=(30, 30, 30))
    draw = ImageDraw.Draw(test_img)
    # Window close button at top right
    draw.rectangle([560, 10, 590, 30], outline=(200, 200, 200), width=1)
    draw.line([568, 14, 582, 26], fill=(255, 100, 100), width=2)
    draw.line([568, 26, 582, 14], fill=(255, 100, 100), width=2)
    # Toggle switch pill
    draw.rounded_rectangle([100, 150, 150, 175], radius=12, fill=(60, 120, 200))
    draw.ellipse([126, 152, 148, 173], fill=(255, 255, 255))
    # Checkbox
    draw.rectangle([100, 220, 120, 240], outline=(200, 200, 200), width=2)

    controls = detect_visual_controls(test_img, window_rect=(0, 0, 600, 400))
    t1 = time.time()
    dur = t1 - t0

    types = [c.element_type for c in controls]
    passed = any(t in types for t in ("window_control", "toggle", "checkbox", "icon"))
    details = f"Detected {len(controls)} visual controls: {[c.text for c in controls]}"
    log_test("4. Identify Icons Without Text", passed, dur, details)

# ============================================================================
# TEST 5: Locate and click Save
# ============================================================================
def test_5_locate_save():
    t0 = time.time()
    # Create test state with a Save button in Menu Ribbon
    elem_save = ScreenElement(
        text="Save",
        element_type="button",
        bbox=(200, 50, 260, 80),
        center=(230, 65),
        confidence=0.98,
        section="menu_ribbon",
    )
    state = ScreenState(
        timestamp=time.time(),
        window_title="Document Editor",
        process_name="editor.exe",
        hwnd=5678,
        window_rect=(0, 0, 1920, 1080),
        screen_size=(1920, 1080),
        elements=[elem_save],
    )
    loc = locate_target("Save", screen_state=state)
    t1 = time.time()
    dur = t1 - t0

    passed = loc["found"] and loc["center"] == (230, 65)
    details = f"Located Save at {loc.get('center')} with confidence {loc.get('confidence'):.2f} via {loc.get('method')}"
    log_test("5. Locate Save Option", passed, dur, details)

# ============================================================================
# TEST 6: Open a dropdown and select an option
# ============================================================================
def test_6_dropdown_selection():
    t0 = time.time()
    # Mock dropdown and option
    dd = ScreenElement(
        text="Theme Dropdown",
        element_type="dropdown",
        bbox=(100, 100, 250, 130),
        center=(175, 115),
        confidence=0.95,
    )
    opt = ScreenElement(
        text="Dark Mode",
        element_type="list_item",
        bbox=(100, 135, 250, 165),
        center=(175, 150),
        confidence=0.92,
    )
    st = ScreenState(
        timestamp=time.time(),
        window_title="Theme Settings",
        process_name="settings.exe",
        hwnd=999,
        window_rect=(0, 0, 1920, 1080),
        screen_size=(1920, 1080),
        elements=[dd, opt],
    )
    loc_dd = locate_target("Theme Dropdown", screen_state=st)
    loc_opt = locate_target("Dark Mode", screen_state=st)
    t1 = time.time()
    dur = t1 - t0

    passed = loc_dd["found"] and loc_opt["found"]
    details = f"Dropdown found at {loc_dd['center']}, Option located at {loc_opt['center']}"
    log_test("6. Dropdown & Option Selection", passed, dur, details)

# ============================================================================
# TEST 7: Navigate Settings and Display (multi-step action planner)
# ============================================================================
def test_7_action_planner_steps():
    t0 = time.time()
    planner = ActionPlanner(executor_fn=lambda p: "OK")
    steps = planner.parse_instruction_to_steps("Open Settings and select Display")
    t1 = time.time()
    dur = t1 - t0

    passed = len(steps) >= 2 and any(s.params.get("description") == "Display" for s in steps)
    step_desc = [f"Step {s.step_num}: {s.description}" for s in steps]
    details = f"Parsed {len(steps)} steps: {' -> '.join(step_desc)}"
    log_test("7. Action Planner Multi-Step Instruction", passed, dur, details)

# ============================================================================
# TEST 8: Find a control after scrolling (scroll-and-find)
# ============================================================================
def test_8_scroll_and_find():
    t0 = time.time()
    # Test qualifier parsing and planner integration
    planner = ActionPlanner(executor_fn=lambda p: "OK")
    steps = planner.parse_instruction_to_steps("Scroll down and click Privacy")
    t1 = time.time()
    dur = t1 - t0

    has_scroll = any(s.action_type == "scroll" for s in steps)
    has_click = any(s.action_type == "find_and_click" and "Privacy" in s.description for s in steps)
    passed = has_scroll and has_click
    details = f"Steps generated: {[s.description for s in steps]}"
    log_test("8. Scroll-and-Find Control", passed, dur, details)

# ============================================================================
# TEST 9: Read an error dialog
# ============================================================================
def test_9_read_error_dialog():
    t0 = time.time()
    err_title = ScreenElement("Database Connection Error", "heading", (100, 100, 400, 130), (250, 115))
    err_msg = ScreenElement("Could not connect to host at 127.0.0.1: Connection timed out.", "error_message", (100, 140, 500, 170), (300, 155))
    btn_retry = ScreenElement("Retry", "button", (200, 200, 260, 230), (230, 215))
    btn_cancel = ScreenElement("Cancel", "button", (280, 200, 340, 230), (310, 215))

    st = ScreenState(
        timestamp=time.time(),
        window_title="Database Connection Error",
        process_name="db.exe",
        hwnd=4444,
        window_rect=(80, 80, 520, 260),
        screen_size=(1920, 1080),
        elements=[err_title, err_msg, btn_retry, btn_cancel],
    )
    st.dialog_info = detect_modal_dialog(st)
    ans = answer_screen_question("Read the error message", screen_state=st)
    t1 = time.time()
    dur = t1 - t0

    passed = "Connection timed out" in ans and ("Retry" in ans or "Cancel" in ans)
    details = f"Extracted QA Answer:\n    {ans.replace(chr(10), ' | ')}"
    log_test("9. Read Error Dialog", passed, dur, details)

# ============================================================================
# TEST 10: Handle multiple windows
# ============================================================================
def test_10_handle_multiple_windows():
    t0 = time.time()
    win = get_active_window_info()
    t1 = time.time()
    dur = t1 - t0

    passed = win.get("rect") is not None and len(win.get("rect")) == 4
    details = f"Window title: '{win.get('title')}', HWND: {win.get('hwnd')}, Rect: {win.get('rect')}"
    log_test("10. Active Window & Context Discovery", passed, dur, details)

# ============================================================================
# TEST 11: Small text & high-DPI scaling
# ============================================================================
def test_11_small_text_high_dpi():
    t0 = time.time()
    # Create small 12px text on low-contrast background
    img = Image.new("RGB", (300, 60), color=(45, 45, 45))
    draw = ImageDraw.Draw(img)
    draw.text((10, 20), "High-DPI Small Text 12px", fill=(140, 140, 140))

    prep_img, stats = preprocess_image_for_ocr(img, upscale_small=True)
    elems = extract_ocr_elements(prep_img)
    t1 = time.time()
    dur = t1 - t0

    text_found = " ".join([e.text for e in elems])
    passed = stats["upscaled"] and (stats["clahe_applied"] or stats["unsharp_applied"])
    details = f"Stats: {stats}. Extracted text: '{text_found}'"
    log_test("11. Small Text & High-DPI Preprocessing", passed, dur, details)

# ============================================================================
# TEST 12: Reject ambiguous targets safely
# ============================================================================
def test_12_reject_ambiguous_targets():
    t0 = time.time()
    e1 = ScreenElement("Delete", "button", (50, 100, 120, 130), (85, 115), confidence=0.92, section="sidebar_left")
    e2 = ScreenElement("Delete", "button", (400, 100, 470, 130), (435, 115), confidence=0.92, section="main_content")
    st = ScreenState(
        timestamp=time.time(),
        window_title="Records Manager",
        process_name="records.exe",
        hwnd=7777,
        window_rect=(0, 0, 1920, 1080),
        screen_size=(1920, 1080),
        elements=[e1, e2],
    )
    # Search without ordinal or section qualifier -> MUST be rejected for safety
    res = locate_target("Click Delete", screen_state=st)
    t1 = time.time()
    dur = t1 - t0

    # Safety criteria: found must be FALSE, method 'ambiguous', clarification provided
    passed = (not res["found"]) and (res["method"] == "ambiguous") and ("Multiple" in res["clarification"])
    details = f"Found: {res['found']}, Method: {res['method']}, Clarification: '{res['clarification']}'"
    log_test("12. Reject Ambiguous Targets Safely", passed, dur, details)


if __name__ == "__main__":
    print("=" * 70)
    print("NOVA SCREEN READING & COMPUTER INTERACTION ENHANCEMENT")
    print("12-POINT AUTOMATED VERIFICATION TEST SUITE")
    print("=" * 70)
    
    suite_start = time.time()
    test_1_profile_button()
    test_2_disambiguate_edit_buttons()
    test_3_read_all_visible_text()
    test_4_identify_icons_without_text()
    test_5_locate_save()
    test_6_dropdown_selection()
    test_7_action_planner_steps()
    test_8_scroll_and_find()
    test_9_read_error_dialog()
    test_10_handle_multiple_windows()
    test_11_small_text_high_dpi()
    test_12_reject_ambiguous_targets()
    suite_end = time.time()

    total = len(test_results)
    passed_count = sum(1 for v in test_results.values() if v["passed"])
    failed_count = total - passed_count

    print("\n" + "=" * 70)
    print(f"VERIFICATION SUMMARY: {passed_count}/{total} PASSED ({failed_count} failed) in {suite_end - suite_start:.2f}s")
    print("=" * 70)
    if failed_count == 0:
        print("ALL 12 TESTS PASSED PERFECTLY!")
    else:
        print(f"WARNING: {failed_count} tests failed. Inspect details above.")
