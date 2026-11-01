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
    action_type: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
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

        # Feature 3: Dictation Protection
        # If user is dictating text/paragraphs, preserve the exact text verbatim
        # without splitting on commas, 'and', or embedded keywords like 'cancel' or 'delete'.
        m_dictate = re.match(r"^(?:type\s+(?:this\s+)?(?:paragraph|text|sentence|message)?[:\s]+|dictate[:\s]+|write[:\s]+)(.*)$", inst, flags=re.I | re.DOTALL)
        if m_dictate:
            dictated_text = m_dictate.group(1).strip()
            return [
                PlanStep(
                    step_num=1,
                    action_type="type",
                    description=f"Type: {dictated_text[:50]}...",
                    params={"text": dictated_text, "press_enter": False},
                )
            ]

        clauses = [c.strip() for c in re.split(r",\s*|\s+then\s+|\s+and\s+(?!click\b|press\b|submit\b|hit\s+enter\b)", inst, flags=re.I) if c.strip()]
        step_num = 1

        for raw_clause in clauses:
            clause = re.sub(r"^(then|and|next)\s+", "", raw_clause, flags=re.I).strip()
            if not clause:
                continue
            cl = clause.lower()

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

            elif re.search(r"^(?:bring|switch\s+to|focus)\s+(.*?)(?:\s+to\s+the\s+front|\s+to\s+front)?$", cl):
                m_bring = re.search(r"^(?:bring|switch\s+to|focus)\s+(.*?)(?:\s+to\s+the\s+front|\s+to\s+front)?$", clause, flags=re.I)
                app_name = m_bring.group(1).strip() if m_bring else "app"
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="open_app",
                    description=f"Bring {app_name} to front",
                    params={"app_name": app_name},
                ))
                step_num += 1

            elif re.search(r"^(?:show\s+me|explore|view)\s+(?:the\s+)?(.*?)(?:\s+folder)?$", cl):
                m_show = re.search(r"^(?:show\s+me|explore|view)\s+(?:the\s+)?(.*?)(?:\s+folder)?$", clause, flags=re.I)
                target = m_show.group(1).strip() if m_show else "folder"
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="open_path",
                    description=f"Open {target} folder",
                    params={"path": f"{target} folder"},
                ))
                step_num += 1

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

            elif re.search(r"^(move\s+(the\s+)?mouse\s+to|move\s+to)\s+", cl):
                target = re.sub(r"^(move\s+(the\s+)?mouse\s+to|move\s+to)\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="move",
                    description=f"Move mouse to '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            elif cl.startswith("hover over ") or cl.startswith("hover on ") or cl.startswith("hover "):
                target = re.sub(r"^hover(\s+over|\s+on)?\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="hover",
                    description=f"Hover over '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            # Directional relative mouse movement
            elif re.search(r"^move\s+(?:the\s+)?mouse\s+(left|right|up|down)(?:\s+(?:by\s+)?(\d+)(?:px| pixels)?)?", cl):
                m_rel = re.search(r"^move\s+(?:the\s+)?mouse\s+(left|right|up|down)(?:\s+(?:by\s+)?(\d+)(?:px| pixels)?)?", cl)
                d_str = m_rel.group(1).lower()
                dist_val = int(m_rel.group(2)) if m_rel.group(2) else 100
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="move",
                    description=f"Move mouse {d_str} by {dist_val}px",
                    params={"direction": d_str, "distance": dist_val},
                ))
                step_num += 1

            elif cl in ("move mouse left", "move left", "mouse left"):
                steps.append(PlanStep(step_num=step_num, action_type="move", description="Move mouse left 100px", params={"direction": "left", "distance": 100}))
                step_num += 1
            elif cl in ("move mouse right", "move right", "mouse right"):
                steps.append(PlanStep(step_num=step_num, action_type="move", description="Move mouse right 100px", params={"direction": "right", "distance": 100}))
                step_num += 1
            elif cl in ("move mouse up", "move up", "mouse up"):
                steps.append(PlanStep(step_num=step_num, action_type="move", description="Move mouse up 100px", params={"direction": "up", "distance": 100}))
                step_num += 1
            elif cl in ("move mouse down", "move down", "mouse down"):
                steps.append(PlanStep(step_num=step_num, action_type="move", description="Move mouse down 100px", params={"direction": "down", "distance": 100}))
                step_num += 1

            # Click at current pointer position
            elif cl in ("double click", "double-click", "double click here", "double click mouse"):
                steps.append(PlanStep(step_num=step_num, action_type="double_click", description="Double-click at current position", params={"description": ""}))
                step_num += 1

            elif cl in ("right click", "right-click", "right click here", "right click mouse"):
                steps.append(PlanStep(step_num=step_num, action_type="right_click", description="Right-click at current position", params={"description": ""}))
                step_num += 1

            elif cl in ("click", "left click", "click here", "click mouse"):
                steps.append(PlanStep(step_num=step_num, action_type="click", description="Click at current position", params={"description": ""}))
                step_num += 1

            elif "double click" in cl or "double-click" in cl:
                target = re.sub(r"^(double[- ]click(\s+on|\s+the)?)\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="double_click",
                    description=f"Double-click '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            elif "right click" in cl or "right-click" in cl:
                target = re.sub(r"^(right[- ]click(\s+on|\s+the)?)\s+", "", clause, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="right_click",
                    description=f"Right-click '{target}'",
                    params={"description": target},
                ))
                step_num += 1

            elif cl in ("drag this item", "drag this", "drag item"):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="drag",
                    description="Drag this item",
                    params={},
                ))
                step_num += 1

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

            elif any(w in cl for w in ("go back", "previous screen", "previous page")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="press_key",
                    description="Go back to previous screen (Alt+Left)",
                    params={"keys": "alt+left"},
                ))
                step_num += 1

            # Text Editing Commands
            elif any(k in cl for k in ("delete this line", "delete line")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="delete_line",
                    description="Delete this line",
                    params={},
                ))
                step_num += 1

            elif any(k in cl for k in ("move to the next line", "move to next line", "next line")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="next_line",
                    description="Move to the next line",
                    params={},
                ))
                step_num += 1

            elif any(k in cl for k in ("save this document", "save document", "save this file", "save file")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="save_document",
                    description="Save this document (Ctrl+S)",
                    params={},
                ))
                step_num += 1

            elif cl in ("select this item", "select this", "select item", "select this text"):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="select_item",
                    description="Select this item",
                    params={},
                ))
                step_num += 1

            elif "select all" in cl:
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="select_all",
                    description="Select all (Ctrl+A)",
                    params={},
                ))
                step_num += 1

            elif cl in ("copy", "copy that", "copy selected", "copy this text", "copy text"):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="copy_text",
                    description="Copy text (Ctrl+C)",
                    params={},
                ))
                step_num += 1

            elif cl in ("paste", "paste it", "paste text", "paste this text", "paste content"):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="paste_text",
                    description="Paste text (Ctrl+V)",
                    params={},
                ))
                step_num += 1

            elif any(k in cl for k in ("undo last action", "undo last", "undo this")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="undo",
                    description="Undo last action",
                    params={},
                ))
                step_num += 1

            elif "undo" in cl and len(cl) < 12:
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="undo",
                    description="Undo (Ctrl+Z)",
                    params={},
                ))
                step_num += 1

            # File Management Commands
            elif re.search(r"^rename\s+(?:this\s+file|the\s+file|file)?\s*(.*?)\s+to\s+(.*)$", cl):
                m_rn = re.search(r"^rename\s+(?:this\s+file|the\s+file|file)?\s*(.*?)\s+to\s+(.*)$", clause, flags=re.I)
                if m_rn:
                    src, dst = m_rn.group(1).strip(), m_rn.group(2).strip()
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="rename_file",
                        description=f"Rename '{src}' to '{dst}'",
                        params={"path": src, "new_name": dst},
                    ))
                    step_num += 1

            elif re.search(r"^copy\s+(?:this\s+file|the\s+file|file)?\s*(.*?)\s+to\s+(.*)$", cl):
                m_cp = re.search(r"^copy\s+(?:this\s+file|the\s+file|file)?\s*(.*?)\s+to\s+(.*)$", clause, flags=re.I)
                if m_cp:
                    src, dst = m_cp.group(1).strip(), m_cp.group(2).strip()
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="copy_file",
                        description=f"Copy '{src}' to '{dst}'",
                        params={"path": src, "destination": dst},
                    ))
                    step_num += 1

            elif re.search(r"^move\s+(?:this\s+file|the\s+file|file)?\s*(.*?)\s+to\s+(.*)$", cl):
                m_mv = re.search(r"^move\s+(?:this\s+file|the\s+file|file)?\s*(.*?)\s+to\s+(.*)$", clause, flags=re.I)
                if m_mv:
                    src, dst = m_mv.group(1).strip(), m_mv.group(2).strip()
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="move_file",
                        description=f"Move '{src}' to '{dst}'",
                        params={"path": src, "destination": dst},
                    ))
                    step_num += 1

            # Emergency Stop / Cancel Commands
            elif any(k in cl for k in ("stop nova", "stop current operation", "stop operation", "cancel")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="cancel",
                    description="Stop current operation",
                    params={},
                ))
                step_num += 1

            elif re.search(r"\bselect\s+(.*?)\s+(?:from|in)\s+(?:the\s+)?(.*?)(?:\s+dropdown)?$", cl):
                m_dd = re.search(r"\bselect\s+(.*?)\s+(?:from|in)\s+(?:the\s+)?(.*?)(?:\s+dropdown)?$", clause, flags=re.I)
                if m_dd:
                    opt, dd = m_dd.group(1).strip(), m_dd.group(2).strip()
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="dropdown_select",
                        description=f"Select '{opt}' from dropdown '{dd}'",
                        params={"dropdown": dd, "option": opt},
                    ))
                    step_num += 1

            elif re.search(r"^scroll\s+(down|up)\s+and\s+(?:click|find)\s+(.*)$", cl):
                m_sf = re.search(r"^scroll\s+(down|up)\s+and\s+(?:click|find)\s+(.*)$", clause, flags=re.I)
                if m_sf:
                    direction, target = m_sf.group(1), m_sf.group(2).strip()
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="scroll",
                        description=f"Scroll {direction}",
                        params={"direction": direction, "amount": 600},
                    ))
                    step_num += 1
                    steps.append(PlanStep(
                        step_num=step_num,
                        action_type="find_and_click",
                        description=f"Click '{target}'",
                        params={"description": target, "index": 1, "verify": True, "allow_scroll": True},
                    ))
                    step_num += 1

            elif cl.startswith("select ") or cl.startswith("choose "):
                target = re.sub(r"^(select|choose)\s+(?:the\s+)?", "", clause, flags=re.I).strip()
                ord_match = re.search(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b", target, re.I)
                ord_idx = 1
                if ord_match:
                    ord_map = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
                    ord_idx = ord_map.get(ord_match.group(1).lower(), 1)
                    target = re.sub(rf"\b{re.escape(ord_match.group(1))}\b", "", target, flags=re.I).strip()
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="find_and_click",
                    description=f"Select '{target}'",
                    params={"description": target, "index": ord_idx, "verify": True},
                ))
                step_num += 1

            elif any(k in cl for k in ("read error", "read the error", "check the error", "what is the error")):
                steps.append(PlanStep(
                    step_num=step_num,
                    action_type="read_screen",
                    description="Read error message",
                    params={"query": "Read the error message"},
                ))
                step_num += 1

            elif "click" in cl or "press the" in cl:
                target = re.sub(r"^(click(\s+on|\s+the)?|press\s+the)\s+", "", clause, flags=re.I).strip()
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

            if mode == "safe" and step.action_type in ("type", "find_and_click"):
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
                self._log(f"[ActionPlanner] ❌ Step {step.step_num} failed permanently: {last_err}")
                return PlanResult(
                    success=False,
                    summary=f"Task partially completed ({completed}/{total} steps). Failed at Step {step.step_num}: {step.description} ({last_err})",
                    steps_total=total,
                    steps_completed=completed,
                    step_details=details,
                )

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
                "direction": params.get("direction"),
                "distance": params.get("distance"),
                "x": params.get("x"),
                "y": params.get("y"),
            })

        elif action == "select_item":
            return self._execute({
                "action": "select_item",
                "description": params.get("description", ""),
            })

        elif action == "delete_line":
            return self._execute({"action": "delete_line"})

        elif action == "next_line":
            return self._execute({"action": "next_line"})

        elif action == "save_document":
            return self._execute({"action": "save_document"})

        elif action == "select_all":
            return self._execute({"action": "select_all"})

        elif action == "copy_text":
            return self._execute({"action": "copy_text"})

        elif action == "paste_text":
            return self._execute({"action": "paste_text"})

        elif action == "undo":
            return self._execute({"action": "undo"})

        elif action == "rename_file":
            return self._execute({
                "action": "rename_file",
                "path": params.get("path", ""),
                "new_name": params.get("new_name", ""),
            })

        elif action == "copy_file":
            return self._execute({
                "action": "copy_file",
                "path": params.get("path", ""),
                "destination": params.get("destination", ""),
            })

        elif action == "move_file":
            return self._execute({
                "action": "move_file",
                "path": params.get("path", ""),
                "destination": params.get("destination", ""),
            })

        elif action == "cancel":
            return self._execute({"action": "cancel"})

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

        elif action in ("dropdown_select", "select_dropdown"):
            return self._execute({
                "action": "select_dropdown",
                "dropdown": params.get("dropdown", ""),
                "option": params.get("option", ""),
            })

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

        elif action == "open_path":
            return self._execute({
                "action": "open_path",
                "path": params.get("path", ""),
            })

        elif action == "read_screen":
            from core.screen_reader import answer_screen_question
            return answer_screen_question(params.get("query", "Read current screen"))

        elif action == "direct":
            return self._execute(params)

        return f"Unknown action type: {action}"
