"""Surface protocol plus the cell-mapping reducers every surface shares.

Scalar rasters ship raw and are coloured client-side, so changing colormap
never refetches. RGB rasters (image view) ship as PNG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..elements import Dtype

N_BYTE_CLASSES = 6

#: Ceiling on a requested raster dimension (S4). `clamp` used to be a floor
#: only — `max(1, w)` — so `?w=20000&h=20000` asked for a 400-million-cell
#: raster from a single GET, which did not return inside 40 s. 4096 is well
#: past any real display and bounds the allocation at ~16M cells.
MAX_RASTER_DIM = 4096

#: Image row width is a different axis: it is a stride in pixels, not a
#: canvas size, so it gets a looser bound. It still needs *a* bound, because
#: the "range shorter than one row" path allocates a `width`-wide row of
#: zeros before it can report the problem.
MAX_IMAGE_WIDTH = 1 << 16


class SurfaceParamError(ValueError):
    """A surface parameter is missing, non-numeric, or out of range.

    Subclasses ValueError so existing `except ValueError` handlers keep
    working, but is distinct so the HTTP layer can map *these* to 400
    without also turning a genuine bug deeper in the render path into one.
    A 500 for a malformed query string is wrong; a 400 for an internal
    error is worse, because it blames the caller.
    """


def int_param(params: dict, key: str, default, *,
              lo: int | None = None, hi: int | None = None) -> int:
    """Read an integer surface parameter, or raise SurfaceParamError.

    Query strings arrive already coerced by the service's `_typed`, so a
    value can be str, int, float or bool by the time it lands here. Never
    let one reach a bare `int()`: `int("abc")` is a 500 with a traceback.
    """
    raw = params.get(key, default)
    if isinstance(raw, bool):
        raise SurfaceParamError(f"{key!r} must be a number, got {raw!r}")
    try:
        # OverflowError matters: the service coerces "1e999" to float
        # inf before this sees it, and int(inf) overflows rather than
        # raising ValueError.
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise SurfaceParamError(f"{key!r} must be a number, got {raw!r}")
    if lo is not None and value < lo:
        raise SurfaceParamError(f"{key!r} must be >= {lo}, got {value}")
    if hi is not None and value > hi:
        raise SurfaceParamError(f"{key!r} must be <= {hi}, got {value}")
    return value


def choice_param(params: dict, key: str, default: str, allowed) -> str:
    value = params.get(key, default)
    if value not in allowed:
        raise SurfaceParamError(
            f"unknown {key} {value!r}; known: {sorted(allowed)}")
    return value


@dataclass(frozen=True)
class SurfaceRequest:
    start: int
    end: int
    width: int
    height: int
    dtype: Dtype = "u8"
    params: dict = field(default_factory=dict)

    def clamp(self, size: int, max_dim: int = MAX_RASTER_DIM) -> "SurfaceRequest":
        start = max(0, min(self.start, size))
        end = max(start, min(self.end if self.end >= 0 else size, size))
        return SurfaceRequest(start, end,
                              min(max(1, self.width), max_dim),
                              min(max(1, self.height), max_dim),
                              self.dtype, self.params)

    @property
    def nbytes(self) -> int:
        return self.end - self.start

    def cache_key(self) -> tuple:
        return (self.start, self.end, self.width, self.height, self.dtype,
                tuple(sorted((k, _hashable(v)) for k, v in self.params.items())))


def _hashable(v):
    if isinstance(v, (list, tuple)):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted((k, _hashable(x)) for k, x in v.items()))
    return v


@dataclass
class Raster:
    pixels: np.ndarray     # uint8 (h,w) scalar OR uint8 (h,w,3) rgb
    kind: str              # "scalar" | "rgb"
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.pixels.shape[0], self.pixels.shape[1]


class Surface(Protocol):
    name: str

    def render(self, buf: memoryview, req: SurfaceRequest) -> Raster: ...


# --------------------------------------------------------------- reducers

def cell_index(n_items: int, n_cells: int) -> np.ndarray:
    """Map each item to an output cell, spreading items evenly."""
    if n_items <= 0:
        return np.zeros(0, dtype=np.int64)
    return (np.arange(n_items, dtype=np.int64) * n_cells) // n_items


def reduce_mode_class(classes: np.ndarray, n_cells: int) -> np.ndarray:
    """Most common class per cell.

    Byte-class rasters MUST aggregate by mode, not mean: averaging class ids
    invents classes that do not exist (§5.2). Ties resolve to the lowest
    class id, which is deterministic and therefore testable.
    """
    if classes.size == 0:
        return np.zeros(n_cells, dtype=np.uint8)
    cells = cell_index(classes.size, n_cells)
    idx = cells * N_BYTE_CLASSES + classes.astype(np.int64)
    counts = np.bincount(idx, minlength=n_cells * N_BYTE_CLASSES)
    return counts.reshape(n_cells, N_BYTE_CLASSES).argmax(axis=1).astype(np.uint8)


def reduce_values(values: np.ndarray, n_cells: int,
                  how: str = "max") -> np.ndarray:
    """Aggregate a numeric series to n_cells by max (default), mean, or min.

    Default is max, not mean: a single high-entropy window inside an
    otherwise quiet range is exactly the thing the user opened the tool to
    find, and mean aggregation erases it (§5.2).
    """
    if values.size == 0:
        return np.zeros(n_cells, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    bounds = (np.arange(n_cells + 1, dtype=np.int64) * v.size) // n_cells
    starts = np.minimum(bounds[:-1], v.size - 1)
    if how == "max":
        return np.maximum.reduceat(v, starts)
    if how == "min":
        return np.minimum.reduceat(v, starts)
    if how == "mean":
        cs = np.concatenate([[0.0], np.cumsum(v)])
        seg = np.diff(bounds)
        out = np.where(seg > 0, (cs[bounds[1:]] - cs[bounds[:-1]])
                       / np.maximum(seg, 1), v[starts])
        return out
    raise SurfaceParamError(f"unknown reducer {how!r}; "
                            f"known: ['max', 'mean', 'min']")


def scale_to_u8(values: np.ndarray, lo: float, hi: float) -> np.ndarray:
    if hi <= lo:
        return np.zeros(values.shape, dtype=np.uint8)
    scaled = (np.asarray(values, dtype=np.float64) - lo) * (255.0 / (hi - lo))
    return np.clip(scaled, 0, 255).astype(np.uint8)


# --------------------------------------------------------------- registry

SURFACES: dict[str, Surface] = {}


def register(surface: Surface) -> Surface:
    SURFACES[surface.name] = surface
    return surface


def get_surface(name: str) -> Surface:
    if name not in SURFACES:
        _load_all()
    if name not in SURFACES:
        raise KeyError(f"unknown surface {name!r}; known: {sorted(SURFACES)}")
    return SURFACES[name]


def _load_all() -> None:
    from . import dotplot, hilbert, image, linear, ngram  # noqa: F401
