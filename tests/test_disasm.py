"""Phase 4 acceptance: the instruction decoding core.

Differential oracles: iced-x86 (an independent x86 decoder, installed as
a dev dependency — this machine has no binutils) and objdump when one is
on PATH. Both compare instruction start addresses and sizes, never
mnemonic text — syntax flavours differ and that is not a disagreement.
"""

import re
import shutil
import subprocess
import time

import pytest

from binviz.disasm import (Insn, default_backend, linear_sweep, mode_at,
                           mode_for_model, recursive_descent)
from binviz.loader import MappedFile
from binviz.parse import parse

from conftest import require_sample


def text_region(model):
    return next(r for r in model.regions if r.name == ".text")


def text_bytes(path):
    m = parse(path)
    t = text_region(m)
    data = open(path, "rb").read()
    return data[t.file_off:t.file_off + t.file_size], t.vaddr, m


# ------------------------------------------------------------- unit vectors

def test_x86_64_known_vectors():
    code = bytes.fromhex(
        "55"                    # 0x1000 push rbp
        "e80a000000"            # 0x1001 call 0x1010
        "7408"                  # 0x1006 je   0x1010
        "ffe0"                  # 0x1008 jmp  rax
        "488d3d05000000"        # 0x100a lea  rdi, [rip+5] -> 0x1016
        "c3")                   # 0x1011 ret
    insns = {i.va: i for i in
             default_backend().decode(0x1000, code, "x86_64", detail=True)}
    assert sorted(insns) == [0x1000, 0x1001, 0x1006, 0x1008, 0x100A, 0x1011]

    push = insns[0x1000]
    assert (push.size, push.mnemonic) == (1, "push")
    assert not push.groups and not push.targets

    call = insns[0x1001]
    assert "call" in call.groups and "branch_relative" in call.groups
    assert call.targets == (0x1010,) and not call.is_indirect

    je = insns[0x1006]
    assert "jump" in je.groups and je.targets == (0x1010,)

    jmp_rax = insns[0x1008]
    assert "jump" in jmp_rax.groups
    assert jmp_rax.is_indirect and jmp_rax.targets == ()

    lea = insns[0x100A]
    assert lea.ptr_imms == (0x1016,)

    ret = insns[0x1011]
    assert "ret" in ret.groups
    assert ret.to_json() == {"va": 0x1011, "size": 1, "bytes": "c3",
                             "mnemonic": "ret", "op": ""}


def test_linear_sweep_covers_invalid_bytes():
    # 0x06 (push es) is invalid in 64-bit mode
    code = b"\x55" + b"\x06" + b"\xc3"
    insns = linear_sweep(code, 0x0, "x86_64")
    assert sorted(insns) == [0, 1, 2]
    assert insns[1].is_invalid and insns[1].size == 1
    assert not insns[0].is_invalid and not insns[2].is_invalid
    assert sum(i.size for i in insns.values()) == len(code)


def test_arm64_and_thumb_vectors():
    be = default_backend()
    (ret,) = be.decode(0x1000, bytes.fromhex("c0035fd6"), "arm64", detail=True)
    assert ret.mnemonic == "ret" and ret.size == 4 and "ret" in ret.groups
    (bx,) = be.decode(0x1000, bytes.fromhex("7047"), "thumb", detail=True)
    assert bx.mnemonic == "bx" and bx.size == 2


def test_unsupported_mode_raises():
    with pytest.raises(ValueError):
        list(default_backend().decode(0, b"\x00", "vax"))


# ------------------------------------------------- differential: iced-x86

@pytest.mark.parametrize("name", ["hello_O2", "hello_static"])
def test_differential_iced_x86(name, manifest):
    iced_x86 = pytest.importorskip("iced_x86")
    path = require_sample(name, manifest)
    code, va, _m = text_bytes(path)

    ours = sorted((i.va, i.size) for i in
                  linear_sweep(code, va, "x86_64").values())
    assert all(not i.is_invalid for i in
               linear_sweep(code, va, "x86_64").values()), \
        f"{name}: capstone found undecodable bytes in .text"

    dec = iced_x86.Decoder(64, code, ip=va)
    theirs = []
    for ins in dec:
        assert ins.code != iced_x86.Code.INVALID, \
            f"{name}: iced found undecodable bytes at {ins.ip:#x}"
        theirs.append((ins.ip, ins.len))
    assert ours == sorted(theirs)  # 100% — any mismatch is a real bug


# ------------------------------------------------- differential: objdump

def _find_objdump():
    for tool in ("objdump", "llvm-objdump"):
        exe = shutil.which(tool)
        if exe:
            return exe
    return None


@pytest.mark.parametrize("name", ["hello_O2", "hello_static"])
def test_differential_objdump(name, manifest):
    objdump = _find_objdump()
    if objdump is None:
        pytest.skip("no objdump/llvm-objdump on PATH")
    path = require_sample(name, manifest)
    out = subprocess.run(
        [objdump, "-d", "-j", ".text", "-z", "--no-show-raw-insn", path],
        capture_output=True, text=True, check=True).stdout
    oracle = {int(m.group(1), 16)
              for m in re.finditer(r"^\s*([0-9a-f]+):\s", out, re.M)}
    code, va, _m = text_bytes(path)
    ours = set(linear_sweep(code, va, "x86_64"))
    assert ours == oracle


# ------------------------------------------------- recursive descent

def test_recursive_descent_reaches_main(manifest):
    path = require_sample("hello_O0", manifest)
    m = parse(path)
    main_va = next(s.va for s in m.symbols if s.name == "main")
    info = {}
    with MappedFile.open(path) as mf:
        insns = recursive_descent(mf.view, m, [m.entry_va], info=info)
    assert main_va in insns
    assert not insns[main_va].is_invalid
    assert not info["truncated"]
    # main is handed to libc as a pointer, so it must have arrived via
    # the code-pointer harvest — the walk that makes this criterion pass
    assert main_va in info["pointer_seeds"]


def test_recursive_descent_stays_in_executable_regions(manifest):
    path = require_sample("hello_O2", manifest)
    m = parse(path)
    exec_ranges = [(r.vaddr, r.vaddr + r.file_size) for r in m.regions
                   if "x" in r.perms and r.file_off >= 0]
    with MappedFile.open(path) as mf:
        insns = recursive_descent(mf.view, m, [m.entry_va])
    for va in insns:
        assert any(lo <= va < hi for lo, hi in exec_ranges), hex(va)


def test_recursive_descent_deterministic(manifest):
    path = require_sample("hello_O2", manifest)
    m = parse(path)
    with MappedFile.open(path) as mf:
        a = recursive_descent(mf.view, m, [m.entry_va])
        b = recursive_descent(mf.view, m, [m.entry_va])
    assert a.keys() == b.keys()
    assert all(a[v] == b[v] for v in a)


def test_switchy_indirect_jump_recorded(manifest):
    path = require_sample("switchy", manifest)
    m = parse(path)
    info = {}
    with MappedFile.open(path) as mf:
        insns = recursive_descent(mf.view, m, [m.entry_va], info=info)
    # the 20-case switch dispatch must surface as an unresolved indirect
    # jump — never silently dropped (Phase 5 builds the ? edge from this)
    dispatch = [va for va in info["indirect_jumps"]
                if insns[va].mnemonic == "jmp"]
    assert dispatch, "switch dispatch jmp-through-register not recorded"


def test_upx_packed_never_raises(manifest):
    path = require_sample("hello_upx", manifest)
    m = parse(path)
    info = {}
    with MappedFile.open(path) as mf:
        insns = recursive_descent(mf.view, m, [m.entry_va], info=info)
    assert insns and not info["truncated"]
    # decode failures in the packed stub are recorded, not smoothed over
    assert all(insns[va].is_invalid for va in info["decode_errors"])


# ------------------------------------------------- garbage input

def test_urandom_linear_sweep_terminates(manifest):
    path = require_sample("urandom.bin", manifest)
    data = open(path, "rb").read()[:512 * 1024]
    insns = linear_sweep(data, 0, "x86_64")
    assert sum(i.size for i in insns.values()) == len(data)  # full coverage
    assert any(i.is_invalid for i in insns.values())
    assert all(0 <= i.va < len(data) for i in insns.values())


def test_urandom_recursive_descent_capped(manifest):
    path = require_sample("urandom.bin", manifest)
    m = parse(path, arch="x86_64")  # raw model, arch overridden
    with MappedFile.open(path) as mf:
        insns = recursive_descent(mf.view, m, [0], max_insns=50_000)
    assert 0 < len(insns) <= 50_000  # terminated, never raised


# ------------------------------------------------- arch dispatch

def test_arm64_sweep(manifest):
    path = require_sample("hello_arm64", manifest)
    code, va, m = text_bytes(path)
    assert mode_for_model(m) == "arm64"
    insns = linear_sweep(code, va, "arm64")
    assert all(i.size == 4 for i in insns.values())
    bad = sum(1 for i in insns.values() if i.is_invalid)
    assert bad / len(insns) < 0.05


def test_arm_mode_from_mapping_symbols(manifest):
    # despite its name the sample is ARM-mode code; what matters is that
    # $a/$t ranges drive the decode mode and $d ranges are never swept
    path = require_sample("hello_thumb", manifest)
    m = parse(path)
    assert m.arch_ranges, "expected ARM mapping-symbol ranges"
    code_ranges = [r for r in m.arch_ranges if r[2] in ("arm", "thumb")]
    assert code_ranges
    for va0, _va1, kind in code_ranges[:5]:
        assert mode_at(m, va0) == kind
    data_ranges = [r for r in m.arch_ranges if r[2] == "data"]
    assert data_ranges, "expected at least one $d range (literal pools)"
    for va, _end, _kind in data_ranges[:3]:
        assert mode_at(m, va) is None  # $d is never swept
    va0, va1, kind = code_ranges[0]
    off = m.va_to_off(va0)
    code = open(path, "rb").read()[off:off + min(va1 - va0, 4096)]
    insns = linear_sweep(code, va0, kind)
    bad = sum(1 for i in insns.values() if i.is_invalid)
    assert bad / len(insns) < 0.2


# ------------------------------------------------- performance

@pytest.mark.perf
def test_linear_sweep_10mb_under_20s(manifest):
    path = require_sample("hello_static", manifest)
    code, va, _m = text_bytes(path)
    reps = (10 * 1024 * 1024) // len(code) + 1
    big = (code * reps)[:10 * 1024 * 1024]
    t0 = time.perf_counter()
    insns = linear_sweep(big, 0x400000, "x86_64")
    elapsed = time.perf_counter() - t0
    assert sum(i.size for i in insns.values()) == len(big)
    assert elapsed < 20.0, f"10 MB sweep took {elapsed:.1f}s"
