"""Minimal Pillow renderers for headless verification.

The min/max envelope + mean line mirrors what the frontend will draw:
a spike must survive downsampling visibly (§5.2), even in a static PNG.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..stats import reduce_minmeanmax


def save_signal_png(values: np.ndarray, path: str, *,
                    lo: float = 0.0, hi: float = 8.0,
                    width: int = 1024, height: int = 256,
                    title: str = "") -> None:
    """Render a signal as a min/max envelope with a mean line."""
    mins, means, maxs = reduce_minmeanmax(values, width)
    img = Image.new("RGB", (width, height), (16, 16, 20))
    draw = ImageDraw.Draw(img)

    def y(v: float) -> int:
        frac = (float(v) - lo) / (hi - lo) if hi > lo else 0.0
        return int(round((height - 1) * (1.0 - min(max(frac, 0.0), 1.0))))

    for gy in np.linspace(lo, hi, 5):
        draw.line([(0, y(gy)), (width, y(gy))], fill=(40, 40, 48))
    for x in range(width):
        draw.line([(x, y(mins[x])), (x, y(maxs[x]))], fill=(70, 110, 160))
    draw.line([(x, y(means[x])) for x in range(width)], fill=(180, 220, 255))
    if title:
        draw.text((6, 4), title, fill=(200, 200, 200))
    img.save(path, "PNG")


def to_display(counts: np.ndarray, mode: str = "log1p") -> np.ndarray:
    """Histogram counts -> uint8 display values. The transform is part of
    the analysis (raw bigram counts span 6+ orders of magnitude), so callers
    record the mode in metadata."""
    c = counts.astype(np.float64)
    if mode == "linear":
        v = c
    elif mode == "sqrt":
        v = np.sqrt(c)
    elif mode == "log1p":
        v = np.log1p(c)
    elif mode == "rank":
        flat = c.ravel()
        order = flat.argsort().argsort().astype(np.float64)
        nz = flat > 0
        v = np.zeros_like(flat)
        if nz.any():
            r = order[nz]
            v[nz] = r - r.min() + 1
        v = v.reshape(c.shape)
    else:
        raise ValueError(f"unknown display mode {mode!r}")
    peak = v.max()
    return (v * (255.0 / peak)).astype(np.uint8) if peak > 0 else \
        np.zeros_like(v, dtype=np.uint8)


def save_hist2d_png(counts: np.ndarray, path: str, *,
                    mode: str = "log1p", scale: int = 2) -> None:
    disp = to_display(counts, mode)
    img = Image.fromarray(disp, "L")
    if scale > 1:
        img = img.resize((disp.shape[1] * scale, disp.shape[0] * scale),
                         Image.NEAREST)
    img.save(path, "PNG")
