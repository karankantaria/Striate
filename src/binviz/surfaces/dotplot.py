"""Self-similarity dot plot — the reference's DotPlot, plus an exact mode.

Cell (i, j) is lit when the k-byte window at offset i matches the window
at offset j. Exhaustive comparison is impossible: a 1 MiB range is 10^12
pairs (§5.5). Two honest answers, and `meta.mode` always says which ran:

  exact    ranges under ~256 KiB. Hash every k-mer, group by hash, and
           compare only within groups: O(n + matches). Instant and noise-free
           for the common "inspect this one section" case.
  sampled  the reference's approach, preserved: draw random (i, j) pairs and
           refine over repeated calls. `meta.progress` reports how much of
           the space has actually been looked at, because a sparse sampled
           plot reads as "no self-similarity" when it means "we barely looked".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .base import Raster, SurfaceRequest, int_param, register

EXACT_MAX_BYTES = 256 * 1024
DEFAULT_WINDOW = 8
DEFAULT_MAX_SAMPLES = 1_000_000
# a single k-mer repeated n times implies n^2 pairs; bound the work and say so
EXACT_PAIR_BUDGET = 40_000_000
# P12 scale bounds. Both the range-2 index and the range-1 sampling
# permutation are O(n) in uint64s: a whole-file axis on a 2 GiB file is a
# ~17 GB allocation for each (measured MemoryError on 16 GB RAM). Above
# INDEX_MAX_POSITIONS range 2 is streamed in tiles of this size per pass
# (transient ~10 tile-bytes, nothing persistent); above ROW_SAMPLE_MAX
# range 1 is represented by a fixed random row subset.
INDEX_MAX_POSITIONS = 1 << 24    # 16.7M positions, ~270 MB index when built
ROW_SAMPLE_MAX = 1 << 22         # 4.2M sampled rows, 33 MB of hashes


def _kmer_hashes(a: np.ndarray, k: int) -> np.ndarray:
    """64-bit rolling polynomial hash of every k-byte window.

    Exact for k <= 8 (the value *is* the window, packed big-endian); for
    larger k, collisions are possible but astronomically unlikely, which
    is why the mode is reported rather than silently assumed exact.
    """
    n = a.size - k + 1
    if n <= 0:
        return np.zeros(0, dtype=np.uint64)
    if k <= 8:
        h = np.zeros(n, dtype=np.uint64)
        for i in range(k):
            h = (h << np.uint64(8)) | a[i:i + n].astype(np.uint64)
        return h
    base = np.uint64(1099511628211)  # FNV prime
    h = np.zeros(n, dtype=np.uint64)
    for i in range(k):
        h = h * base + a[i:i + n].astype(np.uint64)
    return h


def _cells(positions: np.ndarray, n_positions: int, extent: int) -> np.ndarray:
    if n_positions <= 0:
        return positions.astype(np.int64)
    return (positions.astype(np.int64) * extent) // n_positions


def _distinct_group_cells(hashes: np.ndarray, n: int,
                          extent: int) -> tuple[np.ndarray, np.ndarray]:
    """Distinct (hash, cell) pairs, sorted by hash then cell."""
    cells = _cells(np.arange(hashes.size, dtype=np.int64), n, extent)
    order = np.lexsort((cells, hashes))
    hs, cs = hashes[order], cells[order]
    if hs.size:
        keep = np.r_[True, (hs[1:] != hs[:-1]) | (cs[1:] != cs[:-1])]
        hs, cs = hs[keep], cs[keep]
    return hs, cs


def _collapse_cells(hashes: np.ndarray, cells: np.ndarray):
    """Distinct (hash, cell) pairs with multiplicities, sorted by hash.

    The join that consumes this emits one row per distinct *combination*,
    weighted by the product of multiplicities — so a degenerate k-mer (a
    zero run) costs at most cells_x x cells_y emissions instead of one per
    matching pair. On a 2 GiB mixed file the naive pair expansion measured
    819 billion pairs in one tile (a 5.96 TiB np.repeat) — the collapse is
    what makes huge self-similar ranges tractable at identical output.
    """
    order = np.lexsort((cells, hashes))
    hs, cs = hashes[order], cells[order]
    if hs.size == 0:
        return hs, cs.astype(np.int32), np.zeros(0, dtype=np.int64)
    new = np.r_[True, (hs[1:] != hs[:-1]) | (cs[1:] != cs[:-1])]
    heads = np.flatnonzero(new)
    mult = np.diff(np.r_[heads, hs.size]).astype(np.int64)
    return hs[heads], cs[heads].astype(np.int32), mult


def _sample_rows(rng: np.random.Generator, n: int, size: int) -> np.ndarray:
    """A sorted random subset of range(n) without replacement, deterministic
    for a given rng state. Never materialises a permutation of n (P12: a
    2 GiB axis makes that a 17 GB array)."""
    if size >= n:
        return np.arange(n, dtype=np.int64)
    rows = np.unique(rng.integers(0, n, int(size * 1.05) + 16,
                                  dtype=np.int64))[:size]
    while rows.size < size:   # collision top-up; astronomically rare
        extra = rng.integers(0, n, size, dtype=np.int64)
        rows = np.unique(np.concatenate([rows, extra]))[:size]
    return rows


@dataclass
class DotPlotAccumulator:
    """Progressive sampling state — the reference's advance_mat + pts_i_.

    Sampling is over *positions*, not over (i, j) pairs, and each sampled
    position is then resolved exactly against a k-mer index. This is a
    deliberate departure from the reference, forced by arithmetic: in
    `repeats.bin` the matching pairs are ~4e-6 of the pair space, so a
    million uniform pair samples return ~4 hits and the bands are invisible.
    Measured, that is exactly what happens. Sampling positions instead
    surfaces every band within the first few thousand samples, keeps the
    per-call work bounded, and makes `progress` mean something a user can
    act on: the fraction of rows that are now fully resolved.

    Two P12 scale bounds change shape, not meaning:
    - n1 > ROW_SAMPLE_MAX: a fixed random row subset stands in for range 1
      (`rows_sampled` in the meta says so).
    - n2 > INDEX_MAX_POSITIONS: no persistent index; each advance() streams
      one tile of range 2 past the row hashes, and progress counts tiles.

    The service (P6) keys these by (range, params, seed) and advances them
    across requests; each call raises `progress` and the resolved count.
    """

    key: tuple
    matrix: np.ndarray          # int64 (h, w) pair counts
    n1: int = 0
    n2: int = 0
    resolved: int = 0           # sampled rows fully resolved so far
    hits: int = 0               # matching (position, position) pairs found
    cursor: int = 0             # advance() calls; in tiled mode, tiles done
    _rng: np.random.Generator = field(default=None, repr=False)
    _order: np.ndarray = field(default=None, repr=False)
    _index: tuple = field(default=None, repr=False)   # collapsed (h, c, m)
    _rows: np.ndarray = field(default=None, repr=False)
    _row_groups: tuple = field(default=None, repr=False)  # collapsed rows

    @property
    def tiled(self) -> bool:
        return self.n2 > INDEX_MAX_POSITIONS

    @property
    def n_tiles(self) -> int:
        return max(1, -(-self.n2 // INDEX_MAX_POSITIONS))

    @property
    def n_rows(self) -> int:
        return min(self.n1, ROW_SAMPLE_MAX)

    def build_index(self, a: np.ndarray, k: int,
                    off1: int, off2: int) -> None:
        """One-time collapsed k-mer index over range 2, plus a sampling
        order for range 1. The index holds distinct (hash, cell) groups
        with multiplicities — bounded by n2 and usually far smaller."""
        h_mat, _w = self.matrix.shape
        h2 = _kmer_hashes(a[off2:off2 + self.n2 + k - 1], k)
        cells2 = _cells(np.arange(self.n2, dtype=np.int64), self.n2, h_mat)
        self._index = _collapse_cells(h2, cells2)
        self._rows = _sample_rows(self._rng, self.n1, ROW_SAMPLE_MAX)
        self._order = self._rng.permutation(self._rows.size)

    def advance(self, a: np.ndarray, k: int, off1: int, off2: int,
                n_samples: int) -> None:
        if self.tiled:
            self._advance_tile(a, k, off1, off2)
            return
        if self._index is None:
            self.build_index(a, k, off1, off2)
        _h, w = self.matrix.shape
        take = self._order[self.resolved:self.resolved + n_samples]
        if take.size == 0:
            return
        pos = self._rows[take]
        h1 = _kmer_hashes_at(a, off1, pos, k)
        c1 = _cells(pos, self.n1, w)
        self._join(_collapse_cells(h1, c1), self._index, transpose=False)
        self.resolved += int(take.size)
        self.cursor += 1

    def _advance_tile(self, a: np.ndarray, k: int,
                      off1: int, off2: int) -> None:
        """One tile of range 2 joined against the (fixed) collapsed row
        groups. The tile's hashes are the only large transient and die on
        return."""
        if self._row_groups is None:
            _h, w = self.matrix.shape
            self._rows = _sample_rows(self._rng, self.n1, ROW_SAMPLE_MAX)
            h1 = _kmer_hashes_at(a, off1, self._rows, k)
            c1 = _cells(self._rows, self.n1, w)
            self._row_groups = _collapse_cells(h1, c1)
        t = self.cursor
        if t >= self.n_tiles:
            return
        h_mat, _w = self.matrix.shape
        base = t * INDEX_MAX_POSITIONS
        n_t = min(INDEX_MAX_POSITIONS, self.n2 - base)
        h2 = _kmer_hashes(a[off2 + base:off2 + base + n_t + k - 1], k)
        cells2 = _cells(base + np.arange(n_t, dtype=np.int64),
                        self.n2, h_mat)
        self._join(_collapse_cells(h2, cells2), self._row_groups,
                   transpose=True)
        self.cursor += 1
        self.resolved = (self.n_rows * min(self.cursor, self.n_tiles)
                         // self.n_tiles)

    def _join(self, probe: tuple, index: tuple, *, transpose: bool) -> None:
        """Weighted join of two collapsed (hash, cell, mult) group sets.

        Emits one matrix update per distinct (cell, cell) combination,
        weighted mult_probe x mult_index — the exact pair count, computed
        without materialising pairs (see _collapse_cells). `transpose`
        says whether probe cells are the y axis (tiled) or x axis."""
        ph, pc, pm = probe
        ih, ic, im = index
        if ph.size == 0 or ih.size == 0:
            return
        lo = np.searchsorted(ih, ph, "left")
        hi = np.searchsorted(ih, ph, "right")
        counts = hi - lo
        found = counts > 0
        if not found.any():
            return
        reps = counts[found]
        js = _ranges(lo[found], reps)
        weights = np.repeat(pm[found], reps) * im[js]
        probe_cells = np.repeat(pc[found], reps)
        index_cells = ic[js]
        ys, xs = ((probe_cells, index_cells) if transpose
                  else (index_cells, probe_cells))
        np.add.at(self.matrix, (ys, xs), weights)
        self.hits += int(weights.sum())

    @property
    def progress(self) -> float:
        if self.n1 <= 0:
            return 1.0
        if self.tiled:
            return min(1.0, self.cursor / self.n_tiles)
        return min(1.0, self.resolved / max(1, self.n_rows))


def _kmer_hashes_at(a: np.ndarray, base: int, pos: np.ndarray,
                    k: int) -> np.ndarray:
    """Hashes of the k-mers starting at `base + pos`, matching _kmer_hashes."""
    idx = base + pos.astype(np.int64)
    if k <= 8:
        h = np.zeros(pos.size, dtype=np.uint64)
        for i in range(k):
            h = (h << np.uint64(8)) | a[idx + i].astype(np.uint64)
        return h
    base_p = np.uint64(1099511628211)
    h = np.zeros(pos.size, dtype=np.uint64)
    for i in range(k):
        h = h * base_p + a[idx + i].astype(np.uint64)
    return h


def _ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Concatenation of arange(s, s+c) for each (s, c) — vectorised."""
    total = int(counts.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    offsets = np.repeat(starts - np.r_[0, np.cumsum(counts)[:-1]], counts)
    return np.arange(total, dtype=np.int64) + offsets


class DotPlotSurface:
    name = "dotplot"

    def render(self, buf, req: SurfaceRequest) -> Raster:
        a = np.frombuffer(buf, dtype=np.uint8)
        p = req.params
        k = int(p.get("window", DEFAULT_WINDOW))
        off1 = int(p.get("off1", req.start))
        end1 = int(p.get("end1", req.end))
        off2 = int(p.get("off2", off1))
        end2 = int(p.get("end2", end1))
        w, h = req.width, req.height
        forced = p.get("mode")

        len1, len2 = max(0, end1 - off1), max(0, end2 - off2)
        n1, n2 = max(0, len1 - k + 1), max(0, len2 - k + 1)
        meta: dict = {"window": k, "off1": off1, "end1": end1,
                      "off2": off2, "end2": end2, "warnings": []}
        if n1 == 0 or n2 == 0:
            meta["mode"] = "empty"
            meta["warnings"].append(f"range shorter than the {k}-byte window")
            return Raster(np.zeros((h, w), dtype=np.uint8), "scalar", meta)

        use_exact = (forced == "exact" or
                     (forced is None and max(len1, len2) <= EXACT_MAX_BYTES))
        if use_exact:
            matrix, exact_meta = self._exact(a, k, off1, n1, off2, n2, w, h)
            if exact_meta.get("fell_back"):
                use_exact = False
                meta["warnings"].extend(exact_meta["warnings"])
            else:
                meta.update(exact_meta)
                meta["mode"] = "exact"
                meta["progress"] = 1.0
                return self._finish(matrix, meta, w, h)

        acc = p.get("accumulator") or self.accumulator(req, w, h, n1, n2)
        max_samples = int(p.get("max_samples", DEFAULT_MAX_SAMPLES))
        acc.advance(a, k, off1, off2, max_samples)
        meta.update({
            "mode": "sampled", "max_samples": max_samples,
            "resolved": acc.resolved, "positions": n1, "hits": acc.hits,
            "progress": acc.progress, "seed": int(p.get("seed", 0)),
            "cursor": acc.cursor,
        })
        if acc.tiled:
            meta["tiled"] = True
            meta["tiles"] = acc.n_tiles
            meta["tiles_done"] = min(acc.cursor, acc.n_tiles)
        if acc.n_rows < n1:
            meta["rows_sampled"] = acc.n_rows
            meta["warnings"].append(
                f"rows sampled: {acc.n_rows:,} random rows stand in for "
                f"{n1:,} positions on axis 1")
        if acc.progress < 1.0:
            meta["warnings"].append(
                f"sampled: {acc.resolved:,} of {acc.n_rows:,} sampled rows "
                f"resolved ({100 * acc.progress:.1f}%); absence of dots in "
                "unresolved columns is not evidence of absence of "
                "self-similarity")
        return self._finish(acc.matrix, meta, w, h)

    @staticmethod
    def accumulator(req: SurfaceRequest, w: int, h: int,
                    n1: int, n2: int) -> DotPlotAccumulator:
        seed = int_param(req.params, "seed", 0)
        # int64: weighted joins accumulate true pair counts, and a
        # degenerate k-mer over a huge range overflows uint32 per cell
        return DotPlotAccumulator(
            key=req.cache_key(), matrix=np.zeros((h, w), dtype=np.int64),
            n1=n1, n2=n2, _rng=np.random.default_rng(seed))

    def _exact(self, a, k, off1, n1, off2, n2, w, h):
        meta: dict = {"warnings": []}
        h1 = _kmer_hashes(a[off1:off1 + n1 + k - 1], k)
        same = (off1 == off2 and n1 == n2)
        h2 = h1 if same else _kmer_hashes(a[off2:off2 + n2 + k - 1], k)

        # Collapse (hash, cell) to distinct pairs *before* joining. A run of
        # identical k-mers maps to at most w cells, so the join is bounded by
        # the raster rather than by n^2 -- and the whole thing stays
        # vectorised. Looping over hash groups in Python instead costs ~15 s
        # on a 448 KiB range, because most groups are a single k-mer.
        g1, c1 = _distinct_group_cells(h1, n1, w)
        if same:
            g2, c2 = g1, c1
        else:
            g2, c2 = _distinct_group_cells(h2, n2, h)

        # inner join on hash: every distinct (cell_x, cell_y) sharing a k-mer
        lo2 = np.searchsorted(g2, g1, "left")
        hi2 = np.searchsorted(g2, g1, "right")
        cnt2 = hi2 - lo2
        total = int(cnt2.sum())
        if total > EXACT_PAIR_BUDGET:
            meta["fell_back"] = True
            meta["warnings"].append(
                f"exact mode needs {total:,} cell emissions "
                f"(budget {EXACT_PAIR_BUDGET:,}); using sampled mode")
            return None, meta

        matrix = np.zeros((h, w), dtype=np.uint32)
        keep = cnt2 > 0
        if keep.any():
            xs = np.repeat(c1[keep], cnt2[keep])
            ys = c2[_ranges(lo2[keep], cnt2[keep])]
            np.add.at(matrix, (ys, xs), 1)
        meta.update({"distinct_kmers": int(len(g1)),
                     "cell_pairs": total,
                     "exact_for_k": k <= 8})
        if k > 8:
            meta["warnings"].append(
                f"k={k} > 8 uses a 64-bit hash; collisions are possible "
                "though vanishingly unlikely")
        return matrix, meta

    @staticmethod
    def _finish(matrix, meta, w, h) -> Raster:
        m = matrix.astype(np.float64)
        peak = m.max()
        meta["max_cell"] = int(peak)
        meta["lit_cells"] = int((m > 0).sum())
        meta["lit_fraction"] = float((m > 0).mean())
        px = (np.log1p(m) * (255.0 / np.log1p(peak))).astype(np.uint8) \
            if peak > 0 else np.zeros((h, w), dtype=np.uint8)
        meta["display"] = "log1p"
        return Raster(pixels=px, kind="scalar", meta=meta)


register(DotPlotSurface())
