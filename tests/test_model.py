"""Phase 1 acceptance: the address-space model.

The oracle for the .text cross-check is a direct struct parse of the ELF
section-header table — independent of LIEF, so a LIEF regression cannot
silently agree with itself.
"""

import json
import os
import shutil
import struct

import numpy as np
import pytest

from binviz.cli import main
from binviz.parse import parse

from conftest import require_sample

MAPPED_ELVES = ["hello_O0", "hello_O2", "hello_static", "hello_stripped",
                "hello_upx", "switchy", "hello_arm64", "hello_thumb"]


# ------------------------------------------------- round-trip property test

@pytest.mark.parametrize("name", MAPPED_ELVES + ["hello_pe.exe"])
def test_off_va_round_trip_10k(name, manifest):
    m = parse(require_sample(name, manifest))
    assert m.mappings, f"{name}: no mappings"
    rng = np.random.default_rng(0)
    per = 10_000 // len(m.mappings) + 1
    checked = 0
    for fo, size, _va in m.mappings:
        for off in rng.integers(fo, fo + size, per):
            off = int(off)
            va = m.off_to_va(off)
            assert va is not None, f"{name}: off_to_va({off:#x}) is None"
            back = m.va_to_off(va)
            assert back == off, f"{name}: {off:#x} -> {va:#x} -> {back}"
            checked += 1
    assert checked >= 10_000 or checked >= sum(s for _, s, _ in m.mappings)


def test_unmapped_offsets_return_none(manifest):
    m = parse(require_sample("hello_O2", manifest))
    assert m.off_to_va(m.size + 100) is None
    assert m.va_to_off(0x10) is None  # below image base


# ------------------------------------------------- .text vs raw section headers

def read_elf_sections(path):
    """Independent oracle: struct-parse the section-header table."""
    d = open(path, "rb").read()
    e_shoff = struct.unpack_from("<Q", d, 40)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 58)
    secs = []
    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        sh_name, _sh_type = struct.unpack_from("<II", d, base)
        sh_addr, sh_offset, sh_size = struct.unpack_from("<QQQ", d, base + 16)
        secs.append((sh_name, sh_addr, sh_offset, sh_size))
    str_off = secs[e_shstrndx][2]
    named = {}
    for sh_name, addr, off, size in secs:
        end = d.index(b"\x00", str_off + sh_name)
        named[d[str_off + sh_name : end].decode()] = (addr, off, size)
    return named

@pytest.mark.parametrize("name", ["hello_O0", "hello_O2", "hello_static", "switchy"])
def test_text_matches_raw_section_headers(name, manifest):
    path = require_sample(name, manifest)
    oracle = read_elf_sections(path)
    m = parse(path)
    text = next(r for r in m.regions if r.name == ".text")
    addr, off, size = oracle[".text"]
    assert text.file_off == off
    assert text.vaddr == addr
    assert text.file_size == size
    assert "x" in text.perms
    # and the mapping agrees with the section header
    assert m.off_to_va(off) == addr
    assert m.va_to_off(addr + size - 1) == off + size - 1


# ------------------------------------------------- plan's explicit criteria

def test_upx_parses_with_warnings_and_no_imports(manifest):
    m = parse(require_sample("hello_upx", manifest))
    assert m.format == "elf"
    assert len(m.warnings) >= 1
    assert len(m.imports) <= 2
    assert any("section" in w or "overlap" in w or "overlay" in w
               for w in m.warnings)


def test_png_raw_fallback(manifest):
    m = parse(require_sample("sample.png", manifest))
    assert m.format == "raw"
    assert len(m.regions) == 1
    assert m.regions[0].file_size == m.size


def test_truncated_elf_still_models(manifest, tmp_path):
    data = open(require_sample("hello_O2", manifest), "rb").read()
    p = tmp_path / "trunc"
    p.write_bytes(data[: int(len(data) * 0.6)])
    m = parse(p)  # must not raise
    assert m is not None
    assert len(m.warnings) >= 1
    assert m.size == int(len(data) * 0.6)


def test_raw_arch_override(manifest):
    m = parse(require_sample("zeros.bin", manifest), arch="x86_64")
    assert m.format == "raw"
    assert m.arch == "x86_64"
    assert m.off_to_va(100) == 100  # identity mapping


# ------------------------------------------------- structural invariants

@pytest.mark.parametrize("name", MAPPED_ELVES)
def test_regions_partition_the_file(name, manifest):
    """File-backed regions must tile [0, size) with no overlap and no holes."""
    m = parse(require_sample(name, manifest))
    backed = sorted((r for r in m.regions if r.file_off >= 0),
                    key=lambda r: r.file_off)
    pos = 0
    for r in backed:
        assert r.file_off == pos, f"{name}: hole/overlap before {r.name} at {pos:#x}"
        assert r.file_size > 0
        pos = r.file_off + r.file_size
    assert pos == m.size, f"{name}: coverage ends at {pos:#x}, file is {m.size:#x}"


@pytest.mark.parametrize("name", MAPPED_ELVES)
def test_mappings_disjoint_both_axes(name, manifest):
    m = parse(require_sample(name, manifest))
    for axis in (0, 2):
        ivs = sorted((mp[axis], mp[axis] + mp[1]) for mp in m.mappings)
        for (a0, a1), (b0, _b1) in zip(ivs, ivs[1:]):
            assert a1 <= b0, f"{name}: overlap on axis {axis}"


def test_region_at_off_finds_text(manifest):
    m = parse(require_sample("hello_O2", manifest))
    text = next(r for r in m.regions if r.name == ".text")
    r = m.region_at_off(text.file_off + text.file_size // 2)
    assert r is not None and r.name == ".text"


def test_entry_is_executable(manifest):
    for name in ("hello_O0", "hello_O2", "hello_static", "switchy"):
        m = parse(require_sample(name, manifest))
        assert m.entry_va is not None
        r = m.region_at_va(m.entry_va)
        assert r is not None and "x" in r.perms, name


def test_symbols_and_imports_hello_o2(manifest):
    m = parse(require_sample("hello_O2", manifest))
    names = {s.name for s in m.symbols}
    assert "main" in names
    funcs = [s for s in m.symbols if s.kind == "func"]
    assert funcs and all(s.source in ("symtab", "dynsym") for s in funcs)
    assert any("printf" in i for i in m.imports)
    assert any(i.startswith("libc.so.6!") for i in m.imports)


def test_overlay_detected(manifest, tmp_path):
    src = require_sample("hello_O2", manifest)
    p = tmp_path / "overlaid"
    shutil.copy(src, p)
    with open(p, "ab") as f:
        f.write(b"APPENDED_PAYLOAD" * 64)
    m = parse(p)
    overlay = [r for r in m.regions if r.kind == "overlay"]
    assert len(overlay) == 1
    assert overlay[0].file_size >= 1024
    assert any("overlay" in w for w in m.warnings)


def test_bss_present_not_file_backed(manifest):
    m = parse(require_sample("hello_static", manifest))
    bss = [r for r in m.regions if r.name == ".bss"]
    assert bss and bss[0].file_off == -1 and bss[0].vsize > 0


def test_thumb_arch_ranges(manifest):
    m = parse(require_sample("hello_thumb", manifest))
    assert m.arch == "arm"
    kinds = {k for _, _, k in m.arch_ranges}
    assert "thumb" in kinds or "arm" in kinds
    assert "data" in kinds  # literal pools must be marked, never swept
    for va0, va1, _k in m.arch_ranges:
        assert va1 > va0


def test_pe_model(manifest):
    m = parse(require_sample("hello_pe.exe", manifest))
    assert m.format == "pe" and m.arch == "x86_64"
    text = next(r for r in m.regions if r.name == ".text")
    assert "x" in text.perms
    assert m.entry_va is not None
    r = m.region_at_va(m.entry_va)
    assert r is not None and "x" in r.perms
    assert any("!" in i for i in m.imports)


def test_to_json_serialises(manifest):
    m = parse(require_sample("hello_O2", manifest))
    s = json.dumps(m.to_json())
    round_tripped = json.loads(s)
    assert round_tripped["format"] == "elf"
    assert round_tripped["mappings"]
    assert round_tripped["regions"][0]["kind"] == "header"


def test_cli_model_json(manifest, capsys):
    path = require_sample("hello_O2", manifest)
    assert main(["model", path, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["format"] == "elf"
    assert out["arch"] == "x86_64"
    assert any(r["name"] == ".text" for r in out["regions"])


def test_cli_model_summary_all_corpus(manifest, capsys):
    """`binviz model` must succeed on every built corpus sample."""
    out_dir = os.path.join(os.path.dirname(__file__), "..", "corpus", "out")
    for name in manifest["samples"]:
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            continue
        assert main(["model", path]) == 0, name
        capsys.readouterr()
