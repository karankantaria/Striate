"""Phase 6 cache: content-addressed artifacts, atomic writes, invalidation."""

import json

import numpy as np
import pytest

from binviz.cache import (BinaryCache, analyze, is_ready, pack_trigram,
                          params_fingerprint)
from binviz.loader import MappedFile, sha256_file
from conftest import require_sample


@pytest.fixture(scope="module")
def hello_cache(tmp_path_factory, manifest):
    """hello_O2 analysed once into a module-scoped cache root."""
    path = require_sample("hello_O2", manifest)
    root = tmp_path_factory.mktemp("cache")
    cache = BinaryCache(sha256_file(path), root)
    meta = analyze(cache, path)
    return path, cache, meta


def test_analyze_populates_every_artifact(hello_cache):
    path, cache, meta = hello_cache
    assert meta["state"] == "complete", meta["error"]
    assert all(v == "ready" for v in meta["artifacts"].values())
    for rel in ("model.json", "signals/entropy_4096.f32",
                "signals/entropy_4096.i64", "hist/1_u8.bin", "hist/2_u8.bin",
                "trigram.sparse", "functions.json", "meta.json"):
        assert cache.exists(rel), rel
    assert cache.path("hist/1_u8.bin").stat().st_size == 256 * 4
    assert cache.path("hist/2_u8.bin").stat().st_size == 65536 * 4
    assert cache.path("trigram.sparse").stat().st_size % 16 == 0
    # every recovered function has its CFG document on disk
    fns = cache.read_json("functions.json")["functions"]
    assert fns
    for f in fns:
        assert cache.exists(f"cfg/{f['va']:x}.json")


def test_no_halfwritten_artifacts_left(hello_cache):
    _, cache, _ = hello_cache
    assert not list(cache.dir.rglob("*.tmp"))


def test_cached_signal_identical_to_engine(hello_cache):
    from binviz.signals import compute_signals

    path, cache, _ = hello_cache
    with MappedFile.open(path) as mf:
        direct = compute_signals(mf.view, ["entropy_4096"])["entropy_4096"]
    values = np.frombuffer(cache.read_bytes("signals/entropy_4096.f32"),
                           dtype="<f4")
    offsets = np.frombuffer(cache.read_bytes("signals/entropy_4096.i64"),
                            dtype="<i8")
    np.testing.assert_array_equal(values, direct.values)
    np.testing.assert_array_equal(offsets, direct.offsets)


def test_model_json_matches_parse(hello_cache):
    from binviz.parse import parse

    path, cache, _ = hello_cache
    cached = cache.read_json("model.json")
    direct = parse(path).to_json()
    assert cached == direct


def test_trigram_is_count_descending(hello_cache):
    _, cache, _ = hello_cache
    pts = np.frombuffer(cache.read_bytes("trigram.sparse"),
                        dtype="<i4").reshape(-1, 4)
    assert len(pts) > 0
    assert (np.diff(pts[:, 3]) <= 0).all()


def test_is_ready_tracks_parameter_fingerprint(hello_cache):
    _, cache, _ = hello_cache
    assert is_ready(cache)
    meta = cache.read_json("meta.json")
    assert meta["params_fingerprint"] == params_fingerprint()
    cache.update_meta(params_fingerprint="0000stale")
    assert not is_ready(cache)
    cache.update_meta(params_fingerprint=params_fingerprint())
    assert is_ready(cache)


def test_atomic_write_creates_parents_and_replaces(tmp_path):
    cache = BinaryCache("ab" * 32, tmp_path)
    cache.write_bytes("deep/nested/x.bin", b"one")
    cache.write_bytes("deep/nested/x.bin", b"two")
    assert cache.read_bytes("deep/nested/x.bin") == b"two"
    assert not list(cache.dir.rglob("*.tmp"))


def test_pack_trigram_deterministic_tie_break():
    coords = np.array([[3, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=np.uint8)
    counts = np.array([5, 5, 9], dtype=np.uint32)
    pts = np.frombuffer(pack_trigram(coords, counts),
                        dtype="<i4").reshape(-1, 4)
    # count-descending; equal counts ordered by coordinate
    assert pts.tolist() == [[2, 0, 0, 9], [1, 0, 0, 5], [3, 0, 0, 5]]


def test_analyze_raw_fallback_never_hard_fails(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 64)
    cache = BinaryCache(sha256_file(blob), tmp_path / "root")
    meta = analyze(cache, str(blob))
    assert meta["state"] == "complete", meta["error"]
    model = cache.read_json("model.json")
    assert model["format"] == "raw"
    # unknown arch: recovery yields no functions but still writes the index
    fns = cache.read_json("functions.json")
    assert fns["functions"] == []


def test_meta_json_is_valid_json_after_many_updates(tmp_path):
    cache = BinaryCache("cd" * 32, tmp_path)
    for i in range(50):
        cache.mark_artifact("model", f"state{i}")
    doc = json.loads(cache.read_bytes("meta.json"))
    assert doc["artifacts"]["model"] == "state49"
