"""Raw bytes as pixels — the reference's ImageView, all modes.

Packed formats: grey/rgb/bgr/rgba/bgra at 8, 12 and 16 bits.
Bayer CFA:     4 phases x 6 channel permutations = 24 modes.

Demosaicing is plain bilinear on purpose. This is a *format identification*
tool: the question is "does this suddenly look like a photograph", and
bilinear answers it. Anything fancier is an image pipeline nobody asked for.

The hidden control is row stride — wrong stride turns a photograph into
diagonal noise and users conclude there is no image (§5.7). Hence
`suggest_stride`, which turns the guessing game into a click.
"""

from __future__ import annotations

import numpy as np

from ..elements import elements
from .base import (MAX_IMAGE_WIDTH, Raster, SurfaceParamError,
                   SurfaceRequest, int_param, register)

CFA_PHASES = ("RGGB", "BGGR", "GRBG", "GBRG")
CHANNEL_PERMS = ("RGB", "RBG", "GRB", "GBR", "BRG", "BGR")
PIXEL_FORMATS = ("grey", "rgb", "bgr", "rgba", "bgra")
BIT_DEPTHS = (8, 12, 16)

# bayer8_0..23 in the reference's numbering: phase-major, permutation-minor
BAYER_MODES = tuple(f"bayer_{p}_{c}" for p in CFA_PHASES for c in CHANNEL_PERMS)

_CHANNELS = {"grey": 1, "rgb": 3, "bgr": 3, "rgba": 4, "bgra": 4}
_ORDER = {"rgb": (0, 1, 2), "bgr": (2, 1, 0),
          "rgba": (0, 1, 2), "bgra": (2, 1, 0)}
_DTYPE_FOR_DEPTH = {8: "u8", 12: "u12", 16: "u16le"}


def parse_mode(mode: str) -> dict:
    """`grey8`, `rgb12`, `bgra16`, `bayer_RGGB_RGB`, or `bayer8_7`."""
    if mode.startswith("bayer"):
        return _parse_bayer(mode)
    for fmt in sorted(PIXEL_FORMATS, key=len, reverse=True):
        if mode.startswith(fmt):
            depth = mode[len(fmt):] or "8"
            if not depth.isdigit() or int(depth) not in BIT_DEPTHS:
                raise SurfaceParamError(f"bad bit depth in mode {mode!r}; "
                                        f"expected one of {BIT_DEPTHS}")
            return {"kind": "packed", "format": fmt, "depth": int(depth)}
    raise SurfaceParamError(f"unknown image mode {mode!r}")


def _parse_bayer(mode: str) -> dict:
    body = mode[len("bayer"):]
    if body.startswith("_"):
        parts = body[1:].split("_")
        if len(parts) == 2 and parts[0] in CFA_PHASES and parts[1] in CHANNEL_PERMS:
            return {"kind": "bayer", "phase": parts[0], "perm": parts[1],
                    "depth": 8}
        if len(parts) == 3 and parts[0] in CFA_PHASES and parts[2].isdigit():
            return {"kind": "bayer", "phase": parts[0], "perm": parts[1],
                    "depth": int(parts[2])}
        raise SurfaceParamError(f"unknown bayer mode {mode!r}")
    # reference-compatible bayer<depth>_<index 0..23>. isdigit() guards
    # every int() here: `bayerXX_1` used to reach int("XX") and 500.
    depth_str, _, idx_str = body.partition("_")
    if depth_str and not depth_str.isdigit():
        raise SurfaceParamError(f"unknown bayer mode {mode!r}")
    depth = int(depth_str) if depth_str else 8
    if depth not in BIT_DEPTHS or not idx_str.isdigit():
        raise SurfaceParamError(f"unknown bayer mode {mode!r}")
    idx = int(idx_str)
    if not 0 <= idx < len(BAYER_MODES):
        raise SurfaceParamError(f"bayer index {idx} out of range 0..23")
    return {"kind": "bayer", "phase": CFA_PHASES[idx // len(CHANNEL_PERMS)],
            "perm": CHANNEL_PERMS[idx % len(CHANNEL_PERMS)], "depth": depth}


def bytes_per_pixel(spec: dict) -> float:
    n = 1 if spec["kind"] == "bayer" else _CHANNELS[spec["format"]]
    return n * spec["depth"] / 8.0


def _to_u8(vals: np.ndarray, depth: int) -> tuple[np.ndarray, float]:
    """Scale an element array to 8-bit for display, reporting the factor."""
    if depth == 8:
        return vals.astype(np.uint8), 1.0
    shift = {12: 4, 16: 8}[depth]
    return (vals >> shift).astype(np.uint8), 1.0 / (1 << shift)


def _conv3x3(a: np.ndarray, k: np.ndarray) -> np.ndarray:
    """3x3 convolution by shifted adds (no scipy dependency)."""
    p = np.pad(a, 1, mode="edge")
    out = np.zeros(a.shape, dtype=np.float64)
    h, w = a.shape
    for dy in range(3):
        for dx in range(3):
            if k[dy, dx]:
                out += k[dy, dx] * p[dy:dy + h, dx:dx + w]
    return out


_BILINEAR_K = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=np.float64)


def demosaic(mosaic: np.ndarray, phase: str) -> np.ndarray:
    """Bilinear demosaic of a single-plane CFA mosaic to (h, w, 3) RGB.

    Normalised convolution — interpolate the known samples and divide by
    the interpolated mask — so edges and the differing R/G/B sample
    densities are all handled by the same code path.
    """
    h, w = mosaic.shape
    # phase gives the 2x2 layout reading (0,0) (0,1) / (1,0) (1,1)
    layout = {"RGGB": "RGGB", "BGGR": "BGGR",
              "GRBG": "GRBG", "GBRG": "GBRG"}[phase]
    pos = {(0, 0): layout[0], (0, 1): layout[1],
           (1, 0): layout[2], (1, 1): layout[3]}
    out = np.empty((h, w, 3), dtype=np.float64)
    src = mosaic.astype(np.float64)
    for ci, cname in enumerate("RGB"):
        mask = np.zeros((h, w), dtype=np.float64)
        for (py, px), c in pos.items():
            if c == cname:
                mask[py::2, px::2] = 1.0
        num = _conv3x3(src * mask, _BILINEAR_K)
        den = _conv3x3(mask, _BILINEAR_K)
        out[:, :, ci] = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    return np.clip(out, 0, 255)


def detect_cfa_phase(mosaic: np.ndarray) -> list[dict]:
    """Rank the 4 CFA phases for a single-plane mosaic. The Bayer analogue
    of the stride suggester: don't make the user try 24 modes by hand.

    What is actually detectable is the *green lattice*. The two G samples in
    a 2x2 block are both green and adjacent, so they track each other
    closely; assume the wrong lattice and you are differencing R against B
    instead, which does not. Measured on the corpus sample the wrong lattice
    scores ~9500x worse, so the call is not marginal.

    What is NOT detectable from geometry: RGGB vs BGGR (and GRBG vs GBRG)
    are an exact R<->B swap and score identically. Distinguishing them needs
    scene semantics (sky, skin), so this returns them tied rather than
    inventing a winner.
    """
    m = mosaic.astype(np.float64)
    out = []
    for phase in CFA_PHASES:
        (y0, x0), (y1, x1) = [
            (yy, xx) for (yy, xx), c in _phase_positions(phase).items() if c == "G"
        ]
        a = m[y0::2, x0::2]
        b = m[y1::2, x1::2]
        n, k = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        if n == 0 or k == 0:
            score = float("inf")
        else:
            score = float(np.mean((a[:n, :k] - b[:n, :k]) ** 2))
        out.append({"phase": phase, "g_pair_mse": score})
    out.sort(key=lambda d: d["g_pair_mse"])
    best = out[0]["g_pair_mse"]
    for d in out:
        d["relative"] = (d["g_pair_mse"] / best) if best > 0 else 1.0
    ties = [d["phase"] for d in out if d["relative"] <= 1.0 + 1e-9]
    for d in out:
        d["tied_with"] = [p for p in ties if p != d["phase"]] \
            if d["phase"] in ties else []
    return out


def _phase_positions(phase: str) -> dict:
    return {(0, 0): phase[0], (0, 1): phase[1],
            (1, 0): phase[2], (1, 1): phase[3]}


def suggest_stride(buf, start: int = 0, end: int = -1, *,
                   lo: int = 64, hi: int = 8192, top: int = 3,
                   max_bytes: int = 1 << 20) -> list[dict]:
    """Top candidate row strides via FFT autocorrelation (§5.7).

    Returns byte lags plus, when the caller supplies a dtype, the pixel
    stride they imply. ~15 lines of numpy that convert the tool's biggest
    usability cliff into a click.
    """
    a = np.frombuffer(buf, dtype=np.uint8)
    end = len(a) if end < 0 else end
    a = a[start:end][:max_bytes].astype(np.float64)
    if a.size < 2 * lo:
        return []
    a = a - a.mean()
    n = 1 << int(np.ceil(np.log2(a.size * 2)))
    spec = np.fft.rfft(a, n)
    ac = np.fft.irfft(spec * np.conj(spec), n)[: a.size]
    if ac[0] > 0:
        ac = ac / ac[0]
    hi = min(hi, a.size // 2)
    if hi <= lo:
        return []
    band = ac[lo:hi]
    # local maxima only: adjacent samples of one peak are not three candidates
    is_peak = np.r_[False, (band[1:-1] > band[:-2]) & (band[1:-1] >= band[2:]),
                    False]
    idx = np.flatnonzero(is_peak)
    if idx.size == 0:
        return []
    order = idx[np.argsort(-band[idx])]
    cands: list[dict] = []

    def add(lag: int, origin: str) -> None:
        if not lo <= lag < hi:
            return
        if any(abs(lag - c["bytes"]) <= max(2, 0.01 * c["bytes"]) for c in cands):
            return
        cands.append({"bytes": lag, "score": float(ac[lag]), "origin": origin})

    # Strongest peak first, each immediately followed by its sub-multiples.
    # Sub-multiples are not optional polish: image data whose rows alternate
    # (a Bayer CFA, interlaced fields, alternating row content) correlates at
    # 2x the row stride. On the corpus Bayer sample the true 960-byte stride
    # scores 0.001 while 1920 scores 0.84 — rank on peak height alone and the
    # suggester confidently proposes exactly double the right answer.
    for i in order:
        lag = int(i + lo)
        add(lag, "peak")
        for div in (2, 3):
            if lag % div == 0:
                add(lag // div, f"peak/{div}")
        if len(cands) >= top:
            break
    return cands[:top]


def suggest_stride_pixels(buf, mode: str, **kw) -> list[dict]:
    """Stride candidates expressed in pixels for a given image mode."""
    spec = parse_mode(mode)
    bpp = bytes_per_pixel(spec)
    out = []
    for c in suggest_stride(buf, **kw):
        px = c["bytes"] / bpp
        out.append({**c, "pixels": int(round(px)),
                    "exact": abs(px - round(px)) < 1e-6})
    return out


class ImageSurface:
    name = "image"

    def render(self, buf, req: SurfaceRequest) -> Raster:
        mode = req.params.get("mode", "grey8")
        spec = parse_mode(mode)
        # width=0 divided by zero, width=-5 failed the reshape, and
        # width=abc never got past int() — all three were 500s (S3).
        width = int_param(req.params, "width", req.width,
                          lo=1, hi=MAX_IMAGE_WIDTH)
        invert = bool(req.params.get("invert", False))
        max_rows = int_param(req.params, "max_rows", 4096,
                             lo=1, hi=MAX_IMAGE_WIDTH)

        raw = np.frombuffer(buf, dtype=np.uint8)[req.start:req.end]
        vals = elements(raw.tobytes(), _DTYPE_FOR_DEPTH[spec["depth"]])
        px8, scale = _to_u8(vals, spec["depth"])
        meta: dict = {"mode": mode, "width": width, "invert": invert,
                      "bytes_per_pixel": bytes_per_pixel(spec),
                      "depth_scale": scale, "warnings": []}
        if spec["depth"] != 8:
            meta["warnings"].append(
                f"{spec['depth']}-bit samples scaled by {scale} for display")

        if spec["kind"] == "bayer":
            rgb = self._render_bayer(px8, width, spec, meta)
        else:
            rgb = self._render_packed(px8, width, spec, meta)

        if rgb.shape[0] > max_rows:
            meta["warnings"].append(
                f"{rgb.shape[0]} rows truncated to {max_rows}")
            rgb = rgb[:max_rows]
        if invert:
            rgb = 255 - rgb
        meta["height"] = int(rgb.shape[0])
        return Raster(pixels=np.ascontiguousarray(rgb, dtype=np.uint8),
                      kind="rgb", meta=meta)

    def _render_packed(self, px8, width, spec, meta) -> np.ndarray:
        fmt = spec["format"]
        nch = _CHANNELS[fmt]
        per_row = width * nch
        rows = len(px8) // per_row
        if rows == 0:
            meta["warnings"].append("range shorter than one row at this stride")
            return np.zeros((1, max(1, width), 3), dtype=np.uint8)
        dropped = len(px8) - rows * per_row
        if dropped:
            meta["warnings"].append(f"{dropped} trailing sample(s) dropped")
        block = px8[: rows * per_row].reshape(rows, width, nch)
        if fmt == "grey":
            return np.repeat(block, 3, axis=2)
        r, g, b = _ORDER[fmt]
        return np.stack([block[:, :, r], block[:, :, g], block[:, :, b]], axis=2)

    def _render_bayer(self, px8, width, spec, meta) -> np.ndarray:
        rows = len(px8) // width
        if rows < 2:
            meta["warnings"].append("range shorter than two rows at this stride")
            return np.zeros((1, max(1, width), 3), dtype=np.uint8)
        rows -= rows % 2  # CFA needs whole 2x2 blocks
        mosaic = px8[: rows * width].reshape(rows, width)
        rgb = demosaic(mosaic, spec["phase"])
        perm = spec["perm"]
        idx = ["RGB".index(c) for c in perm]
        meta.update({"cfa_phase": spec["phase"], "channel_perm": perm})
        return rgb[:, :, idx]


register(ImageSurface())
