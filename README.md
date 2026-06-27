# Push-to-Talk Local Dictation

Hold **Right Ctrl**, speak, release — your words are transcribed locally on the GPU and
pasted into the active window. Zero cloud. English only.

## Setup
    py -3.14 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    python dictate.py        # first run downloads ~1.6 GB model

## Autostart
Run `run_dictation.bat` (uses pythonw, no console). To start at login, put a shortcut to it
in the folder opened by `explorer "shell:startup"`.

## GPU notes
INT8 `large-v3-turbo` uses ~1.6 GB VRAM (compute_type `int8_float16`). On a 4 GB laptop GPU
shared with the desktop, if you hit CUDA OOM, edit `Engine(...)` in `dictate.py` to
`distil-large-v3` or `small.en`.
The nvidia cuBLAS/cuDNN wheels are required (in `requirements.txt`); `dictate.py` prepends
their bin dirs to PATH at startup via `_add_cuda_dlls()`.

## Known limitations
- Terminals paste with **Ctrl+Shift+V**, so dictation into a terminal won't insert text.
- Won't capture the hotkey while an **elevated/admin** window is focused (Windows UIPI).
- Clipboard restore is **text-only**; a copied image/file is lost during a dictation.
- Text pastes into whatever window has focus **at release** — switching windows mid-speech
  lands the text in the new window.
