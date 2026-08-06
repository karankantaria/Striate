"""Phase 12 scale hardening — regression tests at test-friendly scale.

Every fix here was entered with a measured number on a 2 GiB mixed file
(16 GB machine), recorded in HANDOVER.md:

- histogram(): whole-array bincount casts u8 -> int64 internally, 8x the
  input (measured 16.75 GiB commit). Now chunked; tested via tracemalloc.
- trigram: sparse-vs-dense strategy probed on the first chunk only, so a
  file that opens binary-like and turns random exploded the sparse merge
  (measured 85 s / +10.3 GiB commit). Now switches mid-stream; tested by
  shrinking the chunk constants and comparing against a brute-force count.
- dotplot: the range-2 index and the range-1 permutation are O(n) uint64
  (~17 GB each on a whole-file 2 GiB axis -> MemoryError). Now tiled /
  row-sampled past thresholds; tested by shrinking the thresholds and
  asserting tiled-at-100% equals the untiled reference exactly.
- trigram.sparse artifact capped (a 2 GiB mixed file wrote 256 MiB);
  sidecar meta records the truth and /hist3 reports capped.
- per-step progress in meta.json -> /status (a 2 GiB analysis is minutes
  of silent "running" otherwise).
- uploads stream to disk instead of buffering the whole body in RAM.
"""

import json
import time
import tracemalloc

import numpy as np
import pytest

import binviz.cache as cache_mod
import binviz.stats as stats
import binviz.surfaces.dotplot as dot
from binviz.cache import BinaryCache, analyze
from binviz.loader import sha256_file
from binviz.stats import histogram, ngram, window_stats

from conftest import authed_client, make_app, require_sample


# ------------------------------------------------------------- histogram

def test_histogram_matches_bincount():
    rng = np.random.default_rng(7)
    a = rng.integers(0, 256, 1_000_003, dtype=np.uint8)
    assert np.array_equal(histogram(a), np.bincount(a, minlength=256))


def test_histogram_chunked_memory():
    """32 MiB input: the old whole-array bincount casts to int64 (256 MiB
    transient); the chunked version stays under 2x one chunk."""
    a = np.zeros(32 * 1024 * 1024, dtype=np.uint8)
    tracemalloc.start()
    histogram(a)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 2 * stats._TARGET_CHUNK_ELEMS * 8


# ------------------------------------------------- trigram strategy switch

def _brute_trigrams(a: np.ndarray):
    keys = ((a[:-2].astype(np.int64) << 16)
            | (a[1:-1].astype(np.int64) << 8) | a[2:].astype(np.int64))
    u, c = np.unique(keys, return_counts=True)
    return u, c


def _as_key_dict(coords: np.ndarray, counts: np.ndarray) -> dict:
    keys = ((coords[:, 0].astype(np.int64) << 16)
            | (coords[:, 1].astype(np.int64) << 8)
            | coords[:, 2].astype(np.int64))
    return dict(zip(keys.tolist(), counts.tolist()))


@pytest.fixture
def tiny_trigram_chunks(monkeypatch):
    """Shrink the chunk and budgets so the mixed-content switch happens at
    kilobyte scale."""
    monkeypatch.setattr(stats, "_TRIGRAM_CHUNK_ELEMS", 4096)
    monkeypatch.setattr(stats, "_TRIGRAM_SPARSE_MAX_UNIQUE", 700)
    monkeypatch.setattr(stats, "_TRIGRAM_SPARSE_TOTAL_MAX", 1500)


def test_trigram_switches_mid_stream(tiny_trigram_chunks, monkeypatch):
    """Binary-like head, random tail: the head keeps the sparse strategy,
    the tail must flip it to dense — and counts stay exact either way."""
    switched = []
    orig = stats._sparse_to_dense

    def spy(uniqs, cnts):
        switched.append(True)
        return orig(uniqs, cnts)

    monkeypatch.setattr(stats, "_sparse_to_dense", spy)
    rng = np.random.default_rng(3)
    head = np.tile(np.arange(64, dtype=np.uint8), 400)       # low-unique
    tail = rng.integers(0, 256, 40_000, dtype=np.uint8)      # high-unique
    a = np.concatenate([head, tail])
    coords, counts = ngram(a, 3)
    assert switched, "dense switch never engaged"
    u, c = _brute_trigrams(a)
    got = _as_key_dict(coords, counts)
    assert got == dict(zip(u.tolist(), c.tolist()))
    assert int(counts.sum()) == len(a) - 2


def test_trigram_sparse_path_still_exact(tiny_trigram_chunks):
    a = np.tile(np.arange(48, dtype=np.uint8), 500)   # stays sparse
    coords, counts = ngram(a, 3)
    u, c = _brute_trigrams(a)
    assert _as_key_dict(coords, counts) == dict(zip(u.tolist(), c.tolist()))


def test_trigram_random_first_chunk_goes_dense(tiny_trigram_chunks):
    rng = np.random.default_rng(4)
    a = rng.integers(0, 256, 30_000, dtype=np.uint8)
    coords, counts = ngram(a, 3)
    u, c = _brute_trigrams(a)
    assert _as_key_dict(coords, counts) == dict(zip(u.tolist(), c.tolist()))


# ------------------------------------------------------ progress callbacks

def test_window_stats_progress_monotone_to_one():
    buf = bytes(np.random.default_rng(5).integers(0, 256, 3_000_000,
                                                  dtype=np.uint8))
    seen = []
    window_stats(buf, 256, 256, which=("entropy",), progress=seen.append)
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


def test_bigram_progress_reaches_one_on_even_and_odd_lengths():
    for n in (100_000, 100_001):
        a = np.zeros(n, dtype=np.uint8)
        seen = []
        ngram(a, 2, seen.append)
        assert seen[-1] == pytest.approx(1.0), f"len {n}"


def test_trigram_progress_reaches_one():
    a = np.zeros(50_000, dtype=np.uint8)
    seen = []
    ngram(a, 3, seen.append)
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


def test_analyze_records_progress(tmp_path, manifest):
    path = require_sample("hello_O2", manifest)
    cache = BinaryCache(sha256_file(path), tmp_path)
    meta = analyze(cache, path)
    prog = meta.get("progress") or {}
    for name in ("signals", "hist", "trigram", "triage"):
        assert prog.get(name) == pytest.approx(1.0), (name, prog)


# ------------------------------------------------- trigram artifact cap

def test_trigram_artifact_cap(tmp_path, monkeypatch, manifest):
    monkeypatch.setattr(cache_mod, "TRIGRAM_STORE_MAX_POINTS", 100)
    path = require_sample("urandom.bin", manifest)
    cache = BinaryCache(sha256_file(path), tmp_path)
    analyze(cache, path)
    side = cache.read_json("trigram.meta.json")
    assert side["capped"] is True
    assert side["stored_points"] == 100
    assert side["total_points"] > 100
    pts = cache.memmap("trigram.sparse", cache_mod.TRIGRAM_DTYPE).reshape(-1, 4)
    assert len(pts) == 100
    # count-descending prefix: the cap keeps the densest points
    assert (np.diff(pts[:, 3]) <= 0).all()


def test_hist3_reports_artifact_cap(tmp_path_factory, monkeypatch, manifest):
    monkeypatch.setattr(cache_mod, "TRIGRAM_STORE_MAX_POINTS", 100)
    path = require_sample("urandom.bin", manifest)
    app = make_app(tmp_path_factory.mktemp("p12cache"))
    with authed_client(app) as client:
        r = client.post("/api/open", json={"path": path})
        sha = r.json()["id"]
        deadline = time.time() + 300
        while time.time() < deadline:
            s = client.get(f"/api/{sha}/status").json()
            if s.get("state") == "complete":
                break
            if s.get("state") == "error":
                pytest.fail(f"analysis error: {s}")
            time.sleep(0.1)
        r = client.get(f"/api/{sha}/hist3", params={"threshold": 1})
        meta = json.loads(r.headers["X-Meta"])
        assert meta["capped"] is True
        assert meta["points"] == 100
        assert meta["total_points"] > 100


# ------------------------------------------------------- dotplot tiling

@pytest.fixture
def repeats_buf():
    """Structured buffer with genuine self-similarity: three repeated
    blocks placed apart, over a varied background."""
    rng = np.random.default_rng(9)
    a = rng.integers(0, 256, 96 * 1024, dtype=np.uint8)
    block = rng.integers(0, 256, 2048, dtype=np.uint8)
    for off in (8 * 1024, 40 * 1024, 77 * 1024):
        a[off:off + block.size] = block
    return a


def _run_sampled_to_completion(a, w=64, h=64, k=8, seed=0):
    from binviz.surfaces.base import SurfaceRequest

    req = SurfaceRequest(0, len(a), w, h, "u8",
                         {"mode": "sampled", "seed": seed})
    surf = dot.DotPlotSurface()
    n1 = n2 = len(a) - k + 1
    acc = surf.accumulator(req, w, h, n1, n2)
    req.params["accumulator"] = acc
    last = None
    for _ in range(10_000):
        raster = surf.render(memoryview(a.tobytes()), req)
        last = raster
        if raster.meta["progress"] >= 1.0:
            break
    assert last is not None and last.meta["progress"] >= 1.0
    return acc, last


def test_dotplot_tiled_matches_untiled(repeats_buf, monkeypatch):
    """At 100% progress the tiled accumulator must have counted exactly the
    pairs the untiled one counts — tiling changes memory, not meaning."""
    acc_ref, ras_ref = _run_sampled_to_completion(repeats_buf)
    assert not acc_ref.tiled

    monkeypatch.setattr(dot, "INDEX_MAX_POSITIONS", 8192)
    acc_t, ras_t = _run_sampled_to_completion(repeats_buf)
    assert acc_t.tiled
    assert ras_t.meta["tiled"] is True
    assert ras_t.meta["tiles_done"] == ras_t.meta["tiles"] == acc_t.n_tiles

    assert acc_t.hits == acc_ref.hits
    assert np.array_equal(acc_t.matrix, acc_ref.matrix)


def test_dotplot_tiled_progress_is_tilewise(repeats_buf, monkeypatch):
    from binviz.surfaces.base import SurfaceRequest

    monkeypatch.setattr(dot, "INDEX_MAX_POSITIONS", 8192)
    a = repeats_buf
    k = 8
    n = len(a) - k + 1
    req = SurfaceRequest(0, len(a), 64, 64, "u8",
                         {"mode": "sampled", "seed": 1})
    surf = dot.DotPlotSurface()
    acc = surf.accumulator(req, 64, 64, n, n)
    req.params["accumulator"] = acc
    buf = memoryview(a.tobytes())
    fracs = []
    for _ in range(acc.n_tiles):
        raster = surf.render(buf, req)
        fracs.append(raster.meta["progress"])
    assert fracs == sorted(fracs)
    assert fracs[-1] == pytest.approx(1.0)
    assert len(set(fracs)) == acc.n_tiles   # strictly advancing per pass


def test_dotplot_row_sampling_bounds_axis1(repeats_buf, monkeypatch):
    monkeypatch.setattr(dot, "ROW_SAMPLE_MAX", 4096)
    acc, raster = _run_sampled_to_completion(repeats_buf)
    assert raster.meta["rows_sampled"] == 4096
    assert acc.resolved == 4096
    assert raster.meta["progress"] == pytest.approx(1.0)
    # sampled rows still find the repeated blocks
    assert acc.hits > 0


def test_sample_rows_deterministic_and_unique():
    r1 = dot._sample_rows(np.random.default_rng(42), 10_000_000, 5000)
    r2 = dot._sample_rows(np.random.default_rng(42), 10_000_000, 5000)
    assert np.array_equal(r1, r2)
    assert len(np.unique(r1)) == 5000
    assert r1.min() >= 0 and r1.max() < 10_000_000


# ------------------------------------------------------ upload streaming

def test_upload_streams_to_disk(tmp_path_factory):
    payload = np.random.default_rng(11).integers(
        0, 256, 4 * 1024 * 1024, dtype=np.uint8).tobytes()
    import hashlib

    app = make_app(tmp_path_factory.mktemp("p12up"))
    with authed_client(app) as client:
        r = client.post("/api/open", content=payload,
                        headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        assert r.json()["id"] == hashlib.sha256(payload).hexdigest()
        r = client.post("/api/open", content=b"",
                        headers={"content-type": "application/octet-stream"})
        assert r.status_code == 400
    # no orphaned temp files left behind
    root = app.state.cache_root
    assert not list(root.glob("*.upload"))
