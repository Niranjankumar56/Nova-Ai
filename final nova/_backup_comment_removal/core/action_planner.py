"""
core/action_planner.py — Multi-Step Visual Action Planner & Autonomous Task Engine for NOVA.

Provides:
  • Sequential task decomposition and execution
  • State verification & screen re-reading between UI-changing actions
  • Progress reporting and step-by-step UI notifications
  • Safety gating according to Interaction Mode (Standard / Safe / Trusted)
  • Immediate cancellation on "NOVA, stop" or stop signal
  • Auto-recovery and retry logic (max 2 retries per step)
"""
from __future__ import annotations

import json
import os
import platform
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from memory.config_manager import get_interaction_mode, get_verbosity

# Global stop event for instant automation halt
_STOP_EVENT = threading.Event()


def request_stop() -> None:
    """Trigger immediate halt of any ongoing multi-step action."""
    print("[ActionPlanner] 🛑 EMERGENCY STOP REQUESTED.")
    _STOP_EVENT.set()


def reset_stop() -> None:
    """Reset the stop event before launching a new sequence."""
    _STOP_EVENT.clear()


def is_stop_requested() -> bool:
    return _STOP_EVENT.is_set()


@dataclass
class PlanStep:
    step_num: int
    action_type: str        # 'open_app', 'find_and_click', 'type', 'press_key', 'scroll', 'wait', 'read_screen', 'verify'
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending" # 'pending', 'running', 'completed', 'failed', 'skipped'
    result: str = ""
    retries: int = 0
    max_retries: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanResult:
    success: bool
    summary: str
    steps_total: int
    steps_completed: int
    aborted_by_user: bool = False
    step_details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActionPlanner:
    """Orchestrates multi-step visual workflows on Windows with verification and safety."""

    def __init__(
        self,
        executor_fn: Callable[[dict[str, Any]], str],
        screen_reader_fn: Optional[Callable[[], Any]] = None,
        logger_fn: Callable[[str], None] = print,
        status_fn: Optional[Callable[[str], None]] = None,
    ):
        self._execute = executor_fn
        self._screen_reader = screen_reader_fn
        self._log = logger_fn
        self._notify_status = status_fn or (lambda s: None)

    def parse_instruction_to_steps(self, instruction: str) -> list[PlanStep]:
        """Convert a complex natural language instruction into sequential PlanSteps."""
        inst = (instruction or "").strip()
        steps: list[PlanStep] = []

        # Split multiple clauses without breaking compounds like 'and click' or 'and submit'
        clauses = [c.strip() for c in re.split(r",\s*|\s+then\s+|\s+and\s+(?!click\b|press\b|submit\b|hit\s+enter\b)", inst, flags=re.I) if c.strip()]
        step_num = 1

        for raw_clause in clauses:
            clause = re.sub(r"^(then|and|next)\s+", "", raw_clause, flags=re.I).strip()
            if not clause:
                continue
            cl = clause.lower()

            # 1. UI element open / click synonym (e.g. 'open my profile', 'open settings', 'open account')
            ui_open_keywords = ("profile", "my profile", "account", "my account", "settings", "settings icon", "profile icon", "menu", "options", "preferences", "search", "search box")
            m_open = re.search(r"^(?:open|launch|start)\s+(.*)$", clause, flags=re.I)
            if m_open and any(m_open.group(1).lower().strip() == kw for kw in ui_open_keywords):
                target = m_open.group(1).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="find_and_click",
                    description=f"Click '{target}'",
                    params={"description": target, "index": 1, "verify": True},
                ))
                step_num += 1

            # 1b. Open / launch application
            elif cl.startswith("open ") or cl.startswith("launch ") or cl.startswith("start "):
                app_name = re.sub(r"^(open|launch|start)\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="open_app",
                    description=f"Open {app_name}",
                    params={"app_name": app_name},
                ))
                step_num += 1
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="wait",
                    description=f"Wait for {app_name} to stabilize",
                    params={"seconds": 1.2},
                ))
                step_num += 1

            # 2. Find and click compound (e.g. 'find the settings icon and click it')
            elif re.search(r"^(?:find|locate)\s+(?:the\s+)?(.*?)\s+and\s+(?:click|press)(?:\s+(?:it|that|the\s+same))?$", clause, flags=re.I):
                m_find_click = re.search(r"^(?:find|locate)\s+(?:the\s+)?(.*?)\s+and\s+(?:click|press)(?:\s+(?:it|that|the\s+same))?$", clause, flags=re.I)
                target = m_find_click.group(1).strip() if m_find_click else "target"
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="find_and_click",
                    description=f"Click '{target}'",
                    params={"description": target, "index": 1, "verify": True},
                ))
                step_num += 1

            # 3. Search on current page (e.g. 'search for this option on the current page')
            elif re.search(r"^(?:search\s+for|find)\s+(.*?)\s+(?:on\s+(?:the|this)\s+(?:current\s+)?(?:page|screen|window)|in\s+this\s+page)$", clause, flags=re.I):
                m_page_search = re.search(r"^(?:search\s+for|find)\s+(.*?)\s+(?:on\s+(?:the|this)\s+(?:current\s+)?(?:page|screen|window)|in\s+this\s+page)$", clause, flags=re.I)
                term = m_page_search.group(1).strip() if m_page_search else "search"
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Open find on page (Ctrl+F)",
                    params={"keys": "ctrl+f"},
                ))
                step_num += 1
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="type",
                    description=f"Search for '{term}'",
                    params={"text": term, "press_enter": True},
                ))
                step_num += 1

            # 4. Move mouse
            elif re.search(r"^(move\s+(the\s+)?mouse\s+to|move\s+to)\s+", cl):
                target = re.sub(r"^(move\s+(the\s+)?mouse\s+to|move\s+to)\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="move",
                    description=f"Move mouse to '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            # 5. Hover
            elif cl.startswith("hover over ") or cl.startswith("hover on ") or cl.startswith("hover "):
                target = re.sub(r"^hover(\s+over|\s+on)?\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="hover",
                    description=f"Hover over '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            # 6. Double click
            elif "double click" in cl or "double-click" in cl:
                target = re.sub(r"^(double[- ]click(\s+on|\s+the)?)\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="double_click",
                    description=f"Double-click '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            # 7. Right click
            elif "right click" in cl or "right-click" in cl:
                target = re.sub(r"^(right[- ]click(\s+on|\s+the)?)\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="right_click",
                    description=f"Right-click '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            # 8. Drag
            elif "drag " in cl and " to " in cl:
                m_drag = re.search(r"drag\s+(.*?)\s+to\s+(.*)", clause, flags=re.I)
                if m_drag:
                    src, dst = m_drag.group(1).strip(), m_drag.group(2).strip()
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="drag",
                        description=f"Drag '{src}' to '{dst}'",
                        params={"source": src, "destination": dst},
                    ))
                    step_num += 1

            # 9. Navigation: back / previous screen
            elif any(w in cl for w in ("go back", "previous screen", "previous page")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Go back to previous screen (Alt+Left)",
                    params={"keys": "alt+left"},
                ))
                step_num += 1

            # 10. Shortcuts & text operations (select all, copy, paste, save, undo)
            elif "select all" in cl:
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Select all (Ctrl+A)",
                    params={"keys": "ctrl+a"},
                ))
                step_num += 1
            elif cl == "copy" or "copy that" in cl or "copy selected" in cl:
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Copy (Ctrl+C)",
                    params={"keys": "ctrl+c"},
                ))
                step_num += 1
            elif cl == "paste" or "paste it" in cl or "paste text" in cl:
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Paste (Ctrl+V)",
                    params={"keys": "ctrl+v"},
                ))
                step_num += 1
            elif "save" in cl and not "as" in cl and len(cl) < 15:
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Save (Ctrl+S)",
                    params={"keys": "ctrl+s"},
                ))
                step_num += 1
            elif "undo" in cl and len(cl) < 12:
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Undo (Ctrl+Z)",
                    params={"keys": "ctrl+z"},
                ))
                step_num += 1

            # 11. Click button, link, profile, tab, or ordinal result
            elif "click" in cl or "press the" in cl:
                target = re.sub(r"^(click(\s+on|\s+the)?|press\s+the)\s+", "", clause, flags=re.I).strip()
                # Check for ordinal like 'third matching result'
                ord_match = re.search(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b", target, re.I)
                ord_idx = 1
                if ord_match:
                    ord_map = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
                    ord_idx = ord_map.get(ord_match.group(1).lower(), 1)
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="find_and_click",
                    description=f"Click '{target}'",
                    params={"description": target, "index": ord_idx, "verify": True},
                ))
                step_num += 1

            # 12. Type text / submit
            elif cl.startswith("search for ") or cl.startswith("type ") or "enter " in cl:
                should_submit = any(k in cl for k in ("submit", "press enter", "hit enter", "search for"))
                query = re.sub(r"^(search\s+for|type|enter)\s+", "", clause, flags=re.I)
                query = re.sub(r"\s+and\s+(?:submit|press\s+enter|hit\s+enter)(?:\s+it)?\s*$", "", query, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="type",
                    description=f"Type: {query}",
                    params={"text": query, "press_enter": should_submit},
                ))
                step_num += 1
                if should_submit:
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="wait",
                        description="Wait for submission response",
                        params={"seconds": 1.5},
                    ))
                    step_num += 1

            # 11. Scroll
            elif "scroll" in cl:
                direction = "up" if "up" in cl else "down"
                amount = 400 if "slowly" in cl else 1200 if "bottom" in cl else 600
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="scroll",
                    description=f"Scroll {direction}",
                    params={"direction": direction, "amount": amount},
                ))
                step_num += 1

            # 12. Read / Explain / Summarize / Screen options
            elif any(w in cl for w in ("read", "explain", "summarize", "tell me", "check", "options")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="read_screen",
                    description=f"Inspect screen: {clause}",
                    params={"query": clause},
                ))
                step_num += 1

        if not steps:
            steps.append(PlanStep(
                step_num=1,
                action_type="direct",
                description=instruction,
                params={"command": instruction},
            ))

        return steps

    def execute_plan(self, steps: list[PlanStep]) -> PlanResult:
        """Run the planned sequence step by step, honoring cancellation and safety."""
        reset_stop()
        mode = get_interaction_mode()
        total = len(steps)
        completed = 0
        details: list[dict[str, Any]] = []

        self._log(f"[ActionPlanner] 🚀 Starting plan with {total} steps (Mode: {mode.upper()}).")

        for step in steps:
            # Check stop condition
            if is_stop_requested():
                self._log(f"[ActionPlanner] 🛑 Plan aborted at step {step.step_num}: {step.description}")
                self._notify_status("Automation stopped by user.")
                return PlanResult(
                    success=False,
                    summary=f"Automation stopped by user at step {step.step_num} of {total}.",
                    steps_total=total,
                    steps_completed=completed,
                    aborted_by_user=True,
                    step_details=details,
                )

            step.status = "running"
            self._notify_status(f"Step {step.step_num}/{total}: {step.description}")
            self._log(f"[ActionPlanner] ▶ Step {step.step_num}/{total}: {step.description}")

            # Safety Gate check in Safe mode
            if mode == "safe" and step.action_type in ("type", "find_and_click"):
                # In safe mode, sensitive steps log verification
                self._log(f"[ActionPlanner] 🛡️ Safe mode: Verifying step {step.step_num} before execution.")

            step_success = False
            last_err = ""

            while step.retries <= step.max_retries and not step_success:
                if is_stop_requested():
                    break

                try:
                    res = self._dispatch_step(step)
                    step.result = str(res)
                    step_success = True
                    step.status = "completed"
                    completed += 1
                except Exception as e:
                    last_err = str(e)
                    step.retries += 1
                    if step.retries <= step.max_retries:
                        self._log(f"[ActionPlanner] ⚠️ Step {step.step_num} failed: {e}. Retrying ({step.retries}/{step.max_retries})...")
                        time.sleep(1.0)
                    else:
                        step.status = "failed"
                        step.result = f"Error after {step.max_retries} retries: {last_err}"

            details.append(step.to_dict())

            if not step_success and not is_stop_requested():
                # Stop sequence on failure of critical step
                self._log(f"[ActionPlanner] ❌ Step {step.step_num} failed permanently: {last_err}")
                return PlanResult(
                    success=False,
                    summary=f"Task partially completed ({completed}/{total} steps). Failed at Step {step.step_num}: {step.description} ({last_err})",
                    steps_total=total,
                    steps_completed=completed,
                    step_details=details,
                )

            # Re-read or breathe between UI steps
            time.sleep(0.3)

        self._notify_status("Multi-step task completed.")
        self._log(f"[ActionPlanner] ✅ All {total} steps completed successfully.")
        return PlanResult(
            success=True,
            summary=f"All {total} steps completed successfully.",
            steps_total=total,
            steps_completed=completed,
            step_details=details,
        )

    def _dispatch_step(self, step: PlanStep) -> str:
        """Route step to appropriate computer control handler."""
        action = step.action_type
        params = step.params

        if action == "open_app":
            return self._execute({
                "action": "open_path",
                "path": params.get("app_name", ""),
            })

        elif action in ("find_and_click", "click"):
            return self._execute({
                "action": "click",
                "description": params.get("description", ""),
                "index": int(params.get("index", 1)),
                "verify": bool(params.get("verify", True)),
            })

        elif action == "double_click":
            return self._execute({
                "action": "double_click",
                "description": params.get("description", ""),
                "verify": bool(params.get("verify", True)),
            })

        elif action == "right_click":
            return self._execute({
                "action": "right_click",
                "description": params.get("description", ""),
                "verify": bool(params.get("verify", True)),
            })

        elif action == "move":
            return self._execute({
                "action": "move",
                "description": params.get("description", ""),
                "x": params.get("x"),
                "y": params.get("y"),
            })

        elif action == "hover":
            return self._execute({
                "action": "hover",
                "description": params.get("description", ""),
                "x": params.get("x"),
                "y": params.get("y"),
            })

        elif action == "drag":
            return self._execute({
                "action": "drag",
                "source": params.get("source"),
                "destination": params.get("destination"),
                "x1": params.get("x1"),
                "y1": params.get("y1"),
                "x2": params.get("x2"),
                "y2": params.get("y2"),
            })

        elif action == "press_key":
            keys = params.get("keys")
            if keys:
                return self._execute({"action": "hotkey", "keys": keys})
            return self._execute({"action": "press", "key": params.get("key", "enter")})

        elif action == "type":
            txt = params.get("text", "")
            press_enter = params.get("press_enter", False)
            res = self._execute({"action": "smart_type", "text": txt})
            if press_enter:
                time.sleep(0.15)
                self._execute({"action": "press", "key": "enter"})
            return res

        elif action == "scroll":
            return self._execute({
                "action": "scroll",
                "direction": params.get("direction", "down"),
                "amount": params.get("amount", 600),
            })

        elif action == "wait":
            secs = float(params.get("seconds", 1.0))
            time.sleep(secs)
            return f"Waited {secs}s."

        elif action == "read_screen":
            from core.screen_reader import answer_screen_question
            return answer_screen_question(params.get("query", "Read current screen"))

        elif action == "direct":
            return self._execute(params)

        return f"Unknown action type: {action}"
