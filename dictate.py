"""Push-to-talk local dictation. Hold Right Ctrl to record; release to transcribe + paste."""
import os
import glob
import time
import math
import ctypes
import ctypes.wintypes
import threading
import importlib.util
import tkinter as tk

import queue
import numpy as np
import sounddevice as sd
import pyperclip
import keyboard
from ctypes import cast, POINTER
import comtypes
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

from PIL import Image, ImageDraw, ImageTk

from dictate_core import frames_to_audio, is_too_short, clean_text, backdrop_box

SAMPLE_RATE = 16000
HOTKEY = "right ctrl"


class Recorder:
    """Open-ended mic capture into a RAM buffer. start() opens a 16 kHz mono float32
    stream; stop() returns the captured audio as a flat float32 numpy array."""

    def __init__(self, samplerate=SAMPLE_RATE):
        self.samplerate = samplerate
        self._frames = []
        self._stream = None
        self.level = 0.0

    def _callback(self, indata, frames, time_info, status):
        # ponytail: ignore `status` overflows — a dropped mic frame in dictation is harmless.
        self._frames.append(indata.copy())
        self.level = float(np.sqrt(np.mean(np.square(indata))))

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
    Override without editing code via the DICTATE_MODEL / DICTATE_DEVICE / DICTATE_COMPUTE
    env vars (see README "Choosing a model") so users pick the VRAM/accuracy tradeoff.

    Quantization: 'int8_float16' = INT8 weights (smallest VRAM, ~1.6 GB, same as plain
    int8) + float16 compute. On this Ampere GPU it's equal-or-faster than plain int8 and
    more accurate -> the "smallest footprint without sacrificing speed/accuracy" choice.
    On CPU fallback use compute_type='int8' instead.
    """

    def __init__(self, model_size=None, device=None, compute_type=None):
        from faster_whisper import WhisperModel  # heavy import deferred to construction
        # Explicit args win (tests pass them); otherwise resolve from env, then defaults.
        model_size = model_size or os.environ.get("DICTATE_MODEL", "large-v3-turbo")
        device = device or os.environ.get("DICTATE_DEVICE", "cuda")
        # int8_float16 needs a GPU; CPU can't do float16 compute, so default to int8 there.
        compute_type = compute_type or os.environ.get("DICTATE_COMPUTE") or (
            "int8" if device == "cpu" else "int8_float16")
        print(f"Loading {model_size} on {device} ({compute_type})...")
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


# ponytail: window classes where plain Ctrl+V isn't reliable paste (mintty/Git Bash binds
# it to something else by default; legacy conhost needs "Ctrl key shortcuts" enabled).
# Shift+Insert is the one paste shortcut nearly every Windows terminal emulator honors.
_TERMINAL_CLASSES = {"mintty", "PuTTY", "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}


def _foreground_window_class():
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


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
    paste_keys = "shift+insert" if _foreground_window_class() in _TERMINAL_CLASSES else "ctrl+v"
    keyboard.send(paste_keys)
    time.sleep(0.1)
    try:
        pyperclip.copy(previous)
    except Exception:
        pass


BAR_COLOR = "#39FF14"  # neon green
BAR_COUNT = 5
# ponytail: sized as a fraction of screen height (not fixed px) so the HUD reads the same
# relative size on a 1080p laptop and a 4K monitor instead of looking tiny or oversized.
BAR_WIDTH_FRAC = 0.015
BAR_GAP_FRAC = 0.010
MAX_BAR_HEIGHT_FRAC = 0.08
# ponytail: typical mic RMS while speaking on this hardware is ~0.01-0.08; this gain maps
# that range onto the 0-1 bar scale. Retune if bars look maxed-out or barely move.
LEVEL_GAIN = 10.0

# Backdrop box behind the bars: metallic silver rim around a flat charcoal core.
# Padding is a fraction of the bar bounding box (see backdrop_box in dictate_core).
BACKDROP_PAD_X_FRAC = 0.15
BACKDROP_PAD_Y_FRAC = 0.10
BACKDROP_CORNER_FRAC = 0.14   # corner radius as a fraction of box height
BACKDROP_BEVEL_FRAC = 0.035   # silver rim band width as a fraction of box height
BACKDROP_RIM_LIGHT = (242, 244, 246)   # top-left of the rim gradient
BACKDROP_RIM_DARK = (91, 97, 103)      # bottom-right of the rim gradient
BACKDROP_CORE = (45, 50, 55)           # charcoal #2d3237


class Overlay(tk.Tk):
    """Borderless, topmost HUD: a neon-green equalizer that pulses with live mic level
    while RECORDING. Runs entirely on the Tk main thread; App talks to it only through
    `queue` (thread-safe put), since on_press/on_release fire from keyboard's own hook
    thread, same reason the COM mute worker above uses a queue instead of direct calls."""

    def __init__(self, recorder):
        super().__init__()
        self.recorder = recorder
        self.queue = queue.SimpleQueue()
        self._visible = False

        screen_h = self.winfo_screenheight()
        self._bar_width = round(screen_h * BAR_WIDTH_FRAC)
        self._bar_gap = round(screen_h * BAR_GAP_FRAC)
        self._max_bar_height = round(screen_h * MAX_BAR_HEIGHT_FRAC)
        bars_w = BAR_COUNT * (self._bar_width + self._bar_gap) + self._bar_gap
        bars_h = self._max_bar_height + self._bar_gap * 2
        # Grow the window to the padded backdrop box; bars sit inset by (off_x, off_y).
        self._width, self._height, off_x, off_y = backdrop_box(
            bars_w, bars_h, BACKDROP_PAD_X_FRAC, BACKDROP_PAD_Y_FRAC
        )
        width, height = self._width, self._height

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-transparentcolor", "black")
        self.configure(bg="black")
        x = self.winfo_screenwidth() - width - 20
        y = screen_h - height - 60
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.canvas = tk.Canvas(self, width=width, height=height, bg="black", highlightthickness=0)
        self.canvas.pack()
        # Static backdrop image, drawn first so it sits under the bars. Keep a ref on
        # self so Tk doesn't garbage-collect the PhotoImage out from under the canvas.
        self._backdrop = ImageTk.PhotoImage(self._render_backdrop(width, height))
        self.canvas.create_image(0, 0, anchor="nw", image=self._backdrop)
        self._floor = height - off_y - self._bar_gap
        self.bars = [
            self.canvas.create_rectangle(
                off_x + self._bar_gap + i * (self._bar_width + self._bar_gap), self._floor,
                off_x + self._bar_gap + i * (self._bar_width + self._bar_gap) + self._bar_width, self._floor,
                fill=BAR_COLOR, outline="",
            )
            for i in range(BAR_COUNT)
        ]

        self.withdraw()
        self._make_noactivate()
        self._poll_queue()

    def _render_backdrop(self, w, h):
        """Metallic-bevel box: a diagonal silver rim around a flat charcoal core,
        rendered once as a PIL image (Tk's Canvas has no gradient/rounded-rect)."""
        # ponytail: color-key transparency only drops exactly-black pixels, so the
        # anti-aliased rounded corners leave a faint ~1px dark rim against whatever's
        # behind. Fine for a transient HUD; upgrade path is a per-pixel-alpha layered
        # window (UpdateLayeredWindow), a big rewrite not worth it here.
        radius = round(h * BACKDROP_CORNER_FRAC)
        bevel = max(2, round(h * BACKDROP_BEVEL_FRAC))

        # Diagonal metallic gradient: light top-left -> dark bottom-right.
        ys = np.linspace(0, 1, h)[:, None]
        xs = np.linspace(0, 1, w)[None, :]
        t = (xs + ys) / 2.0
        light = np.array(BACKDROP_RIM_LIGHT, dtype=np.float32)
        dark = np.array(BACKDROP_RIM_DARK, dtype=np.float32)
        grad = (light * (1 - t)[..., None] + dark * t[..., None]).astype(np.uint8)
        rim = Image.fromarray(grad)

        # Rounded-rect mask; paste the rim onto a black (transparent-key) background.
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        img = Image.new("RGB", (w, h), (0, 0, 0))
        img.paste(rim, (0, 0), mask)

        # Charcoal core, inset by the bevel band so the rim reads as a border.
        ImageDraw.Draw(img).rounded_rectangle(
            [bevel, bevel, w - 1 - bevel, h - 1 - bevel],
            radius=max(0, radius - bevel), fill=BACKDROP_CORE,
        )
        return img

    def _make_noactivate(self):
        # ponytail: stdlib ctypes, no pywin32 dep. WS_EX_NOACTIVATE + TOOLWINDOW stop this
        # HUD from stealing keyboard focus from whatever window you're dictating into
        # (verified empirically: the very first Tk() map briefly grabs focus regardless -
        # a one-time blip at process startup - but every later show()/hide() doesn't,
        # once this style is set on the hidden window beforehand).
        self.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        )

    def _poll_queue(self):
        try:
            while True:
                cmd = self.queue.get_nowait()
                if cmd == "show" and not self._visible:
                    self._visible = True
                    self._center_on_active_window()
                    self.deiconify()
                    self._tick()
                elif cmd == "hide":
                    self._visible = False
                    self.withdraw()
        except queue.Empty:
            pass
        self.after(30, self._poll_queue)

    def _center_on_active_window(self):
        # ponytail: queried while the overlay is still hidden (NOACTIVATE means it was
        # never the foreground window anyway), so this is genuinely the window you're
        # dictating into. Falls back to leaving the last position alone if Windows can't
        # give us a rect (e.g. foreground hwnd is the desktop).
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        rect = ctypes.wintypes.RECT()
        if not hwnd or not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        self.geometry(f"+{cx - self._width // 2}+{cy - self._height // 2}")

    def _tick(self):
        if not self._visible:
            return
        level = min(self.recorder.level * LEVEL_GAIN, 1.0)
        now = time.time()
        for i, bar in enumerate(self.bars):
            wobble = 0.5 + 0.5 * math.sin(now * 6 + i * 1.3)
            h = max(4, int(level * self._max_bar_height * wobble))
            x0, _, x1, _ = self.canvas.coords(bar)
            self.canvas.coords(bar, x0, self._floor - h, x1, self._floor)
        self.after(50, self._tick)


class App:
    """Glues recorder + engine + injection behind a 3-state guard so overlapping
    key events are safe. State transitions are serialized by a lock."""

    def __init__(self):
        self.recorder = Recorder()
        self.engine = Engine()
        self.overlay = Overlay(self.recorder)
        self.state = "IDLE"
        self.lock = threading.Lock()
        self._mute_q = queue.SimpleQueue()
        # ponytail: dedicated thread keeps COM calls off the keyboard hook thread,
        # which has a ~200ms Windows timeout that COM init easily exceeds.
        threading.Thread(target=self._mute_worker, daemon=True).start()

    def _mute_worker(self):
        comtypes.CoInitialize()
        dev = AudioUtilities.GetSpeakers()
        iface = dev._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(iface, POINTER(IAudioEndpointVolume))
        while True:
            muted = self._mute_q.get()
            try:
                vol.SetMute(muted, None)
            except Exception:
                pass

    def on_press(self):
        # Windows sends key-repeat while held; the guard makes repeats no-ops.
        with self.lock:
            if self.state != "IDLE":
                return
            self.state = "RECORDING"
        self.recorder.start()
        self._mute_q.put(1)
        self.overlay.queue.put("show")

    def on_release(self):
        with self.lock:
            if self.state != "RECORDING":
                return
            self.state = "PROCESSING"
        audio = self.recorder.stop()
        self._mute_q.put(0)
        self.overlay.queue.put("hide")
        # ponytail: a tap during PROCESSING is dropped (guard above). Swap to a queue
        # only if rapid back-to-back dictation becomes a real need.
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio):
        try:
            if is_too_short(audio):
                return
            text = clean_text(self.engine.transcribe(audio))
            inject(text)  # pastes into whatever window has focus now (focus-loss is inherent)
        except Exception as e:
            print("dictation error:", e)
        finally:
            with self.lock:
                self.state = "IDLE"


def main():
    print("Starting dictation (first run downloads the model)...")
    app = App()
    keyboard.on_press_key(HOTKEY, lambda e: app.on_press() if e.name == HOTKEY else None)
    keyboard.on_release_key(HOTKEY, lambda e: app.on_release() if e.name == HOTKEY else None)
    print(f"Ready. Hold [{HOTKEY}] to dictate. Press Ctrl+C here to quit.")
    app.overlay.mainloop()


if __name__ == "__main__":
    main()
