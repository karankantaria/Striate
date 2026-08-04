"""Minimal Pillow renderers for headless verification.

The min/max envelope + mean line mirrors what the frontend will draw:
a spike must survive downsampling visibly (§5.2), even in a static PNG.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..stats import reduce_minmeanmax
from ..surfaces.ngram import to_display


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


def save_hist2d_png(counts: np.ndarray, path: str, *,
                    mode: str = "log1p", scale: int = 2) -> None:
    disp = to_display(counts, mode)
    img = Image.fromarray(disp, "L")
    if scale > 1:
        img = img.resize((disp.shape[1] * scale, disp.shape[0] * scale),
                         Image.NEAREST)
    img.save(path, "PNG")


# Perceptually-uniform sequential scale, sampled from viridis and
# interpolated to 256 entries. Never rainbow/jet: its yellow/cyan
# transitions manufacture boundaries that are not in the data.
# (P7 builds the real frontend palette with the dataviz skill; this is
# the headless equivalent so plates and UI agree in character.)
_VIRIDIS_STOPS = np.array([
    (68, 1, 84), (72, 40, 120), (62, 74, 137), (49, 104, 142),
    (38, 130, 142), (31, 158, 137), (53, 183, 121), (109, 205, 89),
    (180, 222, 44), (253, 231, 37)], dtype=np.float64)

# Categorical, from a deliberately different hue family so a category map
# can never be mistaken for a magnitude map.
BYTE_CLASS_COLOURS = np.array([
    (24, 24, 32),     # 0 null        near-black
    (235, 235, 235),  # 1 printable   near-white
    (140, 190, 255),  # 2 whitespace  pale blue
    (255, 150, 90),   # 3 control     orange
    (200, 90, 200),   # 4 high        magenta
    (255, 80, 80),    # 5 0xff        red
], dtype=np.uint8)


def viridis_lut() -> np.ndarray:
    xs = np.linspace(0, 1, len(_VIRIDIS_STOPS))
    out = np.empty((256, 3), dtype=np.uint8)
    q = np.linspace(0, 1, 256)
    for c in range(3):
        out[:, c] = np.interp(q, xs, _VIRIDIS_STOPS[:, c]).round()
    return out


def save_raster_png(raster, path: str, *, scale: int = 1) -> None:
    """Render a Raster to PNG: RGB straight through, scalar through the
    categorical palette or viridis depending on what the surface reported."""
    px = raster.pixels
    if raster.kind == "rgb":
        img = Image.fromarray(px, "RGB")
    elif raster.meta.get("categorical"):
        lut = np.zeros((256, 3), dtype=np.uint8)
        n = len(BYTE_CLASS_COLOURS)
        lut[:n] = BYTE_CLASS_COLOURS
        img = Image.fromarray(lut[px], "RGB")
    else:
        img = Image.fromarray(viridis_lut()[px], "RGB")
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    img.save(path, "PNG")
