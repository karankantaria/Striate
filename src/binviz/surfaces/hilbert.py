"""Hilbert-curve whole-file layout — the reference's use_hilbert_curve_.

A linear strip breaks locality at every row wrap, smearing a contiguous
blob across rows; a Hilbert curve keeps nearby offsets nearby in 2-D, so
the same blob is a compact patch.

`xy2d` ships alongside `d2xy` because the inverse is what lets the user
click a pixel and get a file offset — the whole point of having the view
linked rather than decorative.
"""

from __future__ import annotations

import numpy as np

from ..elements import BYTE_CLASS_NAMES, byte_class
from .base import (Raster, SurfaceRequest, register, reduce_mode_class,
                   reduce_values, scale_to_u8)


def _rot(n: int, x: np.ndarray, y: np.ndarray,
         rx: np.ndarray, ry: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotate/flip a quadrant (vectorised form of the standard `rot`)."""
    swap = ry == 0
    flip = swap & (rx == 1)
    xf = np.where(flip, n - 1 - x, x)
    yf = np.where(flip, n - 1 - y, y)
    return np.where(swap, yf, xf), np.where(swap, xf, yf)


def d2xy(order: int, d) -> tuple[np.ndarray, np.ndarray]:
    """Curve index -> (x, y) for a 2**order square."""
    d = np.asarray(d, dtype=np.int64)
    n = 1 << order
    x = np.zeros_like(d)
    y = np.zeros_like(d)
    t = d.copy()
    s = 1
    while s < n:
        rx = 1 & (t >> 1)
        ry = 1 & (t ^ rx)
        x, y = _rot(s, x, y, rx, ry)
        x = x + s * rx
        y = y + s * ry
        t = t >> 2
        s <<= 1
    return x, y


def xy2d(order: int, x, y) -> np.ndarray:
    """(x, y) -> curve index for a 2**order square. Inverse of d2xy."""
    x = np.asarray(x, dtype=np.int64).copy()
    y = np.asarray(y, dtype=np.int64).copy()
    n = 1 << order
    d = np.zeros(np.broadcast(x, y).shape, dtype=np.int64)
    s = n >> 1
    while s > 0:
        rx = ((x & s) > 0).astype(np.int64)
        ry = ((y & s) > 0).astype(np.int64)
        d = d + s * s * ((3 * rx) ^ ry)
        x, y = _rot(n, x, y, rx, ry)
        s >>= 1
    return d


def order_for(width: int, height: int) -> int:
    """Largest order whose square fits: Hilbert requires a power-of-two square."""
    side = max(1, min(width, height))
    return max(0, int(np.floor(np.log2(side))))


def offset_at_xy(req: SurfaceRequest, order: int, x, y) -> np.ndarray:
    """File offset for a clicked Hilbert pixel — the linkage inverse."""
    d = xy2d(order, x, y)
    n_cells = 1 << (2 * order)
    return req.start + (d * req.nbytes) // n_cells


class HilbertSurface:
    name = "hilbert"

    def render(self, buf, req: SurfaceRequest) -> Raster:
        a = np.frombuffer(buf, dtype=np.uint8)[req.start:req.end]
        order = order_for(req.width, req.height)
        side = 1 << order
        n_cells = side * side
        mode = req.params.get("mode", "byteclass")
        meta: dict = {
            "mode": mode, "order": order, "side": side,
            "requested": [req.width, req.height],
            "start": req.start, "end": req.end,
            "bytes_per_cell": (req.nbytes / n_cells) if n_cells else 0,
            "warnings": [],
        }
        # Hilbert needs a power-of-two square: emit one and report the size
        # rather than rescaling a non-square raster behind the user's back.
        if (req.width, req.height) != (side, side):
            meta["warnings"].append(
                f"rendered {side}x{side} (largest power-of-two square fitting "
                f"{req.width}x{req.height})")

        if mode == "byteclass":
            cells = reduce_mode_class(byte_class(a), n_cells)
            meta["classes"] = list(BYTE_CLASS_NAMES)
            meta["value_range"] = [0, len(BYTE_CLASS_NAMES) - 1]
            meta["categorical"] = True
            values = cells
        elif mode == "value":
            how = req.params.get("reduce", "max")
            meta["reduce"] = how
            meta["value_range"] = [0, 255]
            values = scale_to_u8(reduce_values(a, n_cells, how), 0, 255)
        else:
            raise ValueError(f"unknown hilbert mode {mode!r}")

        d = np.arange(n_cells, dtype=np.int64)
        x, y = d2xy(order, d)
        pixels = np.zeros((side, side), dtype=np.uint8)
        pixels[y, x] = np.asarray(values, dtype=np.uint8)
        return Raster(pixels=pixels, kind="scalar", meta=meta)


register(HilbertSurface())
