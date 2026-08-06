"""2-D bigram raster and 3-D trigram point extraction.

The display transform lives here, not in the UI: raw bigram counts span
6+ orders of magnitude, so a linear mapping shows one bright pixel at
(0,0) and black everywhere else. The transform decides what the plot
*means*, so it is chosen in the analysis layer and recorded in meta.
"""

from __future__ import annotations

import numpy as np

from ..elements import elements, element_info, quantise
from ..stats import ngram
from .base import (Raster, SurfaceParamError, SurfaceRequest,
                   int_param, register)

DISPLAY_MODES = ("log1p", "rank", "sqrt", "linear")


def to_display(counts: np.ndarray, mode: str = "log1p") -> np.ndarray:
    """Counts -> uint8 display values.

    log1p  general purpose default
    rank   percentile-flattened; best for faint structure
    sqrt   gentler than log, keeps some magnitude sense
    linear raw; almost always shows one bright cell and nothing else
    """
    c = np.asarray(counts, dtype=np.float64)
    if mode == "linear":
        v = c
    elif mode == "sqrt":
        v = np.sqrt(c)
    elif mode == "log1p":
        v = np.log1p(c)
    elif mode == "rank":
        flat = c.ravel()
        v = np.zeros_like(flat)
        nz = flat > 0
        if nz.any():
            order = flat[nz].argsort().argsort().astype(np.float64)
            v[nz] = order + 1.0
        v = v.reshape(c.shape)
    else:
        raise SurfaceParamError(
            f"unknown display mode {mode!r}; known: {DISPLAY_MODES}")
    peak = float(v.max()) if v.size else 0.0
    if peak <= 0:
        return np.zeros(c.shape, dtype=np.uint8)
    return np.clip(v * (255.0 / peak), 0, 255).astype(np.uint8)


def _quantised_bins(buf, req: SurfaceRequest) -> tuple[np.ndarray, dict]:
    raw = np.frombuffer(buf, dtype=np.uint8)[req.start:req.end]
    vals = elements(raw.tobytes(), req.dtype)
    bins, qmeta = quantise(vals, req.dtype)
    qmeta.update(element_info(raw.size, req.dtype))
    return bins, qmeta


class Ngram2Surface:
    """256x256 bigram raster. Always 256x256 — width/height are ignored,
    since the axes are byte values, not screen space."""

    name = "ngram2"

    def render(self, buf, req: SurfaceRequest) -> Raster:
        bins, qmeta = _quantised_bins(buf, req)
        mode = req.params.get("display", "log1p")
        counts = ngram(bins, 2)
        meta = {
            "display": mode, "dtype": req.dtype, "quantise": qmeta,
            "nonzero_cells": int((counts > 0).sum()),
            "total": int(counts.sum()), "max_count": int(counts.max()),
            "axes": "x = first element bin, y = second element bin",
            "warnings": [],
        }
        if qmeta.get("dropped_tail_bytes"):
            meta["warnings"].append(
                f"{qmeta['dropped_tail_bytes']} tail byte(s) dropped: range is "
                f"not a whole number of {req.dtype} elements")
        return Raster(pixels=to_display(counts, mode), kind="scalar", meta=meta)


class Ngram3Points:
    """Sparse trigram point extraction for the WebGL cloud — not a raster.

    Returns (coords (N,3) uint8, counts (N,) uint32) after thresholding,
    matching the reference's threshold/scale spinboxes.
    """

    name = "ngram3"

    def points(self, buf, req: SurfaceRequest) -> tuple[np.ndarray, np.ndarray, dict]:
        bins, qmeta = _quantised_bins(buf, req)
        threshold = int_param(req.params, "threshold", 1, lo=1)
        max_points = int_param(req.params, "max_points", 0, lo=0)
        coords, counts = ngram(bins, 3)
        meta = {"dtype": req.dtype, "quantise": qmeta, "threshold": threshold,
                "total_points": int(len(counts)), "warnings": []}
        if threshold > 1:
            keep = counts >= threshold
            coords, counts = coords[keep], counts[keep]
        if max_points and len(counts) > max_points:
            # keep the strongest points and say so — never silently truncate
            keep = np.argpartition(counts, -max_points)[-max_points:]
            keep = keep[np.argsort(-counts[keep])]
            coords, counts = coords[keep], counts[keep]
            meta["warnings"].append(
                f"showing the {max_points} strongest of {meta['total_points']} "
                "points")
        meta["shown_points"] = int(len(counts))
        meta["max_count"] = int(counts.max()) if len(counts) else 0
        return coords, counts, meta

    def render(self, buf, req: SurfaceRequest) -> Raster:
        """Projection to 2-D so the point cloud has a headless artifact:
        x/y plane, brightest count along z."""
        coords, counts, meta = self.points(buf, req)
        img = np.zeros((256, 256), dtype=np.float64)
        if len(counts):
            np.maximum.at(img, (coords[:, 1], coords[:, 0]),
                          counts.astype(np.float64))
        meta["projection"] = "max over third element (z)"
        return Raster(pixels=to_display(img, req.params.get("display", "log1p")),
                      kind="scalar", meta=meta)


register(Ngram2Surface())
register(Ngram3Points())
