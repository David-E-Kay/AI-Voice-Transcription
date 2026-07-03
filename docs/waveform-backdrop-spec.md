# Spec — Metallic-bevel backdrop box behind the waveform HUD

## Goal
Draw a rounded, metallic-bevel box **behind** the neon-green equalizer bars in the
recording overlay (`Overlay`, `dictate.py`). The box has a crisp silver rim
(light → dark, top-left to bottom-right) around a flat charcoal core — matching the
app icon. Chosen from mockup option **B**.

## Look (agreed)
- Outer rim: linear metallic gradient, `#f2f4f6` (top-left) → `#5b6167` (bottom-right).
- Inner core: flat charcoal `#2d3237`, inset from the rim by a thin bevel band.
- Corners: rounded on both rim and core.
- Box is **static** — it does not pulse. Only the bars animate (unchanged).

## Geometry
Let `wf_w`, `wf_h` be today's overlay size (the bar bounding box — current
`self._width`, `self._height`).

- `pad_x = 0.15 * wf_w` per side  → `box_w = wf_w * 1.30`
- `pad_y = 0.10 * wf_h` per side  → `box_h = wf_h * 1.20`
- Bars shift inward by `(pad_x, pad_y)`; the window grows to `box_w × box_h`.
- Corner radius: `~0.14 * box_h`. Bevel band width: `max(2, round(box_h * 0.035))`.

New pure helper in **`dictate_core.py`** (keeps it unit-testable):

```python
def backdrop_box(wf_w, wf_h, pad_x_frac=0.15, pad_y_frac=0.10):
    """Return (box_w, box_h, offset_x, offset_y) for the HUD backdrop.
    offset_* is how far the bar region is inset inside the box."""
    off_x = round(wf_w * pad_x_frac)
    off_y = round(wf_h * pad_y_frac)
    return wf_w + 2 * off_x, wf_h + 2 * off_y, off_x, off_y
```

## Rendering — one-time image, not canvas primitives
Tk's Canvas has no gradient or rounded-rect primitive, so build the box **once** at
`Overlay.__init__` as a Pillow image and place it behind the bars with
`canvas.create_image(0, 0, anchor="nw", ...)` (created before the bars so it sits
underneath; keep a ref on `self` so Tk doesn't GC the `PhotoImage`).

Steps (all in `dictate.py`, the hardware/heavy module — not `dictate_core`):
1. Start with an RGB image `box_w × box_h` filled with **pure black** `(0,0,0)`
   (the window's `-transparentcolor`, so everything outside the rounded rim
   disappears).
2. Build the rim: a numpy diagonal gradient `#f2f4f6 → #5b6167`, masked to a
   rounded rect (`ImageDraw.rounded_rectangle` on an `L` mask), pasted onto the black.
3. Paste the charcoal core: a rounded rect inset by the bevel band, filled `#2d3237`.
4. `ImageTk.PhotoImage(...)`, `create_image`, keep the reference.

## Wiring changes in `Overlay`
- Compute `box_w, box_h, off_x, off_y = backdrop_box(self._width, self._height)`;
  use `box_w/box_h` for `self._width/_height`, window `geometry`, and `Canvas` size.
- Offset every bar x by `off_x`; set `self._floor = box_h - off_y - self._bar_gap`.
- `_center_on_active_window` already reads `self._width/_height` → correct for free.

## Dependency
Add `pillow` to `requirements.txt`. New imports in `dictate.py`:
`from PIL import Image, ImageDraw, ImageTk`.

## Known limitation (note as a `ponytail:` comment)
Color-key transparency only removes *exactly* black pixels, so the anti-aliased
rounded corners leave a faint ~1px dark rim against the window behind. Acceptable for
a transient HUD; the upgrade path is a per-pixel-alpha layered window
(`UpdateLayeredWindow`), which is a large rewrite — not worth it.

## Verification
- Unit test `backdrop_box` in `tests/` (pad math, offsets) — the one runnable check.
- Manual: run `dictate.py`, hold Right Ctrl, confirm the beveled box frames the bars,
  stays put, doesn't steal focus, and hides on release.
