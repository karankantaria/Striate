"""Element reinterpretation: raw bytes are not always bytes.

One reinterpretation layer feeds the histograms, the image view, and the
plot view. The reference project implements this idea twice (histo dtypes
and pixel dtypes); here it exists once.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

Dtype = Literal["u8", "u12", "u16le", "u16be", "u32le", "u32be",
                "u64le", "u64be", "f32le", "f32be", "f64le", "f64be"]

_NP_DTYPES: dict[str, str] = {
    "u8": "u1",
    "u16le": "<u2", "u16be": ">u2",
    "u32le": "<u4", "u32be": ">u4",
    "u64le": "<u8", "u64be": ">u8",
    "f32le": "<f4", "f32be": ">f4",
    "f64le": "<f8", "f64be": ">f8",
}

DTYPES: tuple[str, ...] = ("u8", "u12") + tuple(_NP_DTYPES)[1:]


def element_width_bits(dtype: Dtype) -> int:
    if dtype == "u12":
        return 12
    return int(np.dtype(_NP_DTYPES[dtype]).itemsize) * 8


def element_info(nbytes: int, dtype: Dtype) -> dict:
    """How many whole elements fit, and how many tail bytes get dropped.

    Truncation is reported, never silent: the caller shows it in the UI.
    """
    if dtype == "u12":
        count = (nbytes // 3) * 2
        used = (nbytes // 3) * 3
    else:
        w = np.dtype(_NP_DTYPES[dtype]).itemsize
        count = nbytes // w
        used = count * w
    return {"count": count, "dropped_tail_bytes": nbytes - used}


def elements(buf, dtype: Dtype) -> np.ndarray:
    """Reinterpret raw bytes as a 1-D array of elements (no copy where possible).

    u12 is *packed* sensor layout — two 12-bit elements per three bytes:
    [a_hi] [a_lo | b_hi] [b_lo]  (the convention recorded in the corpus
    manifest as `u12_packing`). Tail bytes that don't complete an element
    are truncated; use element_info() to report the drop.
    """
    raw = np.frombuffer(buf, dtype=np.uint8)
    if dtype == "u12":
        n_trip = len(raw) // 3
        trip = raw[: n_trip * 3].reshape(n_trip, 3).astype(np.uint16)
        out = np.empty(n_trip * 2, dtype=np.uint16)
        out[0::2] = (trip[:, 0] << 4) | (trip[:, 1] >> 4)
        out[1::2] = ((trip[:, 1] & 0xF) << 8) | trip[:, 2]
        return out
    nd = np.dtype(_NP_DTYPES[dtype])
    n = len(raw) // nd.itemsize
    return np.frombuffer(buf, dtype=nd, count=n)


def quantise(vals: np.ndarray, dtype: Dtype,
             lo=None, hi=None) -> tuple[np.ndarray, dict]:
    """Map elements to uint8 bin indices 0..255 for histogramming.

    The quantisation choice changes what a histogram *means*, so the method
    and bounds are returned in metadata and must be surfaced in the UI.
    Integers: linear over [min, max] of the data (or explicit lo/hi).
    Floats: linear over the 0.5th-99.5th percentile of finite values —
    raw min/max on float data is almost always destroyed by one outlier.
    Non-finite floats map to bin 0 and are counted, never propagated.
    """
    meta: dict = {"dtype": dtype, "n": int(vals.size)}
    if vals.size == 0:
        return np.zeros(0, dtype=np.uint8), {**meta, "lo": 0, "hi": 0,
                                             "method": "empty"}
    if dtype == "u8":
        return np.asarray(vals, dtype=np.uint8), {
            **meta, "lo": 0, "hi": 255, "method": "identity"}

    if dtype.startswith("f"):
        finite = np.isfinite(vals)
        n_bad = int(vals.size - finite.sum())
        fv = vals[finite] if n_bad else vals
        if fv.size == 0:
            return np.zeros(vals.size, dtype=np.uint8), {
                **meta, "lo": 0.0, "hi": 0.0, "method": "percentile",
                "n_nonfinite": n_bad}
        p_lo = float(np.percentile(fv, 0.5)) if lo is None else float(lo)
        p_hi = float(np.percentile(fv, 99.5)) if hi is None else float(hi)
        if p_hi <= p_lo:
            p_hi = p_lo + 1.0
        scaled = (np.clip(vals, p_lo, p_hi) - p_lo) * (255.0 / (p_hi - p_lo))
        if n_bad:
            scaled = np.where(finite, scaled, 0.0)  # nonfinite -> bin 0
        bins = scaled.astype(np.uint8)
        return bins, {**meta, "lo": p_lo, "hi": p_hi,
                      "method": "percentile", "n_nonfinite": n_bad}

    v_lo = int(vals.min()) if lo is None else int(lo)
    v_hi = int(vals.max()) if hi is None else int(hi)
    if v_hi <= v_lo:
        return np.zeros(vals.size, dtype=np.uint8), {
            **meta, "lo": v_lo, "hi": v_hi, "method": "constant"}
    # float64 scaling loses precision above 2^53 (u64) — recorded by method
    scaled = (vals.astype(np.float64) - v_lo) * (255.0 / (v_hi - v_lo))
    return np.clip(scaled, 0, 255).astype(np.uint8), {
        **meta, "lo": v_lo, "hi": v_hi, "method": "linear"}


# byte classes: the 6-class palette that makes the overall view readable.
# Null and 0xFF get their own classes deliberately — zero padding and erased
# flash are the two most common bulk fills.
CLASS_NULL, CLASS_PRINTABLE, CLASS_WHITESPACE = 0, 1, 2
CLASS_CONTROL, CLASS_HIGH, CLASS_FF = 3, 4, 5

_CLASS_LUT = np.empty(256, dtype=np.uint8)
_CLASS_LUT[:] = CLASS_CONTROL                      # other <0x20 and 0x7F
_CLASS_LUT[0x20:0x7F] = CLASS_PRINTABLE
_CLASS_LUT[[0x09, 0x0A, 0x0B, 0x0C, 0x0D]] = CLASS_WHITESPACE
_CLASS_LUT[0x7F] = CLASS_CONTROL
_CLASS_LUT[0x80:0xFF] = CLASS_HIGH
_CLASS_LUT[0xFF] = CLASS_FF
_CLASS_LUT[0x00] = CLASS_NULL

BYTE_CLASS_NAMES = ("null", "printable", "whitespace", "control", "high", "0xff")


def byte_class(buf) -> np.ndarray:
    """Per-byte class id (0..5), vectorised through a LUT."""
    return _CLASS_LUT[np.frombuffer(buf, dtype=np.uint8)]
