#!/usr/bin/env python3
"""Regenerate docs/plates/ — one plate per surface per corpus class.

These are the visual regression baseline and the documentation. Run after
changing any surface:  python docs/make_plates.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "plates")
CORPUS = os.path.join(ROOT, "corpus", "out")
sys.path.insert(0, os.path.join(ROOT, "src"))

from binviz.loader import MappedFile              # noqa: E402
from binviz.render import save_raster_png, save_signal_png  # noqa: E402
from binviz.signals import compute_signals        # noqa: E402
from binviz.surfaces import SurfaceRequest, get_surface     # noqa: E402

# (plate name, sample, surface, w, h, params, png scale)
PLATES = [
    # overall view, byte-class coloured, across the corpus classes
    ("linear_byteclass_ascii", "ascii.txt", "linear", 512, 256,
     {"mode": "byteclass"}, 1),
    ("linear_byteclass_static", "hello_static", "linear", 512, 256,
     {"mode": "byteclass"}, 1),
    ("linear_byteclass_upx", "hello_upx", "linear", 512, 256,
     {"mode": "byteclass"}, 1),
    ("linear_byteclass_zeros", "zeros.bin", "linear", 512, 256,
     {"mode": "byteclass"}, 1),
    ("linear_entropy_static", "hello_static", "linear", 512, 256,
     {"mode": "signal", "signal": "entropy_4096"}, 1),
    ("linear_entropy_upx", "hello_upx", "linear", 512, 256,
     {"mode": "signal", "signal": "entropy_4096"}, 1),
    # same data, Hilbert-laid-out: locality preserved at every row wrap
    ("hilbert_byteclass_static", "hello_static", "hilbert", 512, 512,
     {"mode": "byteclass"}, 1),
    ("hilbert_byteclass_upx", "hello_upx", "hilbert", 512, 512,
     {"mode": "byteclass"}, 1),
    # bigrams: the four corpus classes must be visually distinct
    ("ngram2_ascii", "ascii.txt", "ngram2", 256, 256, {}, 2),
    ("ngram2_static", "hello_static", "ngram2", 256, 256, {}, 2),
    ("ngram2_upx", "hello_upx", "ngram2", 256, 256, {}, 2),
    ("ngram2_urandom", "urandom.bin", "ngram2", 256, 256, {}, 2),
    ("ngram3_proj_static", "hello_static", "ngram3", 256, 256, {}, 2),
    # image view
    ("image_rgb_bars", "rgb_raw.bin", "image", 320, 240,
     {"mode": "rgb8", "width": 320}, 2),
    ("image_rgb_bars_wrong_stride", "rgb_raw.bin", "image", 321, 240,
     {"mode": "rgb8", "width": 321}, 2),
    ("image_bayer_rggb", "bayer_raw.bin", "image", 640, 480,
     {"mode": "bayer_RGGB_RGB_12", "width": 640}, 1),
    ("image_bayer_grbg_wrong", "bayer_raw.bin", "image", 640, 480,
     {"mode": "bayer_GRBG_RGB_12", "width": 640}, 1),
    ("image_grey_static", "hello_static", "image", 512, 512,
     {"mode": "grey8", "width": 512}, 1),
    # dot plot
    ("dotplot_repeats_exact", "repeats.bin", "dotplot", 256, 256,
     {"window": 8, "mode": "exact"}, 2),
    ("dotplot_repeats_sampled", "repeats.bin", "dotplot", 256, 256,
     {"window": 8, "mode": "sampled", "max_samples": 50_000}, 2),
    ("dotplot_ascii", "ascii.txt", "dotplot", 256, 256,
     {"window": 8, "mode": "exact", "end1": 200_000, "end2": 200_000}, 2),
    ("dotplot_urandom", "urandom.bin", "dotplot", 256, 256,
     {"window": 8, "mode": "exact", "end1": 200_000, "end2": 200_000}, 2),
]

SIGNAL_PLATES = [
    ("entropy_zeros", "zeros.bin", "entropy_4096"),
    ("entropy_urandom", "urandom.bin", "entropy_4096"),
    ("entropy_ascii", "ascii.txt", "entropy_4096"),
    ("entropy_pattern", "pattern.bin", "entropy_4096"),
    ("entropy_repeats", "repeats.bin", "entropy_4096"),
    ("entropy_hello_upx", "hello_upx", "entropy_256"),
    ("entropy_hello_static", "hello_static", "entropy_4096"),
]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    made = 0
    for name, sample, surface, w, h, params, scale in PLATES:
        path = os.path.join(CORPUS, sample)
        if not os.path.exists(path):
            print(f"[skip] {name}: {sample} not built")
            continue
        with MappedFile.open(path) as mf:
            req = SurfaceRequest(0, mf.size, w, h, "u8", params).clamp(mf.size)
            raster = get_surface(surface).render(mf.view, req)
            raster.pixels = raster.pixels.copy()
        save_raster_png(raster, os.path.join(OUT, f"{name}.png"), scale=scale)
        made += 1
        print(f"[plate] {name}.png  {raster.pixels.shape} {raster.kind}")

    for name, sample, signal in SIGNAL_PLATES:
        path = os.path.join(CORPUS, sample)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        sig = compute_signals(data, [signal])[signal]
        save_signal_png(sig.values, os.path.join(OUT, f"{name}.png"),
                        lo=sig.lo, hi=sig.hi, title=f"{signal}  {sample}")
        made += 1
        print(f"[plate] {name}.png  signal {signal}")

    print(f"\n{made} plates in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
