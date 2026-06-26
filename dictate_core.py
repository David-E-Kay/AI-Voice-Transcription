"""Pure helpers for the dictation utility — numpy-only, no hardware/heavy imports.
Kept separate so they unit-test without importing keyboard/sounddevice/faster_whisper."""
import re


def clean_text(text):
    """Normalize Whisper output for injection: strip ends, collapse internal whitespace."""
    return re.sub(r"\s+", " ", text).strip()
