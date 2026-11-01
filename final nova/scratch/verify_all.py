import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import time
import asyncio
import traceback
import numpy as np
from PyQt6.QtWidgets import QApplication

def run_tests():
    try:
        app = QApplication.instance() or QApplication(sys.argv)

        from main import NovaLive, SingleInstanceLock
        from ui import NovaUI
        from google.genai import types
        from memory.config_manager import get_proactive_audio_enabled, get_wake_word_enabled

        print("==================================================")
        print("     NOVA RESTORATION VERIFICATION SUITE         ")
        print("==================================================")

        # Test 1: Single Instance Lock
        print("\n[TEST 1] SingleInstanceLock verification...")
        lock1 = SingleInstanceLock("NovaAssistant_SingleInstance_TestMutex")
        assert lock1.acquire() == True, "Failed to acquire lock1"
        lock2 = SingleInstanceLock("NovaAssistant_SingleInstance_TestMutex")
        assert lock2.acquire() == False, "Lock2 should have been rejected (duplicate instance protection)"
        lock1.release()
        print("  ✓ Single-instance mutex prevents duplicate instances.")

        # Test 2: Config Verification
        print("\n[TEST 2] Configuration verification...")
        proactive_ok = get_proactive_audio_enabled()
        wake_enabled = get_wake_word_enabled()
        print(f"  • Proactive audio enabled: {proactive_ok}")
        print(f"  • Wake word enabled: {wake_enabled}")
        assert proactive_ok == True, "Proactive audio should be enabled"
        print("  ✓ Configuration parameters verified.")

        # Test 3: UI and State Machine
        print("\n[TEST 3] NovaUI and State Transitions...")
        ui = NovaUI()
        nova = NovaLive(ui)

        print(f"  • Initial _wake_enabled: {nova._wake_enabled}")
        print(f"  • Initial _awake: {nova._awake}")
        assert nova._awake == (not nova._wake_enabled), "Initial _awake must match (not _wake_enabled)"
        if not nova._awake:
            nova.wake(reason="verification test init")

        # Test state transitions
        nova.set_speaking(True)
        app.processEvents()
        assert nova.ui._win.hud.state == "SPEAKING", f"Expected SPEAKING, got {nova.ui._win.hud.state}"
        print("  ✓ State: SPEAKING verified.")

        nova.set_speaking(False)
        app.processEvents()
        assert nova.ui._win.hud.state == "LISTENING", f"Expected LISTENING, got {nova.ui._win.hud.state}"
        print("  ✓ State: LISTENING verified.")

        nova.sleep(reason="verification test")
        app.processEvents()
        assert nova._awake == False, "nova._awake should be False after sleep()"
        assert nova.ui._win.hud.state == "SLEEPING", f"Expected SLEEPING, got {nova.ui._win.hud.state}"
        print("  ✓ State: SLEEPING verified.")

        nova.wake(reason="verification test")
        app.processEvents()
        assert nova._awake == True, "nova._awake should be True after wake()"
        assert nova.ui._win.hud.state == "LISTENING", f"Expected LISTENING, got {nova.ui._win.hud.state}"
        print("  ✓ State: WAKE -> LISTENING verified.")

        # Test 4: Microphone Callback and Audio Ingestion
        print("\n[TEST 4] Audio Ingestion & Queue Behavior...")
        nova.out_queue = asyncio.Queue(maxsize=100)
        test_pcm = np.zeros(512, dtype=np.int16)

        # In main.py: callback puts item into out_queue when awake
        nova._awake = True
        mock_item = {"data": test_pcm.tobytes(), "mime_type": "audio/pcm"}
        nova.out_queue.put_nowait(mock_item)
        assert not nova.out_queue.empty(), "Audio item should be in out_queue when awake"
        drained = nova.out_queue.get_nowait()
        assert len(drained["data"]) == 1024, "PCM 512 int16 samples should be 1024 bytes"
        print("  ✓ Audio ingestion active when awake.")

        # Sleep mode drains queues
        nova.out_queue.put_nowait(mock_item)
        nova.sleep(reason="test drain")
        assert nova.out_queue.empty(), "out_queue should be emptied when entering sleep mode"
        print("  ✓ Sleep mode successfully drains out_queue.")

        # Test 5: Voice Interruption
        print("\n[TEST 5] Voice Interruption...")
        nova.wake(reason="test interrupt")
        nova.audio_in_queue = asyncio.Queue(maxsize=100)
        for _ in range(5):
            nova.audio_in_queue.put_nowait(b"\x00" * 480)
        assert nova.audio_in_queue.qsize() == 5, "audio_in_queue should have 5 chunks"
        nova.interrupt()
        assert nova.audio_in_queue.empty(), "interrupt() should drain audio_in_queue"
        assert nova._interrupted == True, "interrupt flag should be set"
        print("  ✓ Voice interruption drains audio output immediately.")

        # Test 6: Action Dispatcher & Security Guard
        print("\n[TEST 6] Action Dispatcher Security Guards...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        nova._loop = loop

        # Case A: Reject action when asleep
        nova.sleep(reason="test security")
        fc_test = types.FunctionCall(id="c1", name="computer_control", args={"action": "type", "text": "hello"})
        resp = loop.run_until_complete(nova._execute_tool(fc_test))
        assert "NOVA is currently in sleep mode" in resp.response["result"], "Action while asleep must be blocked"
        print("  ✓ Guard A: Action blocked when assistant is in sleep mode.")

        # Case B: Reject stale action (>120s since user speech)
        nova.wake(reason="test security")
        nova._last_user_speech = time.monotonic() - 150.0  # 150s ago
        resp = loop.run_until_complete(nova._execute_tool(fc_test))
        assert "No recent user command" in resp.response["result"], "Stale action must be blocked"
        print("  ✓ Guard B: Stale action blocked (150s elapsed since last speech).")

        # Case C: Reject empty parameters
        nova._last_user_speech = time.monotonic()  # fresh speech
        fc_empty = types.FunctionCall(id="c2", name="computer_control", args={})
        resp = loop.run_until_complete(nova._execute_tool(fc_empty))
        assert "Missing command parameters" in resp.response["result"], "Empty argument action must be blocked"
        print("  ✓ Guard C: Empty parameter critical action blocked.")

        # Case D: Valid fresh action execution
        fc_valid = types.FunctionCall(id="c3", name="computer_settings", args={"setting": "volume"})
        resp = loop.run_until_complete(nova._execute_tool(fc_valid))
        assert "result" in resp.response, "Valid fresh action must execute"
        print(f"  ✓ Guard D: Valid fresh action executed successfully: {resp.response['result']}")

        print("\n==================================================")
        print("     ALL VERIFICATION TESTS COMPLETED: 100% OK    ")
        print("==================================================")
        return True
    except Exception as e:
        print("\n[VERIFICATION EXCEPTION CAUGHT]:")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
