"""Phase 2 acceptance: signal registry and calibrated classification."""

import numpy as np
import pytest

from binviz.parse import parse
from binviz.signals import (SIGNALS, classify, classify_profile,
                            compute_signals, load_calibration)

from conftest import require_sample


def read(name, manifest):
    return open(require_sample(name, manifest), "rb").read()


def region_bytes(name, region_name, manifest):
    path = require_sample(name, manifest)
    m = parse(path)
    r = next(r for r in m.regions if r.name == region_name)
    return open(path, "rb").read()[r.file_off : r.file_off + r.file_size]


def test_registry_complete():
    assert {"entropy_256", "entropy_4096", "printable_ratio", "null_ratio",
            "chi2_uniform", "distinct_bytes"} <= set(SIGNALS)


def test_signals_shapes_and_ranges(manifest):
    data = read("urandom.bin", manifest)
    sigs = compute_signals(data)
    assert len(sigs["entropy_256"].values) == len(data) // 256
    assert len(sigs["entropy_4096"].values) == len(data) // 4096
    for s in sigs.values():
        assert s.values.dtype == np.float32
        assert len(s.values) == len(s.offsets)
    assert float(sigs["distinct_bytes"].values.mean()) > 250
    assert float(sigs["null_ratio"].values.mean()) < 0.01


def test_calibration_loaded_from_corpus():
    cal = load_calibration()
    assert cal.get("source") != "fallback-defaults", \
        "corpus/calibration.json missing — run python corpus/calibrate.py"
    d = cal["derived"]
    assert d["code_h_hi"] < d["packed_h_min"] < d["random_h_min"]


def test_classify_zeros(manifest):
    assert classify(read("zeros.bin", manifest)[:4096]) == "zero"


def test_classify_ascii(manifest):
    profile = classify_profile(read("ascii.txt", manifest))
    assert profile.count("ascii") / len(profile) >= 0.95


def test_classify_urandom(manifest):
    profile = classify_profile(read("urandom.bin", manifest))
    ok = profile.count("encrypted_or_random")
    assert ok / len(profile) >= 0.90


def test_classify_upx_payload_high_entropy(manifest):
    """Plan: >=90% of packed-region windows classify compressed-or-random.
    (UPX payload IS compressed data, so `compressed` is the correct label.)"""
    path = require_sample("hello_upx", manifest)
    m = parse(path)
    overlay = next(r for r in m.regions if r.kind == "overlay")
    data = open(path, "rb").read()[overlay.file_off:
                                   overlay.file_off + overlay.file_size]
    profile = classify_profile(data)
    hits = sum(1 for c in profile if c in ("compressed", "encrypted_or_random"))
    assert hits / len(profile) >= 0.90


def test_classify_static_text_is_code(manifest):
    profile = classify_profile(region_bytes("hello_static", ".text", manifest))
    assert profile, "static .text should span multiple decision windows"
    assert profile.count("code") / len(profile) >= 0.80


def test_classify_o2_text_single_window(manifest):
    # hello_O2 .text is 630 bytes — smaller than one decision window; the
    # classifier still runs on the short window and must land on code
    text = region_bytes("hello_O2", ".text", manifest)
    assert len(text) < 4096
    assert classify(text) == "code"


def test_rodata_not_compressed(manifest):
    rodata = region_bytes("hello_O2", ".rodata", manifest)
    assert classify(rodata) not in ("compressed", "encrypted_or_random")


def test_classify_never_raises_on_junk():
    for blob in (b"", b"\x00", b"a" * 10, bytes(range(256))):
        assert classify(blob) in ("zero", "ascii", "code", "data",
                                  "compressed", "encrypted_or_random")


def test_cli_signal_and_hist(manifest, tmp_path, capsys):
    import json as _json

    from binviz.cli import main

    path = require_sample("hello_upx", manifest)
    png = tmp_path / "sig.png"
    assert main(["signal", path, "--name", "entropy_4096",
                 "--png", str(png)]) == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["windows"] > 0 and png.exists()

    png2 = tmp_path / "hist.png"
    ramp = require_sample("ramp16.bin", manifest)
    assert main(["hist", ramp, "--n", "2", "--dtype", "u16le",
                 "--png", str(png2)]) == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["quantise"]["method"] == "linear" and png2.exists()
