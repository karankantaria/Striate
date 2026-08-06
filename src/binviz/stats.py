"""Statistical kernels: windowed entropy, n-grams, chi-square, reducers.

Everything is chunked so a 100 MB file never materialises more than a few
tens of MB of transients (§5.13). Entropy uses the c*log2(c) lookup-table
identity  H = log2(w) - (1/w) * sum(c * log2 c)  so the hot loop is one
fancy-index and one row sum per window chunk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from .elements import CLASS_PRINTABLE, CLASS_WHITESPACE, _CLASS_LUT

# Windowed stats keep a (rows x 256) int64 count matrix per chunk. At 64K
# elements that is 512 KB for a 256-byte window — L2-resident. Doubling the
# chunk pushes it out of L2 and costs 2x wall time (measured, with
# bit-identical output), so this constant is a cache bound, not a guess.
_WINDOW_CHUNK_ELEMS = 1 << 16
# The bigram wants the opposite: its table is a fixed 512 KB, so larger
# chunks amortise the per-call allocation. Measured 1.07 s at 4M vs 1.55 s
# at 64K on the same 100 MB input.
_TARGET_CHUNK_ELEMS = 1 << 22
_TRIGRAM_CHUNK_ELEMS = 1 << 23  # trigram keys are uint32: keep the chunk small
_TRIGRAM_BLOCK_BITS = 4         # 16 key blocks -> cache-resident count tables
# distinct keys per chunk above which the sparse path loses to the dense one
_TRIGRAM_SPARSE_MAX_UNIQUE = 1 << 21
# cumulative distinct-key budget across chunks. The final sparse merge peaks
# at ~24 bytes/key (concat + int64 argsort + reordered copies), so 2^25 keys
# caps the merge transient near 0.8 GB. Without this bound a mixed file that
# opens binary-like and turns random accumulates the random section's keys
# under the sparse strategy: measured 85 s and +10.3 GiB commit on a 2 GiB
# file with a 512 MiB random section (P12).
_TRIGRAM_SPARSE_TOTAL_MAX = 1 << 25


def histogram(bins: np.ndarray) -> np.ndarray:
    """(256,) uint32 counts of uint8 bin indices.

    Chunked: bincount casts its input to int64 internally, so one call over
    the whole array materialises 8x the input size (measured 16.75 GiB of
    commit charge on a 2 GiB file — P12). Counts saturate at uint32 max
    rather than wrapping, matching the <u4 wire format.
    """
    a = np.asarray(bins)
    out = np.zeros(256, dtype=np.int64)
    for i in range(0, len(a), _TARGET_CHUNK_ELEMS):
        out += np.bincount(a[i : i + _TARGET_CHUNK_ELEMS],
                           minlength=256)[:256]
    return np.minimum(out, np.iinfo(np.uint32).max).astype(np.uint32)


def chi2_uniform(hist: np.ndarray) -> float:
    """Chi-square statistic against the uniform 256-symbol distribution."""
    n = int(hist.sum())
    if n == 0:
        return 0.0
    e = n / 256.0
    return float(((hist.astype(np.float64) - e) ** 2).sum() / e)


# ------------------------------------------------------------ windowing

def _iter_window_counts(
    a: np.ndarray, window: int, stride: int
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (first_window_index, (rows, 256) int64 counts) in chunks.

    Only full windows are produced; a short tail is dropped (callers report
    coverage via the offsets array). stride == window is the fast path.
    """
    if len(a) < window:
        return
    n_win = (len(a) - window) // stride + 1
    rows_per = max(1, _WINDOW_CHUNK_ELEMS // window)
    if stride != window:
        view = np.lib.stride_tricks.sliding_window_view(a, window)[::stride]
    for r0 in range(0, n_win, rows_per):
        r1 = min(r0 + rows_per, n_win)
        rows = r1 - r0
        if stride == window:
            seg = a[r0 * window : r1 * window].reshape(rows, window)
        else:
            seg = view[r0:r1]
        idx = seg.astype(np.int64)
        idx += (np.arange(rows, dtype=np.int64) << 8)[:, None]
        counts = np.bincount(idx.ravel(), minlength=rows * 256)
        yield r0, counts.reshape(rows, 256)


def _entropy_lut(window: int) -> np.ndarray:
    c = np.arange(window + 1, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        lut = c * np.log2(c)
    lut[0] = 0.0
    return lut


@dataclass
class EntropyProfile:
    window: int
    stride: int
    values: np.ndarray     # float32, bits/byte in [0, 8]
    offsets: np.ndarray    # int64, window start offsets

    def bin(self, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Reduce to exactly n bins; returns (min, mean, max) per bin."""
        return reduce_minmeanmax(self.values, n)


def entropy_profile(buf, window: int, stride: int | None = None) -> EntropyProfile:
    stride = window if stride is None else stride
    a = np.frombuffer(buf, dtype=np.uint8)
    n_win = (len(a) - window) // stride + 1 if len(a) >= window else 0
    values = np.empty(n_win, dtype=np.float32)
    lut = _entropy_lut(window)
    log2w = np.log2(window)
    inv_w = 1.0 / window
    for r0, counts in _iter_window_counts(a, window, stride):
        values[r0 : r0 + len(counts)] = log2w - lut[counts].sum(axis=1) * inv_w
    np.clip(values, 0.0, 8.0, out=values)
    offsets = np.arange(n_win, dtype=np.int64) * stride
    return EntropyProfile(window=window, stride=stride,
                          values=values, offsets=offsets)


def reduce_minmeanmax(
    values: np.ndarray, n: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reduce a series to exactly n bins of (min, mean, max).

    The max channel is what keeps a single-window spike visible after
    downsampling (§5.2) — never render from mean alone. If n exceeds the
    series length, bins repeat the nearest value (upsampling is honest).
    """
    v = np.asarray(values, dtype=np.float32)
    n = max(1, int(n))
    if v.size == 0:
        z = np.zeros(n, dtype=np.float32)
        return z, z.copy(), z.copy()
    bounds = (np.arange(n + 1, dtype=np.int64) * v.size) // n
    starts = np.minimum(bounds[:-1], v.size - 1)
    mins = np.minimum.reduceat(v, starts)
    maxs = np.maximum.reduceat(v, starts)
    cs = np.concatenate([[0.0], np.cumsum(v, dtype=np.float64)])
    seg_len = np.diff(bounds)
    seg_sum = cs[bounds[1:]] - cs[bounds[:-1]]
    means = np.where(seg_len > 0, seg_sum / np.maximum(seg_len, 1),
                     v[starts]).astype(np.float32)
    # reduceat on an empty segment returns v[start] — already the honest value
    return mins.astype(np.float32), means, maxs.astype(np.float32)


# ------------------------------------------------------------ n-grams

def ngram(bins: np.ndarray, n: int, progress=None):
    """n-gram counts over uint8 bin indices (sliding, overlapping).

    n=1 -> (256,) uint32
    n=2 -> (256, 256) uint32
    n=3 -> sparse: (coords (N,3) uint8, counts (N,) uint32). The dense 256^3
           accumulator exists only as one transient; only sparse is returned.

    `progress`, when given, is called with a fraction in (0, 1] as chunks
    complete (n=2 and n=3 only — n=1 is one fast pass).
    """
    a = np.asarray(bins, dtype=np.uint8)
    if n == 1:
        return histogram(a)
    if n == 2:
        # Every consecutive pair is covered by the uint16 views of a and a[1:],
        # so no index arithmetic is needed at all: measured 2.2x faster and
        # 20x lighter than shift/or into an int64 index array. '<u2' is
        # explicit so the packing is host-endianness independent; the view
        # yields (second << 8) | first, hence the final transpose.
        acc = np.zeros(1 << 16, dtype=np.uint32)
        done = 0
        # exactly the elements the two offset passes will process
        total = max(1, len(a) // 2 + max(0, len(a) - 1) // 2)
        for off in (0, 1):
            b = a[off:]
            b = b[: len(b) // 2 * 2]
            if b.size == 0:
                continue
            v = np.ascontiguousarray(b).view("<u2")
            for i in range(0, len(v), _TARGET_CHUNK_ELEMS):
                np.add(acc, np.bincount(v[i : i + _TARGET_CHUNK_ELEMS],
                                        minlength=1 << 16),
                       out=acc, casting="unsafe")
                done += min(_TARGET_CHUNK_ELEMS, len(v) - i)
                if progress is not None:
                    progress(done / total)
        return acc.reshape(256, 256).T.copy()
    if n == 3:
        return _trigram(a, progress)
    raise ValueError(f"n must be 1, 2 or 3, got {n}")


def _trigram_keys(seg: np.ndarray) -> np.ndarray:
    """24-bit trigram keys, built in place: the natural expression allocates
    a full-size temporary per operator (measured 872 MB peak at 100 MB)."""
    keys = seg[:-2].astype(np.uint32)
    keys <<= 8
    keys |= seg[1:-1]
    keys <<= 8
    keys |= seg[2:]
    return keys


def _trigram(a: np.ndarray, progress=None):
    """Sparse trigram counts, by whichever strategy suits the data.

    A dense bincount over the 2^24 key space allocates a fresh 134 MB int64
    table per call and thrashes cache, so neither path uses one. The two
    regimes invert (measured, 100 MB inputs):

                        binary-like            uniform random
      sparse merge      2.8 s /  81 MB         20.4 s / 3005 MB
      blocked dense     3.9 s / 165 MB          8.0 s /  210 MB

    Real binaries are sparse (~143k distinct trigrams, matching the plan's
    10k-500k estimate) and packed/encrypted regions are dense — and this
    tool is pointed at both, so committing to either alone is wrong.

    The strategy starts sparse and switches to the blocked-dense table
    mid-stream the moment any single chunk's distinct-key count exceeds
    _TRIGRAM_SPARSE_MAX_UNIQUE or the accumulated total exceeds
    _TRIGRAM_SPARSE_TOTAL_MAX. A first-chunk-only probe is not enough: a
    file that opens binary-like and turns random keeps the sparse strategy
    while the random section explodes the unique set (P12, measured).
    """
    step = _TRIGRAM_CHUNK_ELEMS
    starts = list(range(0, max(len(a) - 2, 0), step))
    if not starts:
        return (np.zeros((0, 3), dtype=np.uint8), np.zeros(0, dtype=np.uint32))

    uniqs: list | None = []
    cnts: list | None = []
    total = 0
    acc = None
    for ci, i in enumerate(starts):
        keys = _trigram_keys(a[i : i + step + 2])
        keys.sort()
        if acc is None:
            h = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
            if (h.size > _TRIGRAM_SPARSE_MAX_UNIQUE
                    or total + h.size > _TRIGRAM_SPARSE_TOTAL_MAX):
                acc = _sparse_to_dense(uniqs, cnts)
                uniqs = cnts = None
                _count_blocked(acc, keys)
            else:
                uniqs.append(keys[h])
                cnts.append(np.diff(np.r_[h, len(keys)]).astype(np.uint32))
                total += h.size
        else:
            _count_blocked(acc, keys)
        del keys
        if progress is not None:
            progress((ci + 1) / len(starts))

    if acc is None:
        keys_u, counts = _merge_sparse(uniqs, cnts)
    else:
        nz = np.flatnonzero(acc)
        keys_u, counts = nz.astype(np.uint32), acc[nz]

    coords = np.empty((len(keys_u), 3), dtype=np.uint8)
    coords[:, 0] = keys_u >> 16
    coords[:, 1] = (keys_u >> 8) & 0xFF
    coords[:, 2] = keys_u & 0xFF
    return coords, counts.astype(np.uint32)


def _sparse_to_dense(uniqs: list, cnts: list) -> np.ndarray:
    """Scatter accumulated per-chunk (unique, count) pairs into the dense
    table. Keys are unique within each chunk, so plain fancy-index add is
    exact — no np.add.at needed."""
    acc = np.zeros(1 << 24, dtype=np.uint32)
    for u, c in zip(uniqs, cnts):
        acc[u] += c
    return acc


def _count_blocked(acc: np.ndarray, keys: np.ndarray) -> None:
    """Count sorted keys into the dense table via cache-resident blocks."""
    shift = 24 - _TRIGRAM_BLOCK_BITS
    span = 1 << shift
    probes = np.arange(1 << _TRIGRAM_BLOCK_BITS, dtype=np.uint32) << shift
    bounds = list(np.searchsorted(keys, probes)) + [len(keys)]
    for b in range(1 << _TRIGRAM_BLOCK_BITS):
        s0, s1 = bounds[b], bounds[b + 1]
        if s1 <= s0:
            continue
        base = b << shift
        lo = (keys[s0:s1] - np.uint32(base)).astype(np.int64)
        np.add(acc[base : base + span], np.bincount(lo, minlength=span),
               out=acc[base : base + span], casting="unsafe")


def _merge_sparse(uniqs: list, cnts: list):
    """Merge per-chunk sorted uniques. Memory scales with the number of
    distinct trigrams (bounded by _TRIGRAM_SPARSE_TOTAL_MAX), not with the
    2^24 key space."""
    all_k = np.concatenate(uniqs)
    all_c = np.concatenate(cnts)
    uniqs.clear()
    cnts.clear()
    order = np.argsort(all_k, kind="stable")
    all_k, all_c = all_k[order], all_c[order]
    del order
    h = np.flatnonzero(np.r_[True, all_k[1:] != all_k[:-1]])
    return all_k[h], np.add.reduceat(all_c.astype(np.uint64), h)


# ------------------------------------------------ per-window stat matrix

_PRINTABLE_MASK = np.isin(_CLASS_LUT, (CLASS_PRINTABLE, CLASS_WHITESPACE))


def window_stats(buf, window: int, stride: int | None = None,
                 which: tuple[str, ...] = ("entropy",),
                 progress=None) -> dict[str, np.ndarray]:
    """One streaming pass computing any of: entropy, printable_ratio,
    null_ratio, chi2, distinct — all derivable from per-window byte counts.

    `progress`, when given, is called with a fraction in (0, 1] as window
    chunks complete."""
    stride = window if stride is None else stride
    a = np.frombuffer(buf, dtype=np.uint8)
    n_win = (len(a) - window) // stride + 1 if len(a) >= window else 0
    out = {k: np.empty(n_win, dtype=np.float32) for k in which}
    lut = _entropy_lut(window)
    log2w, inv_w = np.log2(window), 1.0 / window
    e = window / 256.0
    for r0, counts in _iter_window_counts(a, window, stride):
        r1 = r0 + len(counts)
        if "entropy" in out:
            out["entropy"][r0:r1] = np.clip(
                log2w - lut[counts].sum(axis=1) * inv_w, 0.0, 8.0)
        if "printable_ratio" in out:
            out["printable_ratio"][r0:r1] = (
                counts[:, _PRINTABLE_MASK].sum(axis=1) * inv_w)
        if "null_ratio" in out:
            out["null_ratio"][r0:r1] = counts[:, 0] * inv_w
        if "chi2" in out:
            d = counts.astype(np.float64) - e
            out["chi2"][r0:r1] = (d * d).sum(axis=1) / e
        if "distinct" in out:
            out["distinct"][r0:r1] = (counts > 0).sum(axis=1)
        if progress is not None:
            progress(r1 / n_win)
    offsets = np.arange(n_win, dtype=np.int64) * stride
    out["offsets"] = offsets
    return out
