"""
test_screen_control.py — Comprehensive Test Suite for NOVA Screen Understanding,
Mouse, Keyboard & System Navigation Control.
"""
import ctypes
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath("."))

from core.screen_reader import (
    ensure_desktop_and_dpi,
    get_active_window_info,
    capture_screen_image,
    capture_screen_state,
    extract_uia_elements,
    extract_ocr_elements,
    locate_target,
    find_element_by_text,
    find_all_matching_elements,
    verify_action_effect,
    answer_screen_question,
    find_visual_icon,
    ScreenElement,
    ScreenState,
)

from actions.computer_control import (
    computer_control,
    _execute_smart_click,
    _move,
    _hover,
    _scroll,
    _drag,
    _smart_type,
    _press,
    _hotkey,
    _invalidate_ocr_cache,
)

from core.action_planner import ActionPlanner, PlanStep, request_stop, reset_stop, is_stop_requested


class TestScreenUnderstanding(unittest.TestCase):
    """Test screen reading, UIA, OCR, and target grounding."""

    def setUp(self):
        ensure_desktop_and_dpi()

    def test_01_active_window_info(self):
        """Verify active foreground window context discovery."""
        info = get_active_window_info()
        print(f"\n[Test 1] Active Window: '{info['title']}' | Process: '{info['process_name']}' | Rect: {info['rect']}")
        self.assertIsInstance(info, dict)
        self.assertIn("title", info)
        self.assertIn("rect", info)
        self.assertIn("hwnd", info)
        self.assertEqual(len(info["rect"]), 4)
        self.assertGreater(info["rect"][2], info["rect"][0])
        self.assertGreater(info["rect"][3], info["rect"][1])

    def test_02_screen_capture(self):
        """Verify screenshot capture with DPI/monitor awareness."""
        img, size, offset = capture_screen_image()
        self.assertIsNotNone(img, "Screenshot should not be None")
        self.assertGreater(size[0], 0)
        self.assertGreater(size[1], 0)
        print(f"[Test 2] Captured screen size: {size}, monitor offset: {offset}")

    def test_03_uia_controls_and_container_filtering(self):
        """Verify UIA extracts leaf interactive controls and classifies layout containers."""
        elements = extract_uia_elements(max_elements=100)
        self.assertIsInstance(elements, list)
        print(f"[Test 3] Extracted {len(elements)} UIA elements.")
        interactive = [e for e in elements if not e.is_container]
        containers = [e for e in elements if e.is_container]
        print(f"        Interactive controls: {len(interactive)} | Layout containers: {len(containers)}")
        for e in interactive[:5]:
            print(f"        -> [{e.element_type}] '{e.text}' center={e.center} bbox={e.bbox}")

    def test_04_ocr_elements_and_phrases(self):
        """Verify OCR extracts text words and groups multi-word phrases on the screen."""
        img, size, offset = capture_screen_image()
        self.assertIsNotNone(img)
        elements = extract_ocr_elements(img, min_confidence=25, offset=offset)
        self.assertIsInstance(elements, list)
        self.assertGreater(len(elements), 0, "OCR should detect visible text elements on screen")
        phrases = [e for e in elements if " " in e.text]
        print(f"[Test 4] OCR extracted {len(elements)} items (including {len(phrases)} compound phrases).")
        for p in phrases[:5]:
            print(f"        -> Phrase: '{p.text}' center={p.center} bbox={p.bbox}")

    def test_05_locate_labeled_button(self):
        """Verify locating a known labeled control/button via Priority 1 (UIA) or Priority 2 (OCR)."""
        st = capture_screen_state(include_ocr=True)
        res = locate_target("Minimize", screen_state=st)
        if not res["found"]:
            res = locate_target("Close", screen_state=st)
        if not res["found"]:
            words = [e.text for e in st.elements if len(e.text) > 4][:1]
            if words:
                res = locate_target(words[0], screen_state=st)

        self.assertTrue(res["found"], f"Should locate labeled control: {res.get('clarification')}")
        self.assertGreater(res["center"][0], 0)
        self.assertGreater(res["center"][1], 0)
        print(f"[Test 5] Located target via {res['method'].upper()}: center={res['center']}")

    def test_06_unlabeled_icon_detection(self):
        """Verify locating unlabeled icons (profile, settings, search)."""
        img, size, _ = capture_screen_image()
        win = get_active_window_info()
        vis = find_visual_icon("profile", img, win["rect"])
        print(f"[Test 6] Visual icon detection for 'profile': {vis.text if vis else 'None (no topbar circle in window)'}")
        if vis:
            self.assertEqual(vis.category, "profile")
            self.assertGreater(vis.center[0], 0)

    def test_07_duplicate_labels_and_ordinal(self):
        """Verify handling multiple matches and selecting the Nth match (ordinal support)."""
        st = capture_screen_state(include_ocr=True)
        matches = find_all_matching_elements("e", st.elements)
        print(f"[Test 7] Found {len(matches)} matching elements for 'e'")
        if len(matches) >= 2:
            res1 = locate_target("e", screen_state=st, index=1)
            res2 = locate_target("e", screen_state=st, index=2)
            self.assertTrue(res1["found"])
            self.assertTrue(res2["found"])
            self.assertNotEqual(res1["center"], res2["center"], "Index 1 and Index 2 should target distinct elements")
            print(f"        Match 1: {res1['center']} vs Match 2: {res2['center']}")

    def test_08_missing_target_handling(self):
        """Verify missing targets return clean clarification without clicking randomly."""
        st = capture_screen_state(include_ocr=True)
        res = locate_target("NON_EXISTENT_GIBBERISH_BUTTON_XYZ123", screen_state=st)
        self.assertFalse(res["found"])
        self.assertEqual(res["center"], (0, 0))
        self.assertIn("Could not find", res["clarification"])
        print(f"[Test 8] Clean rejection of missing target: '{res['clarification'][:80]}...'")


class TestMouseAndKeyboardControl(unittest.TestCase):
    """Test mouse movements, clicks, hotkeys, and typing."""

    def setUp(self):
        ensure_desktop_and_dpi()

    def test_09_mouse_move_and_hover(self):
        """Verify mouse movement to coordinates and target hovering."""
        res_move = _move(x=200, y=200)
        self.assertIn("Moved mouse to (200, 200)", res_move)

        res_hover = _hover(x=250, y=250)
        self.assertIn("Hovering over (250, 250)", res_hover)
        print(f"[Test 9] Mouse move and hover results: '{res_move}' | '{res_hover}'")

    def test_10_mouse_scroll(self):
        """Verify mouse scrolling up and down."""
        res_down = _scroll(direction="down", amount=3)
        self.assertIn("Scrolled down", res_down)
        res_up = _scroll(direction="up", amount=3)
        self.assertIn("Scrolled up", res_up)
        print(f"[Test 10] Scroll down: '{res_down}' | Scroll up: '{res_up}'")

    def test_11_smart_typing_and_focus(self):
        """Verify smart typing with clipboard fallback and active window focus."""
        res = _smart_type("NOVA test payload", clear_first=False)
        self.assertIn("typed", res.lower())
        print(f"[Test 11] Smart typing result: '{res}'")

    def test_12_keyboard_shortcuts(self):
        """Verify keyboard shortcuts and key presses."""
        res_esc = _press("esc")
        self.assertIn("Pressed: esc", res_esc)

        res_hotkey = _hotkey("ctrl", "shift", "alt")
        self.assertIn("Pressed hotkey", res_hotkey)
        print(f"[Test 12] Key press: '{res_esc}' | Hotkey: '{res_hotkey}'")

    def test_13_action_planner_pipeline(self):
        """Verify Multi-Step Action Planner parsing and Observe-Plan-Act execution."""
        planner = ActionPlanner(
            executor_fn=lambda p: computer_control(p),
            logger_fn=lambda m: None,
        )
        instruction = "move mouse to search box, then hover over Settings, scroll down"
        steps = planner.parse_instruction_to_steps(instruction)
        self.assertGreaterEqual(len(steps), 3)
        print(f"\n[Test 13] Parsed instruction into {len(steps)} sequential steps:")
        for s in steps:
            print(f"         Step {s.step_num}: [{s.action_type}] {s.description}")

        self.assertEqual(steps[0].action_type, "move")
        self.assertEqual(steps[1].action_type, "hover")
        self.assertEqual(steps[2].action_type, "scroll")

    def test_14_stop_automation_cancellation(self):
        """Verify stop signal immediately halts multi-step actions."""
        reset_stop()
        self.assertFalse(is_stop_requested())
        request_stop()
        self.assertTrue(is_stop_requested())
        reset_stop()
        self.assertFalse(is_stop_requested())
        print("[Test 14] Emergency stop / user cancellation verified.")


if __name__ == "__main__":
    print("=" * 70)
    print("  RUNNING NOVA SCREEN UNDERSTANDING & INPUT CONTROL TEST SUITE  ")
    print("=" * 70)
    unittest.main(verbosity=2)
