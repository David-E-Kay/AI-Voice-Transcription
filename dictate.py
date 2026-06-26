"""Push-to-talk local dictation. Hold Right Ctrl to record; release to transcribe + paste."""
import os
import glob
import time
import threading
import importlib.util

import numpy as np
import sounddevice as sd
import pyperclip
import keyboard

from dictate_core import frames_to_audio, is_too_short, clean_text

SAMPLE_RATE = 16000
HOTKEY = "right ctrl"


class Recorder:
    """Open-ended mic capture into a RAM buffer. start() opens a 16 kHz mono float32
    stream; stop() returns the captured audio as a flat float32 numpy array."""

    def __init__(self, samplerate=SAMPLE_RATE):
        self.samplerate = samplerate
        self._frames = []
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        # ponytail: ignore `status` overflows — a dropped mic frame in dictation is harmless.
        self._frames.append(indata.copy())

    def start(self):
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.samplerate, channels=1, dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        return frames_to_audio(self._frames)


def _add_cuda_dlls():
    """Make pip-installed nvidia cuBLAS/cuDNN DLLs discoverable by CTranslate2 on Windows.
    CTranslate2 loads these dynamically in a way that ignores os.add_dll_directory for the
    transitive deps (cublasLt, nvrtc), so we PREPEND every nvidia/*/bin dir to PATH — this
    is the approach verified working on this machine. No-op if the nvidia-*-cu12 wheels
    aren't installed (e.g. you have a system CUDA on PATH instead)."""
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    bins = glob.glob(os.path.join(spec.submodule_search_locations[0], "*", "bin"))
    if bins:
        os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")


class Engine:
    """Pre-warmed faster-whisper engine. Loads once, stays resident in VRAM.

    Model fallback ladder if you hit CUDA OOM on the 4 GB laptop GPU:
      'large-v3-turbo' (default) -> 'distil-large-v3' -> 'small.en'

    Quantization: 'int8_float16' = INT8 weights (smallest VRAM, ~1.6 GB, same as plain
    int8) + float16 compute. On this Ampere GPU it's equal-or-faster than plain int8 and
    more accurate -> the "smallest footprint without sacrificing speed/accuracy" choice.
    On CPU fallback use compute_type='int8' instead.
    """

    def __init__(self, model_size="large-v3-turbo", device="cuda", compute_type="int8_float16"):
        from faster_whisper import WhisperModel  # heavy import deferred to construction
        _add_cuda_dlls()
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._warmup()

    def _warmup(self):
        # Trigger CUDA kernel + cuDNN init now so the first real transcription is fast.
        segs, _ = self.model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                        language="en", beam_size=1)
        list(segs)

    def transcribe(self, audio):
        # Greedy (beam_size=1) for latency; fixed English skips language detection;
        # VAD trims silence for speed + accuracy.
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
        )
        return "".join(seg.text for seg in segments)


def inject(text):
    """Paste text into the active window via clipboard, then restore the old clipboard.
    ponytail: restore is text-only — non-text clipboard (images/files) is lost. Upgrade to
    full Win32 clipboard save/restore only if that ever bites. The 0.1 s sleep is a tuning
    knob: raise it if a slow app pastes stale/empty content before reading the clipboard."""
    if not text:
        return
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = ""
    pyperclip.copy(text)
    keyboard.send("ctrl+v")
    time.sleep(0.1)
    try:
        pyperclip.copy(previous)
    except Exception:
        pass
