"""Phase 2 acceptance: entropy profiles, n-grams, reducers."""

import json
import os

import numpy as np
import pytest

from binviz.stats import (EntropyProfile, chi2_uniform, entropy_profile,
                          histogram, ngram, reduce_minmeanmax)

from conftest import require_sample

CAL_PATH = os.path.join(os.path.dirname(__file__), "..", "corpus",
                        "calibration.json")


def load_cal():
    with open(CAL_PATH, encoding="utf-8") as f:
        return json.load(f)


def read(name, manifest):
    return open(require_sample(name, manifest), "rb").read()


# ------------------------------------------------------------- entropy

def test_zeros_entropy_floor(manifest):
    data = read("zeros.bin", manifest)
    for w in (256, 4096):
        p = entropy_profile(data, w)
        assert len(p.values) == len(data) // w
        assert (p.values == 0.0).all()


def test_pattern_entropy_exact(manifest):
    # 16 distinct bytes, uniform within every window => exactly 4.0 bits/byte
    data = read("pattern.bin", manifest)
    for w in (256, 4096):
        p = entropy_profile(data, w)
        assert np.allclose(p.values, 4.0, atol=1e-6)


def test_urandom_entropy_bands(manifest):
    """Plug-in bias check. NOTE: the plan predicted w=4096 mean in [7.98, 8.0],
    but the Miller-Madow bias term (K-1)/(2N ln2) = 0.045 at w=4096 puts the
    true expectation at ~7.955 — measured calibration agrees. The test pins
    the theoretically-correct band and stability against calibration.json."""
    data = read("urandom.bin", manifest)
    p4096 = entropy_profile(data, 4096)
    assert 7.93 <= float(p4096.values.mean()) <= 8.0
    p256 = entropy_profile(data, 256)
    assert 7.0 <= float(p256.values.mean()) <= 7.3   # plan's band, confirmed

    cal = load_cal()["raw"]["urandom"]
    assert abs(float(p4096.values.mean()) - cal["w4096"]["h_mean"]) < 0.02
    assert abs(float(p256.values.mean()) - cal["w256"]["h_mean"]) < 0.05


def test_upx_payload_entropy(manifest):
    from binviz.parse import parse

    path = require_sample("hello_upx", manifest)
    m = parse(path)
    overlay = next(r for r in m.regions if r.kind == "overlay")
    data = open(path, "rb").read()[overlay.file_off:
                                   overlay.file_off + overlay.file_size]
    p = entropy_profile(data, 4096)
    assert float(p.values.mean()) >= 7.7


def test_entropy_stride_overlapping():
    rng = np.random.default_rng(3)
    data = rng.integers(0, 256, 64 * 1024, dtype=np.uint8).tobytes()
    p = entropy_profile(data, 4096, 1024)
    assert len(p.values) == (len(data) - 4096) // 1024 + 1
    assert p.offsets[1] - p.offsets[0] == 1024
    # overlapping windows agree with non-overlapping ones where aligned
    p_plain = entropy_profile(data, 4096, 4096)
    assert np.allclose(p.values[::4], p_plain.values, atol=1e-6)


# ------------------------------------------------------------- reducer

def test_bin_exact_count_any_input():
    for n_vals in (1, 7, 256, 1999, 2000, 2001, 100_000):
        p = EntropyProfile(256, 256,
                           np.random.default_rng(0).random(n_vals).astype(np.float32),
                           np.arange(n_vals, dtype=np.int64) * 256)
        for n in (1, 2000):
            mins, means, maxs = p.bin(n)
            assert len(mins) == len(means) == len(maxs) == n
            assert (mins <= means + 1e-6).all() and (means <= maxs + 1e-6).all()


def test_spike_survives_binning():
    """§5.2 regression: one 4 KiB random block in 16 MiB of zeros must
    survive reduction to 2000 bins in the max channel."""
    rng = np.random.default_rng(11)
    data = np.zeros(16 * 1024 * 1024, dtype=np.uint8)
    at = 11 * 1024 * 1024  # 4096-aligned
    data[at : at + 4096] = rng.integers(0, 256, 4096, dtype=np.uint8)
    p = entropy_profile(data.tobytes(), 4096)
    mins, means, maxs = p.bin(2000)
    assert len(maxs) == 2000
    assert float(maxs.max()) > 7.5
    spike_bin = int(np.argmax(maxs))
    expected_bin = (at // 4096) * 2000 // len(p.values)
    assert abs(spike_bin - expected_bin) <= 1
    # and the mean channel alone would have diluted it — that's the point
    assert float(means.max()) < 7.5 * 0.6


# ------------------------------------------------------------- n-grams

def test_bigram_pattern_exact_cells(manifest):
    data = read("pattern.bin", manifest)
    counts = ngram(np.frombuffer(data, dtype=np.uint8), 2)
    nz = np.argwhere(counts > 0)
    pattern = bytes.fromhex(manifest["samples"]["pattern.bin"]["pattern_hex"])
    expected = {(pattern[i], pattern[(i + 1) % 16]) for i in range(16)}
    assert {tuple(c) for c in nz} == expected


def test_bigram_urandom_dense(manifest):
    counts = ngram(np.frombuffer(read("urandom.bin", manifest), np.uint8), 2)
    assert (counts > 0).mean() > 0.99


def test_bigram_ascii_box(manifest):
    counts = ngram(np.frombuffer(read("ascii.txt", manifest), np.uint8), 2)
    box = counts[0x09:0x7F, 0x09:0x7F].sum()
    assert box / counts.sum() >= 0.95


def test_ramp16_bigram_collapses_to_diagonal(manifest):
    """P8's dtype proof, pinned at the analysis layer: read as u16le the ramp
    is a clean diagonal; read as u8 it fills the whole plane."""
    from binviz.elements import elements, quantise

    data = read("ramp16.bin", manifest)
    bins, _ = quantise(elements(data, "u16le"), "u16le")
    counts = ngram(bins, 2)
    nz = np.argwhere(counts > 0)
    on_diag = np.abs(nz[:, 0].astype(int) - nz[:, 1].astype(int)) <= 1
    assert len(nz) == 511
    assert int(on_diag.sum()) == 510
    # the single off-diagonal cell is the one wraparound (65535 -> 0)
    assert nz[~on_diag].tolist() == [[255, 0]]
    assert int(counts[255, 0]) == 1

    bins8, _ = quantise(elements(data, "u8"), "u8")
    assert int((ngram(bins8, 2) > 0).sum()) == 65536


def test_trigram_pattern_sparse(manifest):
    coords, counts = ngram(np.frombuffer(read("pattern.bin", manifest),
                                         np.uint8), 3)
    assert len(counts) == 16
    pattern = bytes.fromhex(manifest["samples"]["pattern.bin"]["pattern_hex"])
    expected = {(pattern[i], pattern[(i + 1) % 16], pattern[(i + 2) % 16])
                for i in range(16)}
    assert {tuple(c) for c in coords} == expected


def test_trigram_urandom_coupon_collector(manifest):
    data = read("urandom.bin", manifest)
    coords, counts = ngram(np.frombuffer(data, np.uint8), 3)
    n = len(data) - 2
    m = 2 ** 24
    expected = m * (1 - (1 - 1 / m) ** n)
    assert abs(len(counts) - expected) / expected < 0.02
    assert int(counts.sum()) == n


def test_histogram_and_chi2():
    rng = np.random.default_rng(5)
    uni = rng.integers(0, 256, 1 << 20, dtype=np.uint8)
    h = histogram(uni)
    assert int(h.sum()) == 1 << 20
    assert chi2_uniform(h) < 400          # ~chi2(255): p99 ≈ 310
    skew = np.zeros(1 << 20, dtype=np.uint8)
    assert chi2_uniform(histogram(skew)) > 100_000
