"""Pure helpers for the dictation utility — numpy-only, no hardware/heavy imports.
Kept separate so they unit-test without importing keyboard/sounddevice/faster_whisper."""
import re

import numpy as np


def clean_text(text):
    """Normalize Whisper output for injection: strip ends, collapse internal whitespace."""
    return re.sub(r"\s+", " ", text).strip()


def frames_to_audio(frames):
    """Concatenate a list of float32 mic frames (each (n, 1)) into a flat mono
    float32 array for faster-whisper. Empty list -> empty array."""
    if not frames:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate([f.reshape(-1) for f in frames]).astype(np.float32)


def is_too_short(audio, samplerate=16000, min_ms=300):
    """True if the clip is shorter than min_ms (likely an accidental tap)."""
    return audio.shape[0] < int(samplerate * min_ms / 1000)
