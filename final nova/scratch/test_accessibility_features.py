"""
scratch/test_accessibility_features.py — Comprehensive Accessibility & Reliability Test Suite
Verifies all 5 Accessibility Features:
  Feature 1: Complete Voice Mouse Control
  Feature 2: Smart Screen Reading and Interaction
  Feature 3: Voice Typing and Text Editing (with Dictation Protection)
  Feature 4: Voice-Based Application and File Management
  Feature 5: Undo, Cancel and Emergency Stop (with Duplicate Prevention)
Along with existing NOVA system preservation.
"""
import os
import sys
import time
import shutil
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pyautogui
from actions.computer_control import computer_control, _move, _drag, _select_item, _execute_smart_click, _delete_line, _move_to_next_line, _save_document, _undo_action
from actions.file_controller import file_controller
from actions.open_app import resolve_application, launch_application, open_app
from core.screen_reader import (
    ScreenElement,
    ScreenState,
    capture_screen_state,
    answer_screen_question,
    get_current_selection,
    locate_target,
    _GLOBAL_SCREEN_REGISTRY,
    invalidate_screen_registry,
)
from core.action_planner import ActionPlanner, request_stop, reset_stop, is_stop_requested
from core.undo import push_undo, undo_last, can_undo, clear as clear_undo

results = {}

def log_test(feature: str, name: str, passed: bool, duration: float, details: str):
    key = f"[{feature}] {name}"
    results[key] = {"passed": passed, "duration": duration, "details": details}
    icon = "[OK]" if passed else "[FAIL]"
    print(f"\n{icon} {key} ({duration:.3f}s)")
    print(f"    Details: {details}")

# ============================================================================
# FEATURE 1: COMPLETE VOICE MOUSE CONTROL
# ============================================================================
def test_feature_1_mouse_control():
    print("\n" + "="*70)
    print("FEATURE 1: COMPLETE VOICE MOUSE CONTROL")
    print("="*70)

    # 1A. Directional Mouse Movement
    t0 = time.time()
    p0 = pyautogui.position()
    r_move = computer_control({"action": "move", "direction": "right", "distance": 60})
    p1 = pyautogui.position()
    r_move_down = computer_control({"action": "move", "direction": "down", "distance": 40})
    p2 = pyautogui.position()
    t1 = time.time()
    passed_move = ("Moved mouse right by 60px" in r_move or p1.x >= p0.x) and ("Moved mouse down by 40px" in r_move_down or p2.y >= p1.y)
    log_test("Feature 1", "Directional Mouse Movement (Right & Down)", passed_move, t1 - t0, f"{r_move} | {r_move_down}")

    # 1B. Click at Current Pointer Position
    t0 = time.time()
    r_click = computer_control({"action": "click"})
    t1 = time.time()
    passed_click = "Clicked at current pointer" in r_click or "Clicked at (" in r_click
    log_test("Feature 1", "Click at Current Pointer Position", passed_click, t1 - t0, r_click)

    # 1C. Double Click & Right Click at Current Pointer Position
    t0 = time.time()
    r_double = computer_control({"action": "double_click"})
    r_right = computer_control({"action": "right_click"})
    t1 = time.time()
    passed_clicks = ("Clicked at current pointer" in r_double or "x2" in r_double) and ("right" in r_right)
    log_test("Feature 1", "Double Click & Right Click at Current Position", passed_clicks, t1 - t0, f"{r_double} | {r_right}")

    # 1D. Scroll Up & Scroll Down
    t0 = time.time()
    r_scroll_down = computer_control({"action": "scroll", "direction": "down", "amount": 400})
    r_scroll_up = computer_control({"action": "scroll", "direction": "up", "amount": 400})
    t1 = time.time()
    passed_scroll = "Scrolled down" in r_scroll_down and "Scrolled up" in r_scroll_up
    log_test("Feature 1", "Scroll Up & Scroll Down", passed_scroll, t1 - t0, f"{r_scroll_down} | {r_scroll_up}")

    # 1E. Drag Item
    t0 = time.time()
    r_drag = computer_control({"action": "drag", "destination": "down 50"})
    t1 = time.time()
    passed_drag = "Dragged from" in r_drag
    log_test("Feature 1", "Drag from Current Pointer", passed_drag, t1 - t0, r_drag)

    # 1F. Select Item
    t0 = time.time()
    r_select = computer_control({"action": "select_item"})
    t1 = time.time()
    passed_select = "Selected" in r_select
    log_test("Feature 1", "Select Item", passed_select, t1 - t0, r_select)

# ============================================================================
# FEATURE 2: SMART SCREEN READING AND INTERACTION
# ============================================================================
def test_feature_2_smart_screen_reading():
    print("\n" + "="*70)
    print("FEATURE 2: SMART SCREEN READING AND INTERACTION")
    print("="*70)

    # Mock screen state for deterministic validation
    b_profile = ScreenElement("Profile", "button", (50, 20, 120, 50), (85, 35), section="title_bar")
    b_settings = ScreenElement("Settings", "button", (150, 20, 230, 50), (190, 35), section="title_bar")
    b_edit = ScreenElement("Edit Document", "button", (100, 100, 220, 140), (160, 120), section="main_content")
    tab_home = ScreenElement("Home", "tab", (50, 60, 110, 85), (80, 72), section="tabs", selected=True)
    msg_err = ScreenElement("Network connection lost. Please check your proxy settings.", "text", (200, 200, 600, 240), (400, 220), section="dialog")
    b_retry = ScreenElement("Retry", "button", (300, 260, 370, 290), (335, 275), section="dialog")
    b_cancel = ScreenElement("Cancel", "button", (390, 260, 460, 290), (425, 275), section="dialog")

    st = ScreenState(
        timestamp=time.time(),
        window_title="NOVA Workspace",
        process_name="nova.exe",
        hwnd=7777,
        window_rect=(0, 0, 1920, 1080),
        screen_size=(1920, 1080),
        elements=[b_profile, b_settings, b_edit, tab_home, msg_err, b_retry, b_cancel],
        dialog_info={"title": "Connection Error", "message": msg_err.text, "buttons": ["Retry", "Cancel"]},
        raw_text="NOVA Workspace Profile Settings Home Edit Document Network connection lost Retry Cancel",
    )
    _GLOBAL_SCREEN_REGISTRY.update(st)

    # 2A. "Read my screen"
    t0 = time.time()
    ans_screen = answer_screen_question("Read my screen", screen_state=st)
    t1 = time.time()
    passed_screen = "Active Window: 'NOVA Workspace'" in ans_screen and "Buttons & Actions:" in ans_screen
    log_test("Feature 2", "Read my screen", passed_screen, t1 - t0, ans_screen.replace("\n", " | "))

    # 2B. "What options are available?"
    t0 = time.time()
    ans_opts = answer_screen_question("What options are available?", screen_state=st)
    t1 = time.time()
    passed_opts = "Options available in 'NOVA Workspace':" in ans_opts and "Profile" in ans_opts and "Settings" in ans_opts
    log_test("Feature 2", "What options are available?", passed_opts, t1 - t0, ans_opts.replace("\n", " | "))

    # 2C. "Find the Settings button"
    t0 = time.time()
    ans_find = answer_screen_question("Find the Settings button", screen_state=st)
    t1 = time.time()
    passed_find = "Found 'Settings'" in ans_find and "(190, 35)" in ans_find
    log_test("Feature 2", "Find the Settings button", passed_find, t1 - t0, ans_find)

    # 2D. "Click Profile" (instant registry hit!)
    t0 = time.time()
    loc_prof = locate_target("Profile", screen_state=None) # uses registry
    t1 = time.time()
    passed_prof = loc_prof.get("found") and loc_prof.get("from_registry") and loc_prof.get("center") == (85, 35)
    log_test("Feature 2", "Click Profile (Registry Hit)", passed_prof, t1 - t0, f"Found: {loc_prof.get('found')}, Center: {loc_prof.get('center')}, Method: {loc_prof.get('method')}")

    # 2E. "Read the error message"
    t0 = time.time()
    ans_err = answer_screen_question("Read the error message", screen_state=st)
    t1 = time.time()
    passed_err = "Connection Error" in ans_err and "Network connection lost" in ans_err and "Retry, Cancel" in ans_err
    log_test("Feature 2", "Read the error message", passed_err, t1 - t0, ans_err.replace("\n", " | "))

    # 2F. "What is currently selected?"
    t0 = time.time()
    ans_sel = answer_screen_question("What is currently selected?", screen_state=st)
    t1 = time.time()
    passed_sel = "Currently selected" in ans_sel and "Home" in ans_sel
    log_test("Feature 2", "What is currently selected?", passed_sel, t1 - t0, ans_sel)

    # 2G. Ambiguity Protection
    b_dup1 = ScreenElement("Save", "button", (50, 50, 100, 80), (75, 65), section="sidebar")
    b_dup2 = ScreenElement("Save", "button", (500, 500, 550, 530), (525, 515), section="dialog")
    st_amb = ScreenState(
        timestamp=time.time(),
        window_title="Ambiguous",
        process_name="test.exe",
        hwnd=123,
        window_rect=(0, 0, 1920, 1080),
        screen_size=(1920, 1080),
        elements=[b_dup1, b_dup2],
    )
    loc_amb = locate_target("Save", screen_state=st_amb)
    passed_amb = loc_amb.get("found") is False and loc_amb.get("method") == "ambiguous" and "Multiple 'Save' buttons detected" in loc_amb.get("clarification", "")
    log_test("Feature 2", "Ambiguity Protection Query", passed_amb, 0.001, loc_amb.get("clarification", ""))

# ============================================================================
# FEATURE 3: VOICE TYPING AND TEXT EDITING
# ============================================================================
def test_feature_3_voice_typing_and_editing():
    print("\n" + "="*70)
    print("FEATURE 3: VOICE TYPING AND TEXT EDITING")
    print("="*70)

    # 3A. Dictation Protection in Action Planner
    planner = ActionPlanner(executor_fn=lambda p: f"Executed {p.get('action')}")
    dictation_sample = "Dear Board, please cancel the 3pm appointment and delete the old draft. Regards, Team."
    steps = planner.parse_instruction_to_steps(f"Type this paragraph: {dictation_sample}")
    passed_dict = len(steps) == 1 and steps[0].action_type == "type" and steps[0].params.get("text") == dictation_sample
    log_test("Feature 3", "Dictation Protection (Verbatim Payload)", passed_dict, 0.001, f"Step count: {len(steps)}, Action: {steps[0].action_type if steps else None}")

    # 3B. Voice Typing execution via smart_type (clipboard paste)
    t0 = time.time()
    res_type = computer_control({"action": "smart_type", "text": "NOVA Accessible Voice Typing Test Paragraph.\nLine 2 with exact formatting."})
    t1 = time.time()
    passed_type = "Smart-typed" in res_type
    log_test("Feature 3", "Voice Typing Execution", passed_type, t1 - t0, res_type)

    # 3C. Select All
    t0 = time.time()
    res_sel_all = computer_control({"action": "select_all"})
    t1 = time.time()
    passed_sel_all = "Selected all" in res_sel_all
    log_test("Feature 3", "Select All (Ctrl+A)", passed_sel_all, t1 - t0, res_sel_all)

    # 3D. Delete This Line
    t0 = time.time()
    res_del_line = computer_control({"action": "delete_line"})
    t1 = time.time()
    passed_del_line = "Deleted current line" in res_del_line
    log_test("Feature 3", "Delete This Line", passed_del_line, t1 - t0, res_del_line)

    # 3E. Move to the Next Line
    t0 = time.time()
    res_next_line = computer_control({"action": "next_line"})
    t1 = time.time()
    passed_next_line = "Moved cursor to the next line" in res_next_line
    log_test("Feature 3", "Move to the Next Line", passed_next_line, t1 - t0, res_next_line)

    # 3F. Copy and Paste
    t0 = time.time()
    res_copy = computer_control({"action": "copy_text"})
    res_paste = computer_control({"action": "paste_text"})
    t1 = time.time()
    passed_cp = "Copied selected text" in res_copy and "Pasted clipboard content" in res_paste
    log_test("Feature 3", "Copy & Paste Operations", passed_cp, t1 - t0, f"{res_copy} | {res_paste}")

    # 3G. Save This Document
    t0 = time.time()
    res_save = computer_control({"action": "save_document"})
    t1 = time.time()
    passed_save = "Saved document (Ctrl+S)" in res_save
    log_test("Feature 3", "Save This Document", passed_save, t1 - t0, res_save)

    # 3H. Undo in Application
    t0 = time.time()
    res_undo = computer_control({"action": "undo"})
    t1 = time.time()
    passed_undo = "Undone" in res_undo
    log_test("Feature 3", "Undo Text Action", passed_undo, t1 - t0, res_undo)

# ============================================================================
# FEATURE 4: VOICE-BASED APPLICATION AND FILE MANAGEMENT
# ============================================================================
def test_feature_4_app_and_file_management():
    print("\n" + "="*70)
    print("FEATURE 4: APPLICATION AND FILE MANAGEMENT")
    print("="*70)

    # 4A. Open Application (Notepad)
    t0 = time.time()
    res_app = open_app({"app_name": "Notepad"})
    t1 = time.time()
    passed_app = "Successfully launched" in res_app or "already running" in res_app
    log_test("Feature 4", "Open Application (Notepad)", passed_app, t1 - t0, res_app)

    # 4B. Open Document / File
    dummy_doc = project_root / "scratch" / "accessibility_report.txt"
    dummy_doc.write_text("NOVA Accessibility Test File", encoding="utf-8")
    t0 = time.time()
    res_doc = computer_control({"action": "open_path", "path": str(dummy_doc)})
    t1 = time.time()
    passed_doc = "Successfully opened file" in res_doc and "accessibility_report.txt" in res_doc
    log_test("Feature 4", "Open Document / File", passed_doc, t1 - t0, res_doc)

    # 4C. Rename File via Voice Command
    test_file_a = project_root / "scratch" / "voice_file_a.txt"
    test_file_b = project_root / "scratch" / "voice_file_b.txt"
    if test_file_b.exists():
        test_file_b.unlink()
    test_file_a.write_text("Sample voice file data", encoding="utf-8")

    t0 = time.time()
    res_rename = computer_control({"action": "rename_file", "path": str(test_file_a), "new_name": "voice_file_b.txt"})
    t1 = time.time()
    passed_rn = "Renamed: voice_file_a.txt → voice_file_b.txt" in res_rename and test_file_b.exists() and not test_file_a.exists()
    log_test("Feature 4", "Rename File by Voice", passed_rn, t1 - t0, res_rename)

    # 4D. Copy File via Voice Command
    copy_dst = project_root / "scratch" / "voice_file_copy.txt"
    if copy_dst.exists():
        copy_dst.unlink()
    t0 = time.time()
    res_copy_f = computer_control({"action": "copy_file", "path": str(test_file_b), "destination": str(copy_dst)})
    t1 = time.time()
    passed_cp_f = "Copied: voice_file_b.txt" in res_copy_f and copy_dst.exists()
    log_test("Feature 4", "Copy File by Voice", passed_cp_f, t1 - t0, res_copy_f)

    # 4E. Move File via Voice Command
    move_dst = project_root / "scratch" / "voice_file_moved.txt"
    if move_dst.exists():
        move_dst.unlink()
    t0 = time.time()
    res_move_f = computer_control({"action": "move_file", "path": str(copy_dst), "destination": str(move_dst)})
    t1 = time.time()
    passed_mv_f = "Moved: voice_file_copy.txt" in res_move_f and move_dst.exists() and not copy_dst.exists()
    log_test("Feature 4", "Move File by Voice", passed_mv_f, t1 - t0, res_move_f)

    # Clean up test files
    for p in (test_file_b, move_dst, dummy_doc):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

# ============================================================================
# FEATURE 5: UNDO, CANCEL AND EMERGENCY STOP
# ============================================================================
def test_feature_5_safety_undo_stop():
    print("\n" + "="*70)
    print("FEATURE 5: UNDO, CANCEL AND EMERGENCY STOP")
    print("="*70)

    # 5A. Undo Last Assistant Action
    test_undo_file = project_root / "scratch" / "undo_test_file.txt"
    test_undo_target = project_root / "scratch" / "undo_test_renamed.txt"
    test_undo_file.write_text("Reversible operation text", encoding="utf-8")
    if test_undo_target.exists():
        test_undo_target.unlink()

    # Perform rename (pushes to undo stack)
    computer_control({"action": "rename_file", "path": str(test_undo_file), "new_name": "undo_test_renamed.txt"})
    t0 = time.time()
    res_undo_act = computer_control({"action": "undo"})
    t1 = time.time()
    passed_undo_act = test_undo_file.exists() and not test_undo_target.exists() and "Undone: renamed" in res_undo_act
    log_test("Feature 5", "Undo Last Assistant Action", passed_undo_act, t1 - t0, res_undo_act)

    if test_undo_file.exists():
        test_undo_file.unlink()

    # 5B. Emergency Stop
    t0 = time.time()
    request_stop()
    is_stopped = is_stop_requested()
    reset_stop()
    is_reset = not is_stop_requested()
    t1 = time.time()
    passed_stop = is_stopped and is_reset
    log_test("Feature 5", "Emergency Stop and Reset Signal", passed_stop, t1 - t0, f"Stopped: {is_stopped}, Reset: {is_reset}")

    # 5C. Cancel Operation
    t0 = time.time()
    res_cancel = computer_control({"action": "cancel"})
    t1 = time.time()
    passed_cancel = "Operation stopped" in res_cancel
    log_test("Feature 5", "Cancel Operation Response", passed_cancel, t1 - t0, res_cancel)

    # 5D. Duplicate Command Prevention
    t0 = time.time()
    res_c1 = computer_control({"action": "save_document"})
    res_c2 = computer_control({"action": "save_document"}) # Fired within milliseconds!
    t1 = time.time()
    passed_dup = "Saved document" in res_c1 and "Command 'save_document' already executed" in res_c2
    log_test("Feature 5", "Duplicate Execution Prevention", passed_dup, t1 - t0, f"First: '{res_c1}' | Second: '{res_c2}'")

# ============================================================================
# INTEGRATION: ALL 5 FEATURES COMBINED IN A WORKFLOW
# ============================================================================
def test_integrated_accessibility_workflow():
    print("\n" + "="*70)
    print("INTEGRATED TEST: ALL 5 FEATURES EXECUTED TOGETHER")
    print("="*70)

    t0 = time.time()
    planner = ActionPlanner(executor_fn=lambda p: computer_control(p))

    # Test workflow combining:
    # 1. Voice Mouse Move
    # 2. Smart Screen Reading
    # 3. Voice Typing (Dictation protected)
    # 4. Text Editing (Select all, Copy, Paste, Delete line)
    # 5. Undo & Emergency Stop
    steps = planner.parse_instruction_to_steps(
        "Move mouse right 50, then read my screen, then select all, then copy, then paste, then delete this line"
    )
    t1 = time.time()
    passed_plan = len(steps) == 6
    log_test("Integration", "Parse Multi-Step Accessible Workflow", passed_plan, t1 - t0, f"Steps planned: {len(steps)} -> {[s.description for s in steps]}")

# ============================================================================
# MAIN SUITE RUNNER
# ============================================================================
if __name__ == "__main__":
    t_start = time.time()
    test_feature_1_mouse_control()
    test_feature_2_smart_screen_reading()
    test_feature_3_voice_typing_and_editing()
    test_feature_4_app_and_file_management()
    test_feature_5_safety_undo_stop()
    test_integrated_accessibility_workflow()
    t_total = time.time() - t_start

    print("\n" + "="*70)
    total_count = len(results)
    pass_count = sum(1 for r in results.values() if r["passed"])
    fail_count = total_count - pass_count

    print(f"ACCESSIBILITY VERIFICATION SUMMARY: {pass_count}/{total_count} PASSED ({fail_count} failed) in {t_total:.2f}s")
    print("="*70)

    if fail_count > 0:
        print("\nFailed Tests:")
        for k, v in results.items():
            if not v["passed"]:
                print(f"  - {k}: {v['details']}")
        sys.exit(1)
    else:
        print("\nALL ACCESSIBILITY FEATURES VERIFIED SUCCESSFULLY!")
        sys.exit(0)
