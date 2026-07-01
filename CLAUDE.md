# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A push-to-talk local dictation utility for Windows: hold **Right Ctrl**, speak, release →
faster-whisper transcribes on the GPU and the text is pasted into the active window. No cloud.

## Commands

Setup (Python 3.14 venv lives in `.venv`):

    py -3.14 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt

Run (first run downloads ~1.6 GB model into the HF cache):

    .\.venv\Scripts\python.exe dictate.py     # console; Ctrl+C to quit
    run_dictation.bat                          # manual double-click run — opens a cmd console

Autostart at login = Task Scheduler entry `"AI Voice Dictation"` running
`.venv\Scripts\pythonw.exe dictate.py` headless, restarts on crash (up to 10×, 1 min apart).
See README "Autostart" for the registration command.

Tests:

    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe -m pytest tests/test_app_state.py::test_name   # single test

## Architecture

Two modules, split deliberately by testability:

- **`dictate_core.py`** — pure numpy helpers (`frames_to_audio`, `is_too_short`, `clean_text`),
  no hardware/heavy imports. This is what the unit tests exercise without pulling in
  `keyboard` / `sounddevice` / `faster_whisper`.
- **`dictate.py`** — everything that touches hardware or the model: `Recorder` (RAM mic
  capture), `Engine` (faster-whisper), `inject` (clipboard paste), and `App` (the glue).

`App` is a 3-state machine — `IDLE → RECORDING → PROCESSING → IDLE` — serialized by a
`threading.Lock`. Key-press starts recording; release stops it and hands the audio to a
**daemon worker thread** so the keyboard callback never blocks. The guard makes Windows
key-repeat (while held) and taps-during-processing into no-ops. Tests in
`tests/test_app_state.py` drive `App` with fake Recorder/Engine to assert these transitions.

Pipeline on release: `Recorder.stop()` → `is_too_short` guard (drops <300 ms accidental taps)
→ `Engine.transcribe` (greedy `beam_size=1`, fixed English, VAD) → `clean_text` → `inject`.

## Gotchas (the non-obvious parts)

- **CUDA DLL discovery is a hand-rolled PATH hack.** CTranslate2's Windows wheels ship no
  cuBLAS/cuDNN, so the `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` wheels supply them.
  `_add_cuda_dlls()` **prepends** every `nvidia/*/bin` dir to `PATH` at startup —
  `os.add_dll_directory` does **not** work here (CTranslate2 ignores it for the transitive
  `cublasLt`/`nvrtc` loads). Don't "simplify" this back to `add_dll_directory`.
- **Model fallback ladder for CUDA OOM** (4 GB laptop GPU): set `DICTATE_MODEL`
  `large-v3-turbo` → `distil-large-v3` → `small.en` (env var, no code edit). `DICTATE_DEVICE=cpu`
  auto-selects `int8` compute. Explicit `Engine(...)` args still override the env.
- **Injection is clipboard paste + restore** (`pyperclip` + `keyboard.send`), sending
  Shift+Insert instead of Ctrl+V when the foreground window class is a known terminal
  emulator (mintty, PuTTY, conhost, Windows Terminal — see `_TERMINAL_CLASSES` in
  `dictate.py`); non-text clipboard contents are lost during a dictation; elevated/admin
  windows swallow the hotkey (Windows UIPI).

## Conventions

- `ponytail:` comments mark deliberate simplifications and name the upgrade path. Treat them
  as intent, not omissions — don't "fix" them without reason.
- Keep pure logic in `dictate_core.py` so it stays unit-testable; only hardware/model code
  belongs in `dictate.py`.
