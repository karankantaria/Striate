"""Phase 0 acceptance: every corpus sample exists, matches its manifest
properties, and probe identifies it. Content-structure checks pin the ground
truth that later phases' criteria are written against."""

import os
import struct
import zipfile
import zlib

import numpy as np
import pytest

from binviz.probe import probe

from conftest import require_sample, sample_path

EM = {"x86_64": 0x3E, "arm64": 0xB7, "arm": 0x28}


def elf_header(path):
    with open(path, "rb") as f:
        d = f.read(64)
    assert d[:4] == b"\x7fELF", f"{path} is not ELF"
    bits = 64 if d[4] == 2 else 32
    machine = struct.unpack_from("<H", d, 18)[0]
    return {"bits": bits, "machine": machine, "raw": d}


# ------------------------------------------------------- manifest-driven

def test_all_samples_exist(manifest):
    missing = [
        name
        for name, spec in manifest["samples"].items()
        if not spec.get("optional") and not os.path.exists(sample_path(name))
    ]
    assert not missing, f"required samples missing: {missing}"


@pytest.mark.parametrize("name", [
    "zeros.bin", "urandom.bin", "ascii.txt", "pattern.bin", "repeats.bin",
    "ramp16.bin", "floats.bin", "bayer_raw.bin", "rgb_raw.bin", "sample.png",
    "sample.zip", "hello_O0", "hello_O2", "hello_static", "hello_stripped",
    "hello_upx", "switchy", "hello_arm64", "hello_thumb", "hello_pe.exe",
])
def test_size_and_probe_format(name, manifest):
    spec = manifest["samples"][name]
    path = require_sample(name, manifest)
    size = os.path.getsize(path)
    if "size" in spec:
        assert size == spec["size"]
    if "size_min" in spec:
        assert size >= spec["size_min"]
    if "size_max" in spec:
        assert size <= spec["size_max"]
    assert probe(path)["guessed_format"] == spec["format"]


def test_elf_arch_and_bits(manifest):
    for name, spec in manifest["samples"].items():
        if spec["format"] != "elf":
            continue
        path = sample_path(name)
        if not os.path.exists(path):
            continue  # optional; covered by test_size_and_probe_format skip
        hdr = elf_header(path)
        assert hdr["bits"] == spec["bits"], name
        assert hdr["machine"] == EM[spec["arch"]], name


# ------------------------------------------------------- plan's explicit criteria

def test_probe_hello_o2_is_elf(manifest):
    assert probe(require_sample("hello_O2", manifest))["guessed_format"] == "elf"


def test_probe_sample_png_is_png(manifest):
    assert probe(require_sample("sample.png", manifest))["guessed_format"] == "png"


# ------------------------------------------------------- content structure

def test_zeros_all_zero(manifest):
    data = open(require_sample("zeros.bin", manifest), "rb").read()
    assert data.count(0) == len(data)


def test_urandom_incompressible(manifest):
    data = open(require_sample("urandom.bin", manifest), "rb").read()
    assert len(set(data)) == 256
    assert len(zlib.compress(data, 6)) > 0.95 * len(data)


def test_ascii_printable(manifest):
    data = open(require_sample("ascii.txt", manifest), "rb").read()
    assert max(data) < 0x80
    printable = sum(1 for b in data if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D))
    assert printable / len(data) >= 0.99


def test_pattern_content(manifest):
    spec = manifest["samples"]["pattern.bin"]
    pattern = bytes.fromhex(spec["pattern_hex"])
    assert len(set(pattern)) == 16
    data = open(require_sample("pattern.bin", manifest), "rb").read()
    assert data == pattern * (len(data) // 16)


def test_repeats_blocks_identical(manifest):
    spec = manifest["samples"]["repeats.bin"]
    data = open(require_sample("repeats.bin", manifest), "rb").read()
    bs = spec["block_size"]
    blocks = [data[o : o + bs] for o in spec["block_offsets"]]
    assert blocks[0] == blocks[1] == blocks[2]
    assert data[:bs] != blocks[0]  # separator differs from the repeated block


def test_ramp16_is_a_ramp(manifest):
    vals = np.fromfile(require_sample("ramp16.bin", manifest), dtype="<u2")
    assert len(vals) == 131072
    expected = (np.arange(131072, dtype=np.uint32) % 65536).astype(np.uint16)
    assert np.array_equal(vals, expected)


def test_floats_sine(manifest):
    spec = manifest["samples"]["floats.bin"]
    vals = np.fromfile(require_sample("floats.bin", manifest), dtype="<f4")
    assert len(vals) == spec["f32_count"]
    assert not np.any(np.isnan(vals))
    assert vals.min() >= -1.0 and vals.max() <= 1.0
    assert vals.max() > 0.99 and vals.min() < -0.99  # full-swing sine


def unpack_u12(data: bytes) -> np.ndarray:
    trip = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.uint16)
    a = (trip[:, 0] << 4) | (trip[:, 1] >> 4)
    b = ((trip[:, 1] & 0xF) << 8) | trip[:, 2]
    return np.stack([a, b], axis=1).ravel()


def test_bayer_u12_values(manifest):
    spec = manifest["samples"]["bayer_raw.bin"]
    data = open(require_sample("bayer_raw.bin", manifest), "rb").read()
    vals = unpack_u12(data)
    assert len(vals) == spec["width"] * spec["height"]
    assert vals.max() <= 4095
    img = vals.reshape(spec["height"], spec["width"])
    # RGGB gradient scene: R ramps with x, so row 0 even cols are ~linear in x
    r_row = img[0, 0::2].astype(np.int64)
    assert r_row[0] < 32 and r_row[-1] > 4000
    assert np.all(np.diff(r_row) >= 0)


def test_rgb_bars(manifest):
    spec = manifest["samples"]["rgb_raw.bin"]
    data = open(require_sample("rgb_raw.bin", manifest), "rb").read()
    img = np.frombuffer(data, dtype=np.uint8).reshape(spec["height"], spec["width"], 3)
    for i, bar in enumerate(spec["bars_rgb"]):
        px = img[spec["height"] // 2, i * spec["bar_width"] + spec["bar_width"] // 2]
        assert list(px) == bar, f"bar {i}"


def test_sample_zip_valid(manifest):
    path = require_sample("sample.zip", manifest)
    assert zipfile.is_zipfile(path)
    with zipfile.ZipFile(path) as z:
        assert "ascii.txt" in z.namelist()


def test_sample_png_opens(manifest):
    from PIL import Image

    with Image.open(require_sample("sample.png", manifest)) as im:
        assert im.size == (256, 256)


# ------------------------------------------------------- compiled binaries

def test_stripped_twin_layout_identical(manifest):
    """hello_stripped must share hello_O2's code layout (P5 recall baseline):
    same entry point, byte-identical program header table, no .symtab."""
    o2 = require_sample("hello_O2", manifest)
    stripped = require_sample("hello_stripped", manifest)

    def entry_and_phdrs(p):
        d = open(p, "rb").read()
        e_entry, e_phoff = struct.unpack_from("<QQ", d, 24)
        e_phentsize, e_phnum = struct.unpack_from("<HH", d, 54)
        return e_entry, d[e_phoff : e_phoff + e_phentsize * e_phnum], d

    e1, ph1, _ = entry_and_phdrs(o2)
    e2, ph2, d2 = entry_and_phdrs(stripped)
    assert e1 == e2
    assert ph1 == ph2

    # no SHT_SYMTAB (type 2) section in the stripped twin
    e_shoff = struct.unpack_from("<Q", d2, 40)[0]
    e_shentsize, e_shnum = struct.unpack_from("<HH", d2, 58)
    sh_types = [
        struct.unpack_from("<I", d2, e_shoff + i * e_shentsize + 4)[0]
        for i in range(e_shnum)
    ]
    assert 2 not in sh_types


def test_hello_o2_has_symtab(manifest):
    d = open(require_sample("hello_O2", manifest), "rb").read()
    e_shoff = struct.unpack_from("<Q", d, 40)[0]
    e_shentsize, e_shnum = struct.unpack_from("<HH", d, 58)
    sh_types = [
        struct.unpack_from("<I", d, e_shoff + i * e_shentsize + 4)[0]
        for i in range(e_shnum)
    ]
    assert 2 in sh_types


def test_upx_sample_is_packed(manifest):
    packed = require_sample("hello_upx", manifest)
    plain = require_sample("hello_static", manifest)
    data = open(packed, "rb").read()
    assert data[:4] == b"\x7fELF"
    assert b"UPX!" in data[:4096]
    assert os.path.getsize(packed) < 0.5 * os.path.getsize(plain)


def test_pe_sample(manifest):
    path = require_sample("hello_pe.exe", manifest)
    assert probe(path)["guessed_format"] == "pe"
    d = open(path, "rb").read(0x200)
    e_lfanew = struct.unpack_from("<I", d, 0x3C)[0]
    machine = struct.unpack_from("<H", d, e_lfanew + 4)[0]
    assert machine == 0x8664
