"""Phase 2 acceptance: element reinterpretation."""

import numpy as np
import pytest

from binviz.elements import (byte_class, element_info, elements, quantise,
                             BYTE_CLASS_NAMES)

from conftest import require_sample


def test_u12_hand_built_vector():
    # [0x12, 0x34, 0x56] packs a=0x123, b=0x456 per the manifest convention
    vals = elements(bytes([0x12, 0x34, 0x56]), "u12")
    assert list(vals) == [0x123, 0x456]
    vals = elements(bytes([0xFF, 0xFF, 0xFF, 0x00, 0x10, 0x02]), "u12")
    assert list(vals) == [0xFFF, 0xFFF, 0x001, 0x002]


def test_u12_tail_truncation():
    info = element_info(7, "u12")   # 2 whole triplets? no — 7//3=2 triplets
    assert info == {"count": 4, "dropped_tail_bytes": 1}
    assert len(elements(b"\x00" * 7, "u12")) == 4


def test_u16_tail_truncation():
    assert element_info(9, "u16le") == {"count": 4, "dropped_tail_bytes": 1}
    assert len(elements(b"\x00" * 9, "u16le")) == 4


def test_endianness():
    buf = bytes([0x01, 0x02])
    assert elements(buf, "u16le")[0] == 0x0201
    assert elements(buf, "u16be")[0] == 0x0102


def test_ramp16_exact(manifest):
    data = open(require_sample("ramp16.bin", manifest), "rb").read()
    vals = elements(data, "u16le")
    assert np.array_equal(vals, (np.arange(131072) % 65536).astype(np.uint16))
    bins, meta = quantise(vals, "u16le")
    assert meta["method"] == "linear"
    assert meta["lo"] == 0 and meta["hi"] == 65535
    assert len(np.unique(bins)) == 256   # clean ramp fills every bin


def test_ramp16_as_u8_near_uniform(manifest):
    data = open(require_sample("ramp16.bin", manifest), "rb").read()
    bins, meta = quantise(elements(data, "u8"), "u8")
    assert meta["method"] == "identity"
    hist = np.bincount(bins, minlength=256)
    assert (hist > 0).all()
    assert hist.max() / hist.min() < 2.05  # high bytes ramp slowly: near-uniform


def test_floats_arcsine_no_nan(manifest):
    data = open(require_sample("floats.bin", manifest), "rb").read()
    vals = elements(data, "f32le")
    bins, meta = quantise(vals, "f32le")
    assert meta["method"] == "percentile"
    assert meta["n_nonfinite"] == 0
    hist = np.bincount(bins, minlength=256)
    # sine sample density is arcsine-shaped: edges pile up, middle is flat
    edges = hist[:8].sum() + hist[-8:].sum()
    middle = hist[124:132].sum()
    assert edges > 3 * middle


def test_quantise_float_outlier_resistance():
    vals = np.zeros(10_000, dtype=np.float32)
    vals[:9998] = np.sin(np.linspace(0, 20, 9998))
    vals[9998] = 1e30   # one outlier must not destroy the range
    vals[9999] = np.nan
    bins, meta = quantise(vals, "f32le")
    assert meta["n_nonfinite"] == 1
    assert meta["hi"] < 2.0          # percentile bounds ignored the outlier
    assert len(np.unique(bins[:9000])) > 100  # sine still resolved


def test_quantise_constant_input():
    bins, meta = quantise(np.full(100, 7, dtype=np.uint16), "u16le")
    assert meta["method"] == "constant"
    assert (bins == 0).all()


def test_byte_class_lut():
    got = byte_class(bytes([0x00, ord("A"), 0x09, 0x01, 0x90, 0xFF, 0x20, 0x7F]))
    assert list(got) == [0, 1, 2, 3, 4, 5, 1, 3]
    assert len(BYTE_CLASS_NAMES) == 6


def test_byte_class_corpus(manifest):
    ascii_data = open(require_sample("ascii.txt", manifest), "rb").read()
    cls = byte_class(ascii_data)
    assert ((cls == 1) | (cls == 2)).mean() >= 0.99
    zeros = open(require_sample("zeros.bin", manifest), "rb").read()
    assert (byte_class(zeros) == 0).all()
