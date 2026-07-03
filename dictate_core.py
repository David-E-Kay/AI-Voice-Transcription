"""Pure helpers for the dictation utility — numpy-only, no hardware/heavy imports.
Kept separate so they unit-test without importing keyboard/sounddevice/faster_whisper."""
import numpy as np


def clean_text(text):
    """Normalize Whisper output for injection: strip ends, collapse internal whitespace."""
    return " ".join(text.split())


def frames_to_audio(frames):
    """Concatenate a list of float32 mic frames (each (n, 1)) into a flat mono
    float32 array for faster-whisper. Empty list -> empty array."""
    if not frames:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate([f.reshape(-1) for f in frames]).astype(np.float32)


def is_too_short(audio, samplerate=16000, min_ms=300):
    """True if the clip is shorter than min_ms (likely an accidental tap)."""
    return audio.shape[0] < int(samplerate * min_ms / 1000)


def backdrop_box(wf_w, wf_h, pad_x_frac=0.15, pad_y_frac=0.10):
    """Size the HUD backdrop box that frames the waveform bars.

    Given the bar bounding box (wf_w, wf_h), pad it out by pad_x_frac per left/right
    side and pad_y_frac per top/bottom. Returns (box_w, box_h, offset_x, offset_y)
    where offset_* is how far the bar region is inset inside the box."""
    off_x = round(wf_w * pad_x_frac)
    off_y = round(wf_h * pad_y_frac)
    return wf_w + 2 * off_x, wf_h + 2 * off_y, off_x, off_y
