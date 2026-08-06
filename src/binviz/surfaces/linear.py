"""Linear (row-major) whole-file map — the reference's overall view.

Modes:
  byteclass  6-class categorical map, aggregated by mode
  value      raw byte values, aggregated by max/mean/min
  signal     a named signal (entropy_4096, ...) painted over the same surface
"""

from __future__ import annotations

import numpy as np

from ..elements import BYTE_CLASS_NAMES, byte_class
from .base import (Raster, SurfaceParamError, SurfaceRequest,
                   choice_param, register, reduce_mode_class,
                   reduce_values, scale_to_u8)


class LinearSurface:
    name = "linear"

    def render(self, buf, req: SurfaceRequest) -> Raster:
        a = np.frombuffer(buf, dtype=np.uint8)[req.start:req.end]
        w, h = req.width, req.height
        n_cells = w * h
        mode = req.params.get("mode", "byteclass")
        meta: dict = {"mode": mode, "start": req.start, "end": req.end,
                      "bytes_per_cell": (req.nbytes / n_cells) if n_cells else 0,
                      "warnings": []}

        if mode == "byteclass":
            cells = reduce_mode_class(byte_class(a), n_cells)
            meta["classes"] = list(BYTE_CLASS_NAMES)
            meta["value_range"] = [0, len(BYTE_CLASS_NAMES) - 1]
            meta["categorical"] = True
            pixels = cells
        elif mode == "value":
            how = req.params.get("reduce", "max")
            cells = reduce_values(a, n_cells, how)
            meta["reduce"] = how
            meta["value_range"] = [0, 255]
            pixels = scale_to_u8(cells, 0, 255)
        elif mode == "signal":
            from ..signals import SIGNALS, compute_signals

            # unknown name used to KeyError out of compute_signals -> 500
            name = choice_param(req.params, "signal", "entropy_4096", SIGNALS)
            how = req.params.get("reduce", "max")
            sig = compute_signals(a.tobytes(), [name])[name]
            cells = reduce_values(sig.values, n_cells, how)
            meta.update({"signal": name, "reduce": how, "unit": sig.unit,
                         "value_range": [sig.lo, sig.hi],
                         "windows": int(sig.values.size)})
            if sig.values.size == 0:
                meta["warnings"].append(
                    f"range shorter than one {SIGNALS[name][0]}-byte window")
            elif sig.values.size < n_cells:
                meta["warnings"].append(
                    f"{sig.values.size} windows painted over {n_cells} cells "
                    "(upsampled)")
            pixels = scale_to_u8(cells, sig.lo, sig.hi)
        else:
            raise SurfaceParamError(f"unknown linear mode {mode!r}")

        if req.nbytes < n_cells:
            meta["warnings"].append(
                f"{req.nbytes} bytes over {n_cells} cells (upsampled)")
        return Raster(pixels=np.asarray(pixels, dtype=np.uint8).reshape(h, w),
                      kind="scalar", meta=meta)


register(LinearSurface())
