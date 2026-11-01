"""
Local wake-word detection for NOVA ("Hey Nova").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

NOVA prefers a custom openWakeWord model when one is supplied. Because openWakeWord
does not ship a pretrained "Hey Nova" model, the project includes a local Whisper
fallback that recognizes the phrase without sending audio to a cloud service.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

WAKE_MODEL = "hey_nova"
LEGACY_MODEL = "hey_jarvis"
DEFAULT_THRESHOLD = 0.5
SAMPLE_RATE = 16000


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """Return whether a local Hey Nova backend is available."""
    if is_installed():
        try:
            import openwakeword
            models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
            if models_dir.is_dir() and any(models_dir.glob(f"{WAKE_MODEL}*.onnx")):
                return True
            if models_dir.is_dir() and any(models_dir.glob(f"{WAKE_MODEL}*.tflite")):
                return True
        except Exception:
            pass
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Install a local Hey Nova backend. Custom OWW is preferred; Whisper is the
    portable fallback and downloads its tiny model lazily on first activation."""
    _tell = notify or (lambda _msg: None)
    try:
        if not is_installed():
            logger("Wake word: installing openwakeword (optional custom backend)…")
            try:
                r = subprocess.run([sys.executable, "-m", "pip", "install", "openwakeword"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    logger("Wake word: openwakeword unavailable; using Whisper fallback.")
            except Exception:
                pass
        try:
            import faster_whisper
        except Exception:
            logger("Wake word: installing local faster-whisper fallback…")
            _tell("Wake word: installing local Hey Nova recognition…")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "faster-whisper"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                tail=(r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"faster-whisper install failed: {tail[0][:160]}"
        logger("Wake word: Hey Nova backend ready. The tiny.en model downloads on first use.")
        return True, "Hey Nova is ready."
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Runs the wake model in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect() (called from this thread —
    the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None,
                 on_sleep: Callable[[], None] | None = None,
                 on_stop: Callable[[], None] | None = None):
        self._on_detect = on_detect
        self._on_sleep  = on_sleep
        self._on_stop   = on_stop
        self._threshold = threshold
        self._logger    = logger
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._fallback_stt = None
        self._ready = False

    def start(self) -> bool:
        """Start an offline Hey Nova detector. Prefer a custom openWakeWord model;
        otherwise use a tiny local Whisper model as a phrase recognizer."""
        if self._running:
            return True
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
            self._mode = "openwakeword"
        except Exception as ow_err:
            self._model = None
            try:
                from faster_whisper import WhisperModel
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                compute = "float16" if device == "cuda" else "int8"
            except Exception:
                device, compute = "cpu", "int8"
            try:
                from faster_whisper import WhisperModel
                self._fallback_stt = WhisperModel("tiny.en", device=device, compute_type=compute)
                self._mode = "whisper"
                self._logger("Wake word: Hey Nova uses the local Whisper fallback (first start may download tiny.en).")
            except Exception as e:
                self._logger(f"Wake word: Hey Nova detector unavailable — {e}")
                self._notify("Install faster-whisper or add a custom hey_nova openWakeWord model.")
                return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="NovaWakeWordThread")
        self._thread.start()
        self._logger("Wake word: listening for 'Hey Nova'.")
        return True

    def stop(self) -> None:
        self._running = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        import numpy as np
        if getattr(self, "_mode", "") == "whisper":
            self._loop_whisper(np)
            return
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                scores = self._model.predict(np.asarray(frame, dtype=np.int16))
                score = 0.0
                if isinstance(scores, dict):
                    for k, v in scores.items():
                        if "nova" in k.lower():
                            score = max(score, float(v))
                    if score == 0.0 and scores:
                        score = max(float(v) for v in scores.values())
                if score >= self._threshold:
                    self._drain()
                    try: self._on_detect()
                    except Exception as e: self._logger(f"Wake word: on_detect error — {e}")
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _loop_whisper(self, np) -> None:
        """Recognize short rolling windows and fire only for the exact Hey Nova phrase."""
        buf = np.zeros(0, dtype=np.int16)
        needed = SAMPLE_RATE * 2
        while self._running:
            try:
                frame = self._queue.get(timeout=0.5)
                if frame is None or not self._running:
                    break
                buf = np.concatenate((buf, np.asarray(frame, dtype=np.int16)))
                if len(buf) < needed:
                    continue
                chunk = buf[-needed:]
                audio = chunk.astype(np.float32) / 32768.0
                segments, _ = self._fallback_stt.transcribe(
                    audio, language="en", beam_size=1, best_of=1,
                    condition_on_previous_text=False, vad_filter=True)
                text = " ".join(seg.text for seg in segments).strip().lower()
                normalized = " ".join(text.replace(".", " ").split())

                if ("nova stop" in normalized or normalized == "stop" or "stop listening" in normalized):
                    self._drain()
                    if self._on_stop:
                        self._on_stop()
                    buf = np.zeros(0, dtype=np.int16)
                    continue

                sleep_triggers = (
                    "go to sleep", "sleep nova", "nova go to sleep",
                    "nova stop listening", "nova deactivate"
                )
                try:
                    from memory.config_manager import get_sleep_phrases
                    sleep_triggers = tuple(get_sleep_phrases())
                except Exception:
                    pass

                if any(sp in normalized for sp in sleep_triggers):
                    self._drain()
                    if self._on_sleep:
                        self._on_sleep()
                    buf = np.zeros(0, dtype=np.int16)
                    continue

                wake_triggers = ("hey nova", "wake up nova", "nova wake up", "ok nova")
                try:
                    from memory.config_manager import get_wake_phrases
                    wake_triggers = tuple(get_wake_phrases())
                except Exception:
                    pass

                matched_trigger = None
                for wp in wake_triggers:
                    if wp and wp in normalized:
                        matched_trigger = wp
                        break

                if matched_trigger:
                    self._logger(f"Wake word matched phrase '{matched_trigger}' in speech: '{normalized}'")
                    self._drain()
                    self._on_detect()
                    buf = np.zeros(0, dtype=np.int16)
                else:
                    buf = buf[-SAMPLE_RATE:]
            except queue.Empty:
                continue
            except Exception as e:
                self._logger(f"Wake word: Whisper inference error — {e}")
                buf = buf[-SAMPLE_RATE:] if len(buf) else buf

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
