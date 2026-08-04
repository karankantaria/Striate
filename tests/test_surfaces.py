"""Phase 3 acceptance: the surface engine."""

import json

import numpy as np
import pytest

from binviz.surfaces import SURFACES, SurfaceRequest, get_surface
from binviz.surfaces.base import reduce_mode_class
from binviz.surfaces.hilbert import d2xy, order_for, offset_at_xy, xy2d
from binviz.surfaces.image import (BAYER_MODES, CFA_PHASES, CHANNEL_PERMS,
                                   demosaic, detect_cfa_phase, parse_mode,
                                   suggest_stride_pixels)
from binviz.surfaces.ngram import to_display

from conftest import require_sample

ALL_SURFACES = ("linear", "hilbert", "image", "ngram2", "ngram3", "dotplot")


def read(name, manifest):
    return open(require_sample(name, manifest), "rb").read()


def render(name, data, w=512, h=512, dtype="u8", **params):
    req = SurfaceRequest(0, len(data), w, h, dtype, params)
    return get_surface(name).render(memoryview(data), req)


# --------------------------------------------------------------- protocol

def test_all_six_surfaces_registered():
    for name in ALL_SURFACES:
        assert get_surface(name).name == name
    assert set(ALL_SURFACES) <= set(SURFACES)


def test_unknown_surface_raises():
    with pytest.raises(KeyError):
        get_surface("nope")


@pytest.mark.parametrize("name", ALL_SURFACES)
def test_every_surface_renders_every_corpus_class(name, manifest):
    for sample in ("zeros.bin", "ascii.txt", "urandom.bin", "hello_static"):
        data = read(sample, manifest)[:200_000]
        r = render(name, data, 128, 128)
        assert r.pixels.dtype == np.uint8
        assert r.kind in ("scalar", "rgb")
        assert isinstance(r.meta, dict)


def test_request_clamp():
    req = SurfaceRequest(-5, 10_000, 0, 0).clamp(1000)
    assert (req.start, req.end) == (0, 1000)
    assert req.width >= 1 and req.height >= 1


# ------------------------------------------------------- byte-class / linear

def test_byteclass_mode_not_mean():
    """Averaging class ids invents classes that do not exist (§5.2)."""
    # one cell of 4 bytes: classes [0, 5, 5, 5] -> mode 5, mean would be ~3.75
    classes = np.array([0, 5, 5, 5], dtype=np.uint8)
    assert int(reduce_mode_class(classes, 1)[0]) == 5
    # classes [0, 5] -> mean 2.5 would invent class 2 (whitespace)
    assert int(reduce_mode_class(np.array([0, 5], dtype=np.uint8), 1)[0]) in (0, 5)


def test_linear_byteclass_corpus(manifest):
    r = render("linear", read("ascii.txt", manifest), mode="byteclass")
    assert float((r.pixels == 1).mean()) >= 0.95
    assert r.meta["categorical"] is True

    r = render("linear", read("zeros.bin", manifest), mode="byteclass")
    assert float((r.pixels == 0).mean()) == 1.0


def test_linear_signal_mode(manifest):
    data = read("hello_static", manifest)
    r = render("linear", data, 64, 64, mode="signal", signal="entropy_4096")
    assert r.meta["signal"] == "entropy_4096"
    assert r.meta["reduce"] == "max"
    assert r.pixels.max() > 0


def test_linear_upsample_warns(manifest):
    r = render("linear", read("zeros.bin", manifest)[:100], 64, 64,
               mode="byteclass")
    assert any("upsampled" in w for w in r.meta["warnings"])


# ------------------------------------------------------------- hilbert

@pytest.mark.parametrize("order", [1, 2, 3, 8])
def test_hilbert_round_trip(order):
    d = np.arange(1 << (2 * order))
    x, y = d2xy(order, d)
    assert np.array_equal(xy2d(order, x, y), d)


def test_hilbert_is_a_space_filling_curve():
    order = 6
    d = np.arange(1 << (2 * order))
    x, y = d2xy(order, d)
    # every cell visited exactly once...
    assert len({(int(a), int(b)) for a, b in zip(x, y)}) == d.size
    # ...and consecutive indices are always adjacent, which is the property
    # that makes a contiguous blob render as a compact patch
    steps = np.abs(np.diff(x)) + np.abs(np.diff(y))
    assert (steps == 1).all()


def test_hilbert_preserves_locality():
    """A 4 KiB contiguous marker must render as a connected patch:
    bounding-box area < 4x its pixel count."""
    size = 256 * 1024
    data = bytearray(size)
    at = 100 * 1024
    data[at:at + 4096] = b"\xff" * 4096
    r = render("hilbert", bytes(data), 128, 128, mode="byteclass")
    ys, xs = np.nonzero(r.pixels == 5)   # class 5 == 0xff
    assert xs.size > 0
    area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
    assert area < 4 * xs.size, f"bbox {area} vs {xs.size} lit pixels"


def test_hilbert_click_maps_back_to_offset(manifest):
    data = read("hello_static", manifest)
    req = SurfaceRequest(0, len(data), 128, 128, "u8", {})
    order = order_for(128, 128)
    d = np.array([0, 1, 500, 4095])
    x, y = d2xy(order, d)
    offs = offset_at_xy(req, order, x, y)
    assert np.array_equal(offs, (d * len(data)) // (1 << (2 * order)))
    assert (np.diff(offs) > 0).all()


def test_hilbert_reports_non_square_request():
    r = render("hilbert", b"\x00" * 4096, 100, 60, mode="byteclass")
    assert r.pixels.shape == (32, 32)   # largest power-of-two square that fits
    assert any("32x32" in w for w in r.meta["warnings"])


# --------------------------------------------------------------- n-gram

def test_ngram2_display_transforms():
    counts = np.zeros((256, 256), dtype=np.uint32)
    counts[0, 0] = 10 ** 6
    counts[5, 5] = 1
    linear = to_display(counts, "linear")
    assert linear[5, 5] == 0        # the failure mode: faint structure vanishes
    assert to_display(counts, "log1p")[5, 5] > 0
    assert to_display(counts, "rank")[5, 5] > 0
    with pytest.raises(ValueError):
        to_display(counts, "bogus")


def test_ngram2_pattern_exact(manifest):
    r = render("ngram2", read("pattern.bin", manifest))
    assert r.pixels.shape == (256, 256)
    assert r.meta["nonzero_cells"] == 16


def test_ngram2_dtype_reaches_the_surface(manifest):
    """P8's proof, at the surface layer: u16le collapses the ramp bigram."""
    data = read("ramp16.bin", manifest)
    assert render("ngram2", data, dtype="u16le").meta["nonzero_cells"] == 511
    assert render("ngram2", data, dtype="u8").meta["nonzero_cells"] == 65536


def test_ngram2_reports_dropped_tail(manifest):
    data = read("ramp16.bin", manifest)[:1001]
    r = render("ngram2", data, dtype="u16le")
    assert r.meta["quantise"]["dropped_tail_bytes"] == 1
    assert any("tail" in w for w in r.meta["warnings"])


def test_ngram3_sparse_points(manifest):
    surf = get_surface("ngram3")
    data = read("pattern.bin", manifest)
    req = SurfaceRequest(0, len(data), 256, 256, "u8", {})
    coords, counts, meta = surf.points(memoryview(data), req)
    assert len(counts) == 16 and meta["total_points"] == 16
    assert coords.shape == (16, 3)


def test_ngram3_threshold_and_cap(manifest):
    surf = get_surface("ngram3")
    data = read("hello_static", manifest)
    req = SurfaceRequest(0, len(data), 256, 256, "u8", {"max_points": 100})
    _coords, counts, meta = surf.points(memoryview(data), req)
    assert len(counts) == 100
    assert any("strongest" in w for w in meta["warnings"])   # never silent
    req2 = SurfaceRequest(0, len(data), 256, 256, "u8", {"threshold": 50})
    _c, counts2, _m = surf.points(memoryview(data), req2)
    assert (counts2 >= 50).all()


# ---------------------------------------------------------------- image

def test_all_24_bayer_modes_parse():
    assert len(BAYER_MODES) == 24
    assert len(CFA_PHASES) * len(CHANNEL_PERMS) == 24
    for i in range(24):
        spec = parse_mode(f"bayer8_{i}")           # reference-compatible naming
        assert spec["phase"] in CFA_PHASES and spec["perm"] in CHANNEL_PERMS
    for name in BAYER_MODES:
        assert parse_mode(name)["kind"] == "bayer"


def test_packed_pixel_formats_parse():
    for fmt in ("grey", "rgb", "bgr", "rgba", "bgra"):
        for depth in (8, 12, 16):
            spec = parse_mode(f"{fmt}{depth}")
            assert spec["format"] == fmt and spec["depth"] == depth
    with pytest.raises(ValueError):
        parse_mode("rgb9")
    with pytest.raises(ValueError):
        parse_mode("nonsense8")


def test_rgb_colour_bars_exact(manifest):
    spec = manifest["samples"]["rgb_raw.bin"]
    data = read("rgb_raw.bin", manifest)
    r = render("image", data, mode="rgb8", width=spec["width"])
    assert r.kind == "rgb"
    assert r.pixels.shape == (spec["height"], spec["width"], 3)
    mid = spec["height"] // 2
    for i, bar in enumerate(spec["bars_rgb"]):
        px = r.pixels[mid, i * spec["bar_width"] + spec["bar_width"] // 2]
        assert list(px) == bar, f"bar {i}"


def test_bgr_swaps_channels(manifest):
    data = read("rgb_raw.bin", manifest)
    rgb = render("image", data, mode="rgb8", width=320)
    bgr = render("image", data, mode="bgr8", width=320)
    assert np.array_equal(rgb.pixels[..., 0], bgr.pixels[..., 2])
    assert np.array_equal(rgb.pixels[..., 1], bgr.pixels[..., 1])


def test_invert(manifest):
    data = read("rgb_raw.bin", manifest)
    a = render("image", data, mode="rgb8", width=320)
    b = render("image", data, mode="rgb8", width=320, invert=True)
    assert np.array_equal(255 - a.pixels, b.pixels)


def test_wrong_stride_shears(manifest):
    """stride 321 must not reproduce the bars — this is why the suggester
    exists (§5.7)."""
    spec = manifest["samples"]["rgb_raw.bin"]
    data = read("rgb_raw.bin", manifest)
    good = render("image", data, mode="rgb8", width=320)
    bad = render("image", data, mode="rgb8", width=321)
    mid = spec["height"] // 2
    col = spec["bar_width"] // 2
    good_px = list(good.pixels[mid, col])
    bad_px = list(bad.pixels[mid, col])
    assert good_px == spec["bars_rgb"][0]
    assert bad_px != good_px


def test_bayer_cfa_phase_detection(manifest):
    """The plan asks for a checkable 'did we get the CFA phase right' test.

    It proposes high-frequency energy of the demosaiced output, but that
    metric is provably wrong here: bilinear normalised convolution averages
    both interleaved sublattices, so a wrong phase yields (R+B)/2 everywhere
    -- *smoother* than the correct phase, not checkerboarded. Measured:
    correct 4.04 vs wrong 2.05, i.e. backwards.

    What is genuinely detectable is the green lattice, and the margin there
    is ~9500x rather than the 3x asked for. RGGB vs BGGR remains provably
    indistinguishable (an exact R<->B swap), so the detector reports them
    tied instead of inventing a winner.
    """
    from binviz.elements import elements

    spec = manifest["samples"]["bayer_raw.bin"]
    data = read("bayer_raw.bin", manifest)
    mosaic = elements(data, "u12").reshape(spec["height"], spec["width"])
    ranked = detect_cfa_phase(mosaic)

    assert ranked[0]["phase"] in ("RGGB", "BGGR")
    assert ranked[0]["tied_with"] == [ranked[1]["phase"]]
    wrong = [r for r in ranked if r["phase"] in ("GRBG", "GBRG")]
    assert min(r["relative"] for r in wrong) >= 3.0
    assert spec["cfa_phase"] == "RGGB"


def test_bayer_correct_phase_is_smooth(manifest):
    """The correct phase reconstructs the smooth scene; a wrong *G lattice*
    mixes R and B, so its green channel stops tracking the true gradient."""
    from binviz.elements import elements

    spec = manifest["samples"]["bayer_raw.bin"]
    data = read("bayer_raw.bin", manifest)
    mosaic = elements(data, "u12").reshape(spec["height"], spec["width"]) / 16.0

    good = demosaic(mosaic, "RGGB")
    bad = demosaic(mosaic, "GRBG")
    # true scene: G ramps with y and is independent of x
    def row_variation(img):
        return float(np.mean(np.abs(np.diff(img[:, :, 1], axis=1))))
    assert row_variation(good) < row_variation(bad) / 3


def test_bayer_renders_all_24(manifest):
    data = read("bayer_raw.bin", manifest)[:640 * 3 * 20]
    for name in BAYER_MODES:
        r = render("image", data, mode=f"{name}_12", width=640)
        assert r.kind == "rgb" and r.pixels.shape[2] == 3


def test_bayer_channel_permutations_differ(manifest):
    data = read("bayer_raw.bin", manifest)[:640 * 3 * 40]
    rgb = render("image", data, mode="bayer_RGGB_RGB_12", width=640)
    bgr = render("image", data, mode="bayer_RGGB_BGR_12", width=640)
    assert np.array_equal(rgb.pixels[..., 0], bgr.pixels[..., 2])


def test_12bit_scaling_reported(manifest):
    data = read("bayer_raw.bin", manifest)[:640 * 3 * 10]
    r = render("image", data, mode="bayer_RGGB_RGB_12", width=640)
    assert r.meta["depth_scale"] == pytest.approx(1 / 16)
    assert any("12-bit" in w for w in r.meta["warnings"])


def test_stride_suggester(manifest):
    """§5.7: the top-3 candidates must contain the true row stride."""
    rgb = suggest_stride_pixels(read("rgb_raw.bin", manifest), "rgb8")
    assert any(c["pixels"] == 320 for c in rgb), rgb

    bayer = suggest_stride_pixels(read("bayer_raw.bin", manifest),
                                  "bayer_RGGB_RGB_12")
    assert any(c["pixels"] == 640 for c in bayer), bayer
    # the 960-byte row stride is only reachable as a sub-multiple: a Bayer
    # CFA repeats every *two* rows, so the raw peak sits at 1920
    assert any(c["bytes"] == 960 for c in bayer)


def test_stride_suggester_degenerate():
    assert suggest_stride_pixels(b"\x00" * 10, "grey8") == []


# --------------------------------------------------------------- dot plot

def test_dotplot_exact_finds_repeat_bands(manifest):
    spec = manifest["samples"]["repeats.bin"]
    data = read("repeats.bin", manifest)
    r = render("dotplot", data, 256, 256, window=8, mode="exact")
    assert r.meta["mode"] == "exact"
    assert r.meta["progress"] == 1.0

    ys, xs = np.nonzero(r.pixels)
    delta = ys.astype(int) - xs.astype(int)
    off = np.abs(delta) > 4
    assert off.sum() > 0
    # 3 identical blocks 131072 bytes apart in a 458752-byte file mapped to
    # 256 cells => bands at +-73 and +-146 cells
    spacing = spec["block_size"] * 2
    expect = spacing * 256 // len(data)
    found = {int(d) for d in delta[off]}
    assert any(abs(d - expect) <= 2 for d in found), (expect, sorted(found)[:10])
    assert any(abs(d - 2 * expect) <= 2 for d in found)


def test_dotplot_sampled_shows_same_bands(manifest):
    """Sampled mode must reveal the bands, and say it is sampled."""
    data = read("repeats.bin", manifest)
    exact = render("dotplot", data, 256, 256, window=8, mode="exact")
    sampled = render("dotplot", data, 256, 256, window=8, mode="sampled",
                     max_samples=100_000)
    assert sampled.meta["mode"] == "sampled"
    assert sampled.meta["progress"] < 1.0
    assert any("not evidence of absence" in w for w in sampled.meta["warnings"])
    # every cell the sampled plot lights is a real match, and it finds most
    lit_exact = exact.pixels > 0
    lit_sampled = sampled.pixels > 0
    assert not (lit_sampled & ~lit_exact).any(), "sampled lit a false cell"
    assert lit_sampled.sum() >= 0.9 * lit_exact.sum()


def test_dotplot_progressive_refinement(manifest):
    data = read("repeats.bin", manifest)
    surf = get_surface("dotplot")
    n = len(data) - 7
    base = SurfaceRequest(0, len(data), 128, 128, "u8",
                          {"window": 8, "mode": "sampled", "max_samples": 20_000})
    acc = surf.accumulator(base, 128, 128, n, n)
    last_progress, last_hits = -1.0, -1
    for _ in range(3):
        params = dict(base.params, accumulator=acc)
        r = surf.render(memoryview(data),
                        SurfaceRequest(0, len(data), 128, 128, "u8", params))
        assert r.meta["progress"] > last_progress
        assert r.meta["hits"] > last_hits
        last_progress, last_hits = r.meta["progress"], r.meta["hits"]


def test_dotplot_auto_selects_mode(manifest):
    small = read("pattern.bin", manifest)[:100_000]
    assert render("dotplot", small, 128, 128, window=8).meta["mode"] == "exact"
    big = read("urandom.bin", manifest)
    assert render("dotplot", big, 128, 128, window=8,
                  max_samples=5_000).meta["mode"] == "sampled"


def test_dotplot_self_similarity_of_pattern(manifest):
    """A 16-byte repeating pattern is self-similar everywhere."""
    data = read("pattern.bin", manifest)[:60_000]
    r = render("dotplot", data, 64, 64, window=8, mode="exact")
    assert r.meta["lit_fraction"] > 0.9


def test_dotplot_random_has_only_a_diagonal(manifest):
    data = read("urandom.bin", manifest)[:100_000]
    r = render("dotplot", data, 128, 128, window=8, mode="exact")
    ys, xs = np.nonzero(r.pixels)
    assert (np.abs(ys.astype(int) - xs.astype(int)) <= 1).all()


def test_dotplot_range_shorter_than_window(manifest):
    r = render("dotplot", b"abc", 64, 64, window=8)
    assert r.meta["mode"] == "empty"
    assert any("shorter" in w for w in r.meta["warnings"])


# -------------------------------------------------------------------- CLI

def test_cli_surface_all(manifest, tmp_path, capsys):
    from binviz.cli import main

    path = require_sample("hello_static", manifest)
    for name in ALL_SURFACES:
        png = tmp_path / f"{name}.png"
        argv = ["surface", path, "--name", name, "--png", str(png),
                "-w", "128", "-H", "128"]
        if name == "image":
            argv += ["-p", "mode=grey8", "-p", "width=128"]
        if name == "dotplot":
            argv += ["-p", "max_samples=2000", "-p", "end1=60000"]
        assert main(argv) == 0, name
        out = json.loads(capsys.readouterr().out)
        assert out["surface"] == name
        assert png.exists() and png.stat().st_size > 0


def test_cli_stride(manifest, capsys):
    from binviz.cli import main

    assert main(["stride", require_sample("rgb_raw.bin", manifest),
                 "--mode", "rgb8"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert any(c["pixels"] == 320 for c in out["candidates"])


def test_cli_param_typing(capsys):
    from binviz.cli import _parse_params

    p = _parse_params(["a=1", "b=2.5", "c=true", "d=hello"])
    assert p == {"a": 1, "b": 2.5, "c": True, "d": "hello"}
