"""Push-to-talk local dictation. Hold Right Ctrl to record; release to transcribe + paste."""
import os
import glob
import time
import threading
import importlib.util

import numpy as np
import sounddevice as sd

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
