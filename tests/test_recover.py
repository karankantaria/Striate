"""Phase 5 acceptance: function discovery and CFG assembly.

The stripped-recovery criterion is a *measured recall number against the
unstripped twin*, printed by the test, not a pass/fail — see §5.8. The
twin is the same binary before stripping, so addresses are identical and
the comparison is exact rather than fuzzy.
"""

import json
import shutil
import subprocess

import pytest

from binviz.cli import main
from binviz.disasm import Insn, default_backend, recover
from binviz.disasm.blocks import find_leaders, is_noreturn, split_blocks
from binviz.disasm.jumptable import resolve_jump_table
from binviz.disasm.recover import _union_length, looks_packed
from binviz.loader import MappedFile
from binviz.parse import parse
from binviz.render import cfg_to_dot

from conftest import require_sample


def program(path, **kw):
    m = parse(path)
    with MappedFile.open(path) as mf:
        return m, recover(mf.view, m, **kw)


@pytest.fixture(scope="module")
def switchy(manifest):
    return program(require_sample("switchy", manifest))


@pytest.fixture(scope="module")
def hello_o2(manifest):
    return program(require_sample("hello_O2", manifest))


# ------------------------------------------------------- block splitting

def _insn(va, size, mnemonic, op="", groups=(), targets=(), indirect=False):
    return Insn(va, size, b"", mnemonic, op, frozenset(groups), targets,
                indirect)


def test_split_blocks_diamond():
    # cmp / je +2 / mov / mov / ret  -> 4 blocks, true+false+fallthrough
    body = {
        0x10: _insn(0x10, 2, "cmp", "eax, 1"),
        0x12: _insn(0x12, 2, "je", "0x18", ("jump",), (0x18,)),
        0x14: _insn(0x14, 2, "mov", "eax, 0"),
        0x16: _insn(0x16, 2, "jmp", "0x1a", ("jump",), (0x1A,)),
        0x18: _insn(0x18, 2, "mov", "eax, 1"),
        0x1A: _insn(0x1A, 1, "ret", "", ("ret",)),
    }
    g = split_blocks(body, 0x10, "x86_64")
    assert [b.va for b in g.blocks] == [0x10, 0x14, 0x18, 0x1A]
    assert [b.terminator for b in g.blocks] == ["jcc", "jmp", "fallthrough",
                                                "ret"]
    kinds = {(e.src, e.dst): e.kind for e in g.edges}
    assert kinds == {(0x10, 0x18): "true", (0x10, 0x14): "false",
                     (0x14, 0x1A): "uncond", (0x18, 0x1A): "fallthrough"}
    assert not g.unresolved


def test_noreturn_call_gets_no_fallthrough_edge():
    body = {
        0x10: _insn(0x10, 5, "call", "0x100", ("call",), (0x100,)),
        0x15: _insn(0x15, 1, "nop"),
        0x16: _insn(0x16, 1, "ret", "", ("ret",)),
    }
    plain = split_blocks(body, 0x10, "x86_64")
    assert len(plain.blocks) == 1        # ordinary call does not split

    noret = split_blocks(body, 0x10, "x86_64",
                         noreturn_at=lambda t: t == 0x100)
    assert [b.terminator for b in noret.blocks][0] == "call_noreturn"
    # the padding after `call abort` must not become a fall-through edge
    assert not any(e.kind == "fallthrough" for e in noret.edges)
    assert noret.calls_out == [(0x10, 0x100, "direct")]


def test_indirect_jump_is_never_dropped():
    body = {0x10: _insn(0x10, 2, "jmp", "rax", ("jump",), (), indirect=True)}
    g = split_blocks(body, 0x10, "x86_64")
    assert g.blocks[0].terminator == "indirect"
    assert not g.edges
    assert g.unresolved == [(0x10, "indirect_jump", "jump_table?")]


def test_resolved_targets_become_leaders():
    # without jump-table targets in the leader set, an edge would land in
    # the middle of a block — a lie about the CFG
    body = {
        0x10: _insn(0x10, 2, "jmp", "rax", ("jump",), (), indirect=True),
        0x12: _insn(0x12, 1, "nop"),
        0x13: _insn(0x13, 1, "nop"),
        0x14: _insn(0x14, 1, "ret", "", ("ret",)),
    }
    g = split_blocks(body, 0x10, "x86_64", resolved_targets={0x10: (0x12, 0x13)})
    starts = {b.va for b in g.blocks}
    assert {0x12, 0x13} <= starts
    assert all(e.dst in starts for e in g.edges)
    assert not any(r == "edge_into_block_interior" for _v, r, _h in g.unresolved)


def test_trap_is_not_reported_as_ret():
    body = {0x10: _insn(0x10, 1, "hlt")}
    g = split_blocks(body, 0x10, "x86_64")
    assert g.blocks[0].terminator == "halt"
    assert not g.edges


def test_tail_call_is_a_call_not_an_edge():
    body = {0x10: _insn(0x10, 2, "jmp", "0x900", ("jump",), (0x900,))}
    g = split_blocks(body, 0x10, "x86_64",
                     is_function_start=lambda t: t == 0x900)
    assert not g.edges
    assert g.calls_out == [(0x10, 0x900, "tail")]


def test_find_leaders_marks_post_branch():
    body = {
        0x10: _insn(0x10, 2, "je", "0x14", ("jump",), (0x14,)),
        0x12: _insn(0x12, 2, "nop"),
        0x14: _insn(0x14, 1, "ret", "", ("ret",)),
    }
    assert find_leaders(body, 0x10) == {0x10, 0x12, 0x14}


def test_is_noreturn_matches_plt_decorations():
    assert is_noreturn("abort") and is_noreturn("exit")
    assert is_noreturn("abort@plt") and is_noreturn("__stack_chk_fail")
    assert not is_noreturn("printf") and not is_noreturn(None)


def test_union_length_counts_overlap_once():
    assert _union_length([(0, 10), (5, 20), (30, 40)]) == 30
    assert _union_length([(0, 10), (0, 10)]) == 10
    assert _union_length([]) == 0


# ------------------------------------------------------- discovery cascade

def test_symbols_are_found_with_full_confidence(hello_o2):
    model, prog = hello_o2
    truth = {s.va for s in model.symbols if s.kind == "func" and s.va}
    found = {f.va for f in prog.functions}
    assert truth <= found, f"missing {[hex(v) for v in truth - found]}"
    for f in prog.functions:
        if f.va in truth:
            assert f.discovery == "symbol" and f.confidence == 1.0


def test_every_function_records_its_provenance(hello_o2):
    from binviz.disasm.recover import CONFIDENCE, DISCOVERY

    _model, prog = hello_o2
    for f in prog.functions:
        assert f.discovery in DISCOVERY
        assert f.confidence == CONFIDENCE[f.discovery]


def test_static_binary_finds_many_functions(manifest):
    _m, prog = program(require_sample("hello_static", manifest))
    assert prog.stats["functions"] >= 20
    assert prog.stats["coverage"] > 0.5


def test_coverage_never_exceeds_one(manifest):
    """Aliased symbols cover the same bytes twice; summing would exceed 100%."""
    for name in ("hello_O0", "hello_static", "hello_O2"):
        _m, prog = program(require_sample(name, manifest))
        assert 0.0 <= prog.stats["coverage"] <= 1.0, name


# ------------------------------------------------------- stripped recall

def test_stripped_recall_vs_unstripped_twin(manifest, capsys):
    """The deliverable is a measured number, printed, not a pass/fail."""
    twin_model, _twin = program(require_sample("hello_O2", manifest))
    _sm, stripped = program(require_sample("hello_stripped", manifest))

    truth = {s.va for s in twin_model.symbols if s.kind == "func" and s.va}
    assert truth, "unstripped twin has no function symbols"
    found = {f.va for f in stripped.functions}
    recall = len(truth & found) / len(truth)

    with capsys.disabled():
        print(f"\n  stripped function recall: {len(truth & found)}/{len(truth)}"
              f" = {recall:.2f}  (target >= 0.75)")
        missed = sorted(truth - found)
        if missed:
            print(f"  missed: {[hex(v) for v in missed]}")
        by_tier = {}
        for f in stripped.functions:
            by_tier[f.discovery] = by_tier.get(f.discovery, 0) + 1
        print(f"  discovery tiers: {by_tier}")
    assert recall >= 0.75


def test_blind_recall_and_precision_on_a_large_function_set(manifest, capsys):
    """The corpus twin has only 5 functions — too small to say much. This
    blinds tier 1 on hello_static (56 symbol functions) and measures what
    tiers 2-4 recover on their own, with the symbol table as ground truth.

    **Precision is asserted alongside recall on purpose.** Recall is
    trivially raised by emitting more candidates, and doing so is the
    documented failure mode (§5.8/§5.9). Seeding descent at every
    unclaimed-gap start was measured here: recall 0.62 -> 0.84, but it
    added 139 non-functions to find 12 real ones on this sample, and 5055
    non-functions to find 14 on hello_O0. It was rejected for that reason.
    The precision floor is what keeps it rejected.
    """
    path = require_sample("hello_static", manifest)
    model = parse(path)
    truth = {s.va for s in model.symbols if s.kind == "func" and s.va}
    assert len(truth) >= 20, "sample too small to measure blind recall"

    with MappedFile.open(path) as mf:
        view = mf.view
        blind = recover(view, model, use_symbols=False)
        del view
    found = {f.va for f in blind.functions}
    hit = truth & found
    recall = len(hit) / len(truth)
    precision = len(hit) / len(found) if found else 0.0
    tiers = {}
    for f in blind.functions:
        tiers[f.discovery] = tiers.get(f.discovery, 0) + 1

    with capsys.disabled():
        print(f"\n  blind (symbol-free) recovery on {len(truth)} functions:")
        print(f"    recall    {len(hit)}/{len(truth)} = {recall:.2f}")
        print(f"    precision {len(hit)}/{len(found)} = {precision:.2f}")
        print(f"    discovery tiers: {tiers}")
        print(f"    coverage: {blind.stats['coverage'] * 100:.1f}% of "
              f"executable bytes, {blind.stats['unclaimed_blocks']} "
              f"unclaimed block(s)")
    assert recall >= 0.60
    assert precision >= 0.85, "recall must not be bought with garbage functions"


def test_stripped_binary_has_no_symbols_to_cheat_with(manifest):
    m = parse(require_sample("hello_stripped", manifest))
    assert not [s for s in m.symbols if s.kind == "func"]


# ------------------------------------------------------- jump tables

def test_switchy_resolves_all_20_cases_or_one_unresolved(switchy, manifest):
    """Never a silently truncated CFG: all 20 targets, or one honest
    unresolved record. A subset would be the worst outcome."""
    _m, prog = switchy
    expected = manifest["samples"]["switchy"]["switch_cases"]

    dispatch = next(f for f in prog.functions
                    if any(b.terminator == "indirect" for b in f.graph.blocks))
    doc = dispatch.to_json()
    indirect_blocks = [b for b in doc["blocks"] if b["terminator"] == "indirect"]
    assert len(indirect_blocks) == 1
    bid = indirect_blocks[0]["id"]
    out = [e for e in doc["edges"] if e["src"] == bid]
    unresolved = [u for u in doc["unresolved"] if u["reason"] == "indirect_jump"]

    if out:
        assert len(out) == expected, (
            f"partial jump table: {len(out)} of {expected} targets — a "
            f"truncated switch is worse than an unresolved one")
        assert not unresolved
    else:
        assert len(unresolved) == 1


def test_jump_table_targets_are_distinct_and_in_region(switchy):
    model, prog = switchy
    dispatch = next(f for f in prog.functions
                    if any(b.terminator == "indirect" for b in f.graph.blocks))
    doc = dispatch.to_json()
    bid = next(b["id"] for b in doc["blocks"] if b["terminator"] == "indirect")
    dsts = [e["dst"] for e in doc["edges"] if e["src"] == bid]
    assert len(dsts) == len(set(dsts))
    by_id = {b["id"]: b for b in doc["blocks"]}
    text = next(r for r in model.regions if r.name == ".text")
    for d in dsts:
        va = by_id[d]["va"]
        assert text.vaddr <= va < text.vaddr + text.file_size


def test_unbounded_table_is_refused(manifest):
    """A table with no recoverable `cmp` bound must be abandoned, not
    scanned until an entry looks wrong — that produced 1248 downstream
    decode failures on hello_O0 before the bound was made mandatory."""
    path = require_sample("hello_O0", manifest)
    m = parse(path)
    with MappedFile.open(path) as mf:
        view = mf.view
        prog = recover(view, m)
        del view
    for f in prog.functions:
        for _va, reason, _hint in f.graph.unresolved:
            assert reason == "indirect_jump", (
                f"{reason} indicates descent into data via a bad jump table")


def test_jump_table_rejects_non_matching_shape(switchy):
    """The matcher declines cleanly rather than inventing targets."""
    model, _prog = switchy
    backend = default_backend()
    fake = Insn(0x1000, 2, b"\xff\xe0", "jmp", "rax", frozenset({"jump"}),
                (), True)
    with MappedFile.open(model.path) as mf:
        targets, reason = resolve_jump_table(mf.view, model, backend, [], fake,
                                             "x86_64")
    assert targets == () and reason


# ------------------------------------------------------- packed binaries

def test_packed_binary_refuses_to_guess(manifest):
    """§5.9: a UPX'd .text disassembles into thousands of plausible garbage
    functions. Emitting them is worse than emitting nothing."""
    _m, prog = program(require_sample("hello_upx", manifest))
    assert prog.packed
    assert any("packed" in w for w in prog.warnings)
    assert prog.stats["functions"] < 20, "packed binary produced garbage functions"
    assert prog.stats["by_discovery"]["prologue"] == 0
    assert prog.stats["by_discovery"]["gap_sweep"] == 0
    assert not prog.unclaimed


def test_benign_binary_is_not_flagged_packed(manifest):
    for name in ("hello_O2", "hello_static", "switchy"):
        path = require_sample(name, manifest)
        m = parse(path)
        with MappedFile.open(path) as mf:
            view = mf.view
            packed, _reasons = looks_packed(view, m)
            del view
        assert not packed, f"{name} falsely flagged as packed"


def test_heuristics_can_be_disabled(manifest):
    _m, prog = program(require_sample("hello_pe.exe", manifest),
                       allow_heuristics=False)
    assert prog.stats["by_discovery"]["prologue"] == 0
    assert prog.stats["by_discovery"]["gap_sweep"] == 0
    assert not prog.unclaimed


# ------------------------------------------------------- wire format

def test_cfg_json_shape(hello_o2):
    _m, prog = hello_o2
    fn = prog.by_name("main") or prog.functions[0]
    doc = fn.to_json()
    assert set(doc) == {"function", "blocks", "edges", "unresolved",
                        "calls_out"}
    f = doc["function"]
    assert set(f) >= {"va", "name", "size", "discovery", "confidence",
                      "mode", "complete"}
    ids = {b["id"] for b in doc["blocks"]}
    assert ids == set(range(len(doc["blocks"])))
    for b in doc["blocks"]:
        assert b["confidence"] in ("high", "low")
        assert b["insns"] and b["terminator"]
        assert b["va"] < b["end_va"]
    for e in doc["edges"]:
        assert e["src"] in ids and e["dst"] in ids
        assert e["kind"] in ("true", "false", "uncond", "fallthrough",
                             "indirect_unresolved")
    json.dumps(doc)  # must be serialisable as-is


def test_blocks_do_not_overlap(hello_o2):
    _m, prog = hello_o2
    for f in prog.functions:
        spans = sorted((b.va, b.end_va) for b in f.graph.blocks)
        for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
            assert a1 <= b0, f"overlapping blocks in {f.name}"


def test_block_file_offsets_round_trip(hello_o2):
    model, prog = hello_o2
    for f in prog.functions:
        for b in f.graph.blocks:
            if b.file_off >= 0:
                assert model.off_to_va(b.file_off) == b.va


def test_program_index_shape(hello_o2):
    _m, prog = hello_o2
    doc = prog.to_json()
    assert set(doc) >= {"functions", "call_graph", "unclaimed_blocks",
                        "packed", "warnings", "stats"}
    vas = {f["va"] for f in doc["functions"]}
    for edge in doc["call_graph"]:
        assert edge["from"] in vas
    json.dumps(doc)


def test_call_graph_is_built(manifest):
    _m, prog = program(require_sample("hello_static", manifest))
    assert prog.call_graph
    vas = {f.va for f in prog.functions}
    assert all(a in vas for a, _b in prog.call_graph)


# ------------------------------------------------------- DOT export

def test_dot_export_renders_uncertainty(switchy):
    _m, prog = switchy
    dispatch = next(f for f in prog.functions
                    if any(b.terminator == "indirect" for b in f.graph.blocks))
    dot = cfg_to_dot(dispatch.to_json())
    assert dot.startswith("digraph cfg {") and dot.rstrip().endswith("}")
    assert "dispatch" in dot
    # instruction text must be line-broken with \l, never a raw newline
    for line in dot.splitlines():
        assert line.count('"') % 2 == 0, f"unbalanced quotes: {line}"
    assert "\\l" in dot


def test_dot_marks_low_confidence_blocks_dashed(manifest):
    _m, prog = program(require_sample("hello_pe.exe", manifest))
    low = next((f for f in prog.functions if f.discovery == "prologue"), None)
    if low is None:
        pytest.skip("no prologue-discovered function in this build")
    assert "dashed" in cfg_to_dot(low.to_json())


def test_dot_draws_unresolved_sentinel(hello_o2):
    _m, prog = hello_o2
    fn = next((f for f in prog.functions if f.graph.unresolved), None)
    if fn is None:
        pytest.skip("no unresolved control flow in this build")
    dot = cfg_to_dot(fn.to_json())
    assert "shape=diamond" in dot and "?" in dot


def test_dot_renders_with_graphviz(switchy, tmp_path):
    exe = shutil.which("dot")
    if exe is None:
        pytest.skip("graphviz `dot` not on PATH")
    _m, prog = switchy
    fn = prog.by_name("main") or prog.functions[0]
    src = tmp_path / "cfg.dot"
    src.write_text(cfg_to_dot(fn.to_json()), encoding="utf-8")
    r = subprocess.run([exe, "-Tsvg", str(src)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[:400]


# ------------------------------------------------------- CLI

def test_cli_functions_json(manifest, capsys):
    path = require_sample("hello_O2", manifest)
    assert main(["functions", path, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["functions"] and "stats" in doc


def test_cli_cfg_dot_and_json(manifest, tmp_path, capsys):
    path = require_sample("switchy", manifest)
    out = tmp_path / "d.dot"
    assert main(["cfg", path, "--func", "main", "--dot", str(out),
                 "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["function"]["name"] == "main"
    assert out.exists() and out.read_text(encoding="utf-8").startswith("digraph")


def test_cli_cfg_unknown_function_explains(manifest, capsys):
    path = require_sample("hello_O2", manifest)
    assert main(["cfg", path, "--func", "no_such_function"]) == 1
    assert "no recovered function" in capsys.readouterr().err


def test_cli_cfg_on_packed_says_so(manifest, capsys):
    path = require_sample("hello_upx", manifest)
    assert main(["cfg", path, "--func", "main"]) == 1
    assert "packed" in capsys.readouterr().err


# ------------------------------------------------------- robustness

def test_random_data_never_explodes(manifest):
    """Garbage in must not mean thousands of functions out."""
    path = require_sample("urandom.bin", manifest)
    m = parse(path, arch="x86_64")
    with MappedFile.open(path) as mf:
        view = mf.view
        prog = recover(view, m, allow_heuristics=False)
        del view
    assert prog.stats["functions"] < 500
    json.dumps(prog.to_json())


def test_arm64_recovery(manifest):
    path = require_sample("hello_arm64", manifest)
    model, prog = program(path)
    assert prog.stats["functions"] > 5
    assert all(f.mode == "arm64" for f in prog.functions)
    # ARM64 `tbz w0,#31,label` puts a bit index before the target; treating
    # it as an address produced edges to VA 0x1f before the last-immediate fix
    for f in prog.functions:
        for _va, reason, hint in f.graph.unresolved:
            if reason == "target_outside_function":
                assert int(hint, 16) > 0x1000, f"bogus branch target {hint}"
