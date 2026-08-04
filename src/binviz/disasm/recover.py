"""Function discovery and CFG assembly — the wire format the frontend eats.

Discovery is a strict five-tier cascade and **every function records the
tier that found it** (§5.8). Tiers 1-2 are ground truth, tier 3 is nearly
so, tiers 4-5 are guesses and say so. A tool that presents a prologue-scan
guess and a symbol-table fact identically is lying by omission; the
provenance is what the UI badges and what makes stripped-binary coverage
reportable as a *measured number* rather than a pass/fail.

On packed binaries the heuristic tiers are suppressed entirely. Sweeping
a compressed `.text` yields thousands of plausible-looking garbage
functions, and rendering those is worse than rendering nothing (§5.9).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from ..model import BinaryModel
from .backend import Insn, mode_at, mode_for_model
from .blocks import BlockGraph, is_noreturn, split_blocks
from .capstone_be import default_backend
from .jumptable import resolve_jump_table
from .sweep import _ExecRanges, falls_through, linear_sweep, recursive_descent

# Discovery tiers, best first. "ptr_target" is an addition to the planned
# cascade and it is not optional: startup code hands `main` to libc as a
# *pointer*, never a call, so on a stripped binary the largest function in
# the program is invisible to call-target harvesting. Address-sized
# immediates landing in executable memory are the standard signal for it.
# Ranked below call_target because a code pointer may also be a jump-table
# entry or a label rather than a function start.
DISCOVERY = ("symbol", "entry", "call_target", "ptr_target", "prologue",
             "gap_sweep")
CONFIDENCE = {"symbol": 1.0, "entry": 1.0, "call_target": 0.9,
              "ptr_target": 0.8, "prologue": 0.5, "gap_sweep": 0.2}
HIGH_CONFIDENCE_TIERS = ("symbol", "entry", "call_target", "ptr_target")

# instructions per function before we stop and mark it incomplete
MAX_FUNC_INSNS = 200_000
# jump-table resolution rounds (each may reveal new code)
MAX_RESOLVE_ROUNDS = 4
# shorter unclaimed runs are alignment padding, not code worth reporting
MIN_GAP_BYTES = 16

# Prologue signatures, per mode. Deliberately few and deliberately
# low-confidence: at -O2 there is no frame pointer and these mostly miss.
_PROLOGUES: dict[str, tuple[bytes, ...]] = {
    "x86_64": (
        b"\xf3\x0f\x1e\xfa",       # endbr64
        b"\x55\x48\x89\xe5",       # push rbp; mov rbp, rsp
        b"\x55\x48\x83\xec",       # push rbp; sub rsp, imm8
        b"\x48\x83\xec",           # sub rsp, imm8
    ),
    "x86": (
        b"\xf3\x0f\x1e\xfb",       # endbr32
        b"\x55\x89\xe5",           # push ebp; mov ebp, esp
    ),
}
# ARM64 `stp x29, x30, [sp, #-N]!` — match the encoding, masking the imm
_ARM64_PROLOGUE_MASK = 0xFFC003FF
_ARM64_PROLOGUE_VAL = 0xA9807BFD


@dataclass
class Function:
    va: int
    name: str
    size: int                       # span from entry to the highest end_va
    discovery: str
    confidence: float
    mode: str
    complete: bool = True
    graph: BlockGraph = field(default_factory=BlockGraph)
    insn_count: int = 0

    def to_json(self) -> dict:
        """The per-function CFG document (`GET /api/{id}/cfg/{va}`)."""
        g = self.graph
        ids = {b.va: i for i, b in enumerate(g.blocks)}
        return {
            "function": {
                "va": self.va, "name": self.name, "size": self.size,
                "discovery": self.discovery, "confidence": self.confidence,
                "mode": self.mode, "complete": self.complete,
            },
            "blocks": [b.to_json(ids[b.va]) for b in g.blocks],
            "edges": [{"src": ids[e.src], "dst": ids[e.dst], "kind": e.kind}
                      for e in g.edges if e.src in ids and e.dst in ids],
            "unresolved": [{"va": va, "reason": reason, "hint": hint}
                           for va, reason, hint in g.unresolved],
            "calls_out": [
                {"from_va": fv, "target_va": tv, "name": None, "kind": kind}
                for fv, tv, kind in g.calls_out
            ],
        }

    def index_entry(self) -> dict:
        g = self.graph
        return {
            "va": self.va, "name": self.name, "size": self.size,
            "discovery": self.discovery, "confidence": self.confidence,
            "mode": self.mode, "complete": self.complete,
            "blocks": len(g.blocks), "edges": len(g.edges),
            "insns": self.insn_count, "unresolved": len(g.unresolved),
        }


@dataclass
class Program:
    functions: list[Function]
    call_graph: list[tuple[int, int]]          # (caller_va, callee_va)
    warnings: list[str]
    stats: dict
    unclaimed: list = field(default_factory=list)   # gap-sweep Blocks
    packed: bool = False

    def by_va(self, va: int) -> Function | None:
        return next((f for f in self.functions if f.va == va), None)

    def by_name(self, name: str) -> Function | None:
        return next((f for f in self.functions if f.name == name), None)

    def to_json(self) -> dict:
        """The program-level index (`GET /api/{id}/functions`)."""
        return {
            "functions": [f.index_entry() for f in self.functions],
            "call_graph": [{"from": a, "to": b} for a, b in self.call_graph],
            "unclaimed_blocks": [
                {"va": b.va, "end_va": b.end_va, "file_off": b.file_off,
                 "insns": len(b.insns), "confidence": b.confidence}
                for b in self.unclaimed
            ],
            "packed": self.packed,
            "warnings": self.warnings,
            "stats": self.stats,
        }


# ------------------------------------------------------------ packing gate

def looks_packed(buf, model: BinaryModel) -> tuple[bool, list[str]]:
    """Cheap packed check gating the heuristic discovery tiers.

    Entropy of executable file-backed regions against the corpus
    calibration, plus import starvation. Phase 11 owns the real verdict;
    this only decides whether guessing at functions is defensible.
    """
    from ..signals import DECISION_WINDOW, load_calibration
    from ..stats import window_stats

    reasons: list[str] = []
    thresholds = load_calibration()["derived"]
    exec_regions = [r for r in model.regions
                    if "x" in r.perms and r.file_off >= 0
                    and r.file_size >= DECISION_WINDOW]
    if not exec_regions:
        return False, reasons

    total, high = 0, 0
    worst = 0.0
    for r in exec_regions:
        chunk = buf[r.file_off:r.file_off + r.file_size]
        values = window_stats(chunk, DECISION_WINDOW, DECISION_WINDOW,
                              which=("entropy",))["entropy"]
        if not len(values):
            continue
        total += len(values)
        high += int((values >= thresholds["packed_h_min"]).sum())
        worst = max(worst, float(values.max()))
    if total and high / total >= 0.5:
        reasons.append(
            f"executable regions: {high}/{total} windows at or above the "
            f"calibrated packed threshold {thresholds['packed_h_min']:.2f} "
            f"bits/byte (peak {worst:.2f})")
    if model.format in ("elf", "pe") and len(model.imports) <= 3:
        reasons.append(f"import-starved: {len(model.imports)} imports")
    # entropy alone is the decisive signal; starvation alone is not
    packed = bool(reasons) and any("executable regions" in r for r in reasons)
    return packed, reasons


# ------------------------------------------------------------ seeds

def _seed_symbols(model: BinaryModel) -> dict[int, str]:
    """Tier 1: symbol tables and export tables."""
    out: dict[int, str] = {}
    for s in model.symbols:
        if s.kind in ("func", "export") and s.va:
            out.setdefault(s.va & ~1 if model.arch == "arm" else s.va, s.name)
    return out


_INIT_SECTIONS = {".init": "_init", ".fini": "_fini"}
_INIT_ARRAYS = (".preinit_array", ".init_array", ".fini_array")


def _seed_entry(buf, model: BinaryModel) -> dict[int, str]:
    """Tier 2: entry point, `.init`/`.fini`, and the init/fini pointer
    arrays. These are ground truth — the loader really does call them —
    and on a stripped static binary they are the only route to the
    constructor chain, which nothing else reaches."""
    out: dict[int, str] = {}
    if model.entry_va is not None:
        out[model.entry_va] = "_entry"

    width = 8 if model.bits == 64 else 4
    byteorder = "big" if model.endian == "big" else "little"
    for r in model.regions:
        if r.name in _INIT_SECTIONS and r.vaddr >= 0:
            out.setdefault(r.vaddr, _INIT_SECTIONS[r.name])
        elif (r.name in _INIT_ARRAYS and r.file_off >= 0
              and 0 < r.file_size <= 1 << 16):
            data = bytes(buf[r.file_off:r.file_off + r.file_size])
            for i in range(0, len(data) - width + 1, width):
                va = int.from_bytes(data[i:i + width], byteorder)
                if va:      # 0 and -1 are the conventional terminators
                    out.setdefault(va, f"{r.name.lstrip('.')}[{i // width}]")
    return out


def _prologue_seeds(buf, model: BinaryModel, exec_ranges: _ExecRanges,
                    insns: dict[int, Insn], mode: str) -> list[int]:
    """Tier 4: prologue signatures over executable bytes no earlier tier
    reached. Unreliable at -O2 by construction — hence confidence 0.5.

    The only filter is "not inside an instruction we already decoded".
    An alignment filter was tried and removed: real function starts in a
    densely packed .text (musl) are frequently unaligned, and requiring
    4-byte alignment discarded most true positives.
    """
    inside = _merge_covered(insns)
    starts = [c[0] for c in inside]

    def already_decoded(va: int) -> bool:
        i = bisect_right(starts, va) - 1
        return i >= 0 and inside[i][1] > va

    seeds: list[int] = []
    if mode == "arm64":
        import numpy as np
        for va0, va1, fo in exec_ranges.ranges:
            n = (va1 - va0) // 4
            if n <= 0:
                continue
            words = np.frombuffer(bytes(buf[fo:fo + n * 4]), dtype="<u4")
            hits = np.flatnonzero(
                (words & _ARM64_PROLOGUE_MASK) == _ARM64_PROLOGUE_VAL)
            seeds.extend(va for h in hits
                         if not already_decoded(va := va0 + int(h) * 4))
        return seeds

    patterns = _PROLOGUES.get(mode, ())
    for va0, va1, fo in exec_ranges.ranges:
        data = bytes(buf[fo:fo + (va1 - va0)])
        for pat in patterns:
            pos = data.find(pat)
            while pos != -1:
                va = va0 + pos
                if not already_decoded(va):
                    seeds.append(va)
                pos = data.find(pat, pos + 1)
    return sorted(set(seeds))


# ------------------------------------------------------------ extents

def _walk_function(insns: dict[int, Insn], starts: set[int], entry: int,
                   mode: str, resolved: dict[int, tuple[int, ...]],
                   ) -> tuple[dict[int, Insn], bool]:
    """Instructions belonging to one function, by intra-procedural walk.

    Bounded by other function starts rather than by address order, so
    hot/cold splitting and non-contiguous layout do not swallow
    neighbours. Returns (body, complete).
    """
    body: dict[int, Insn] = {}
    work = [entry]
    complete = True
    while work:
        va = work.pop()
        while va in insns and va not in body:
            if len(body) >= MAX_FUNC_INSNS:
                return body, False
            insn = insns[va]
            body[va] = insn
            if insn.is_invalid:
                complete = False
                break
            if "jump" in insn.groups:
                for t in list(insn.targets) + list(resolved.get(va, ())):
                    if t in insns and t not in starts:
                        work.append(t)
            if not falls_through(insn, mode):
                break
            nxt = insn.end_va
            if nxt in starts or nxt not in insns:
                break
            va = nxt
    return body, complete


# ------------------------------------------------------------ recovery

def recover(buf, model: BinaryModel, backend=None, *,
            allow_heuristics: bool = True,
            resolve_tables: bool = True,
            use_symbols: bool = True) -> Program:
    """Run the discovery cascade and assemble every function's CFG.

    `use_symbols=False` blinds tier 1, simulating a stripped binary on a
    sample whose symbol table is still available as ground truth. That is
    how stripped recall gets measured against a large function set rather
    than only against whatever twin the corpus happens to ship.
    """
    backend = backend or default_backend()
    warnings: list[str] = []
    mode = mode_for_model(model)
    if mode is None:
        return Program([], [], [f"no decoder for arch {model.arch!r}"],
                       {"functions": 0}, packed=False)

    packed, reasons = looks_packed(buf, model)
    if packed:
        warnings.append("binary appears packed; static CFG is not meaningful "
                        "— heuristic function discovery suppressed")
        warnings.extend(reasons)

    exec_ranges = _ExecRanges(model, buf)
    names = _seed_symbols(model) if use_symbols else {}
    entry_seeds = _seed_entry(buf, model)
    tier: dict[int, str] = {}
    for va in names:
        tier[va] = "symbol"
    for va in entry_seeds:
        tier.setdefault(va, "entry")
        names.setdefault(va, entry_seeds[va])

    in_exec = {va for va in tier if exec_ranges.find(va) is not None}
    dropped = len(tier) - len(in_exec)
    if dropped:
        warnings.append(f"{dropped} symbol seed(s) outside executable regions")
    tier = {va: t for va, t in tier.items() if va in in_exec}

    # ---- tiers 1-3: descent from ground truth, harvesting call targets
    info: dict = {}
    insns = recursive_descent(buf, model, list(tier), backend, info=info)
    resolved: dict[int, tuple[int, ...]] = {}

    if resolve_tables:
        for _ in range(MAX_RESOLVE_ROUNDS):
            new = _resolve_round(buf, model, backend, insns, resolved, mode)
            if not new:
                break
            more: dict = {}
            insns.update(recursive_descent(buf, model, new, backend, info=more))
            info["indirect_jumps"].extend(more.get("indirect_jumps", []))

    _harvest(insns, info, tier, exec_ranges, model)

    # ---- tier 4: prologue scan over what tiers 1-3 never reached
    if allow_heuristics and not packed:
        pro = _prologue_seeds(buf, model, exec_ranges, insns, mode)
        if pro:
            more: dict = {}
            insns.update(recursive_descent(buf, model, pro, backend, info=more))
            for va in pro:
                if va in insns and va not in tier:
                    tier[va] = "prologue"
            info["indirect_jumps"].extend(more.get("indirect_jumps", []))
            # a prologue-found function calls things nothing else reached;
            # without this second harvest, everything below `main` on a
            # stripped static binary stays invisible
            _harvest(insns, more, tier, exec_ranges, model)

    # ---- assemble functions
    starts = set(tier)
    order = sorted(starts)
    functions: list[Function] = []
    for va in order:
        fn_mode = mode_at(model, va) or mode
        body, complete = _walk_function(insns, starts, va, fn_mode, resolved)
        if not body:
            continue
        graph = split_blocks(
            body, va, fn_mode,
            confidence="high" if tier[va] in HIGH_CONFIDENCE_TIERS else "low",
            noreturn_at=lambda t: is_noreturn(names.get(t)),
            resolved_targets=resolved,
            is_function_start=lambda t, s=starts, e=va: t in s and t != e,
            off_at=model.va_to_off,
        )
        end = max((b.end_va for b in graph.blocks), default=va)
        functions.append(Function(
            va=va, name=names.get(va) or f"sub_{va:x}", size=end - va,
            discovery=tier[va], confidence=CONFIDENCE[tier[va]],
            mode=fn_mode, complete=complete and not info.get("truncated"),
            graph=graph, insn_count=len(body),
        ))

    # ---- tier 5: gap sweep over executable bytes still unclaimed
    unclaimed: list = []
    if allow_heuristics and not packed:
        unclaimed = _gap_sweep(buf, model, exec_ranges, insns, mode, backend)

    call_graph = sorted({
        (f.va, tv) for f in functions for _fv, tv, kind in f.graph.calls_out
        if tv is not None and kind in ("direct", "tail")
    })

    # union, not sum: aliased symbols (malloc/__libc_malloc) legitimately
    # produce two functions over the same bytes, and summing reports >100%
    claimed_bytes = _union_length(
        (b.va, b.end_va) for f in functions for b in f.graph.blocks)
    exec_bytes = sum(hi - lo for lo, hi, _ in exec_ranges.ranges)
    stats = {
        "functions": len(functions),
        "by_discovery": {t: sum(1 for f in functions if f.discovery == t)
                         for t in DISCOVERY},
        "blocks": sum(len(f.graph.blocks) for f in functions),
        "edges": sum(len(f.graph.edges) for f in functions),
        "unresolved": sum(len(f.graph.unresolved) for f in functions),
        "indirect_jumps": len(info.get("indirect_jumps", [])),
        "jump_tables_resolved": len(resolved),
        "instructions": len(insns),
        "exec_bytes": exec_bytes,
        "claimed_bytes": claimed_bytes,
        "coverage": round(claimed_bytes / exec_bytes, 4) if exec_bytes else 0.0,
        "unclaimed_blocks": len(unclaimed),
    }
    if info.get("truncated"):
        warnings.append("decode cap hit; instruction set is incomplete")

    return Program(functions=functions, call_graph=call_graph,
                   warnings=warnings, stats=stats, unclaimed=unclaimed,
                   packed=packed)


def _resolve_round(buf, model, backend, insns, resolved, mode) -> list[int]:
    """Try the jump-table matcher on every unresolved indirect jump;
    return newly reachable target VAs."""
    ordered = sorted(insns)
    new: list[int] = []
    for i, va in enumerate(ordered):
        insn = insns[va]
        if va in resolved or not (insn.is_indirect and "jump" in insn.groups):
            continue
        before = _contiguous_run_before(insns, ordered, i)
        targets, _reason = resolve_jump_table(
            buf, model, backend, before, insn, mode)
        if targets:
            resolved[va] = targets
            new.extend(t for t in targets if t not in insns)
    return new


def _harvest(insns: dict[int, Insn], info: dict, tier: dict[int, str],
             exec_ranges: _ExecRanges, model: BinaryModel) -> None:
    """Tier 3: promote direct call targets and taken code pointers.

    Run again after every descent — a function found later calls things
    nothing earlier reached, and harvesting only once leaves everything
    below it invisible.
    """
    for insn in list(insns.values()):
        if "call" in insn.groups:
            for t in insn.targets:
                if t not in tier and exec_ranges.find(t) is not None:
                    tier[t] = "call_target"
    # only pointers that actually decoded: a pointer into a jump table or
    # a data island is not a function, and missing one beats inventing one
    for p in info.get("pointer_seeds", ()):
        va = p & ~1 if model.arch == "arm" else p
        if va not in tier and va in insns and exec_ranges.find(va) is not None:
            tier[va] = "ptr_target"


def _contiguous_run_before(insns: dict[int, Insn], ordered: list[int],
                           i: int) -> list[Insn]:
    """The address-contiguous run of instructions ending just before
    ordered[i] — the dispatch sequence, in practice."""
    run: list[Insn] = []
    want = ordered[i]
    j = i - 1
    while j >= 0 and insns[ordered[j]].end_va == want:
        run.append(insns[ordered[j]])
        want = ordered[j]
        j -= 1
    run.reverse()
    return run


def _union_length(intervals) -> int:
    """Total length covered by [start, end) intervals, counting overlap once."""
    total = 0
    end = -1
    for lo, hi in sorted(intervals):
        if hi <= end:
            continue
        total += hi - max(lo, end) if end > lo else hi - lo
        end = hi
    return total


def _merge_covered(insns: dict[int, Insn]) -> list[tuple[int, int]]:
    """Decoded instruction addresses merged into [start, end) intervals."""
    out: list[tuple[int, int]] = []
    for va in sorted(insns):
        end = insns[va].end_va
        if out and va <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((va, end))
    return out


def _gap_sweep(buf, model, exec_ranges, insns, mode, backend) -> list:
    """Tier 5: linear-sweep executable bytes no function claimed.

    Rendered as an 'unclaimed code' band, never hidden and never promoted
    to a function — a linear sweep resynchronises within a few bytes of a
    wrong start, so its output looks plausible while being unverified.
    """
    from .blocks import Block

    covered = _merge_covered(insns)
    starts = [c[0] for c in covered]
    out: list[Block] = []
    for va0, va1, fo in exec_ranges.ranges:
        pos = va0
        i = bisect_right(starts, va0) - 1
        if i >= 0 and covered[i][1] > pos:
            pos = min(covered[i][1], va1)
        i = bisect_left(starts, pos)
        while pos < va1:
            gap_end = min(covered[i][0], va1) if i < len(covered) else va1
            if gap_end - pos >= MIN_GAP_BYTES:
                data = bytes(buf[fo + (pos - va0):fo + (gap_end - va0)])
                swept = linear_sweep(data, pos, mode, backend)
                real = [swept[v] for v in sorted(swept)
                        if not swept[v].is_invalid]
                if real:
                    out.append(Block(pos, gap_end, real, "fallthrough",
                                     confidence="low",
                                     file_off=fo + (pos - va0)))
            if i >= len(covered):
                break
            pos = min(max(covered[i][1], gap_end), va1)
            i += 1
    return out
