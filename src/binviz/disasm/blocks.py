"""Basic-block splitting and terminator/edge classification.

Mechanical, with two subtleties that decide whether the CFG is honest:

**Noreturn calls.** Treating `call abort` as falling through invents an
edge into alignment padding and grows a fake tail on the function. Calls
to known-noreturn names end their block with no fall-through edge.

**Indirect jumps.** Every non-trivial `switch` compiles to one. They are
never dropped: the block's terminator is "indirect" and the function
carries an `unresolved` record, so the UI can draw a dangling edge to a
visible "?" sentinel instead of showing a plausible-looking lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .backend import Insn
from .sweep import is_uncond_jump

# Calls to these never come back. Without this list a `call abort` grows a
# spurious fall-through edge into whatever follows (usually padding).
NORETURN_NAMES = frozenset({
    "exit", "_exit", "_Exit", "quick_exit", "abort",
    "__stack_chk_fail", "__stack_chk_fail_local", "__fortify_fail",
    "__chk_fail", "__assert_fail", "__assert_fail_base", "__assert_perror_fail",
    "_Unwind_Resume", "_Unwind_Resume_or_Rethrow", "longjmp", "siglongjmp",
    "__longjmp_chk", "pthread_exit", "thrd_exit",
    "__cxa_throw", "__cxa_rethrow", "__cxa_bad_cast", "__cxa_bad_typeid",
    "_ZSt9terminatev", "_ZSt10unexpectedv",
    "__libc_start_main", "err", "errx", "verr", "verrx",
    "rust_begin_unwind", "abort_report_np",
})

# terminator values in the wire format. "halt" is an addition to the
# planned enum: hlt/ud2/brk end control flow without returning, and
# labelling them "ret" would be a lie about where flow goes.
TERMINATORS = ("jcc", "jmp", "ret", "call_noreturn", "fallthrough",
               "indirect", "invalid", "halt")
EDGE_KINDS = ("true", "false", "uncond", "fallthrough", "indirect_unresolved")

_TRAPS = {"hlt", "ud2", "ud0", "ud1", "int3", "brk", "udf"}


def normalise_symbol(name: str) -> str:
    """Strip decorations so PLT thunks and aliases match NORETURN_NAMES."""
    return name.split("@", 1)[0].lstrip("_") or name


_NORETURN_STRIPPED = frozenset(normalise_symbol(n) for n in NORETURN_NAMES)


def is_noreturn(name: str | None) -> bool:
    return bool(name) and (name in NORETURN_NAMES
                           or normalise_symbol(name) in _NORETURN_STRIPPED)


@dataclass
class Block:
    va: int
    end_va: int
    insns: list[Insn]
    terminator: str
    confidence: str = "high"        # "low" for prologue-only / gap-sweep code
    file_off: int = -1

    @property
    def last(self) -> Insn:
        return self.insns[-1]

    def to_json(self, block_id: int) -> dict:
        return {
            "id": block_id, "va": self.va, "end_va": self.end_va,
            "file_off": self.file_off, "confidence": self.confidence,
            "insns": [i.to_json() for i in self.insns],
            "terminator": self.terminator,
        }


@dataclass
class Edge:
    src: int          # source block VA (mapped to a block id at serialisation)
    dst: int
    kind: str


@dataclass
class BlockGraph:
    blocks: list[Block] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved: list[tuple[int, str, str]] = field(default_factory=list)
    # (from_va, target_va | None, kind) — direct | plt | indirect | tail
    calls_out: list[tuple[int, int | None, str]] = field(default_factory=list)


# ------------------------------------------------------------ leaders

def find_leaders(body: dict[int, Insn], entry: int, noreturn_at=None,
                 resolved_targets: dict[int, tuple[int, ...]] | None = None,
                 ) -> set[int]:
    """Block leaders: the entry, every branch target inside the body, and
    every instruction following a branch or a noreturn call.

    Jump-table targets count as branch targets. Omitting them lets an edge
    land in the middle of a block, which is a lie about the CFG.
    """
    resolved_targets = resolved_targets or {}
    leaders = {entry}
    for insn in body.values():
        cuts_after = bool(insn.groups & {"jump", "ret"}) or insn.is_invalid \
            or insn.mnemonic in _TRAPS
        if "jump" in insn.groups:
            leaders.update(t for t in insn.targets if t in body)
            leaders.update(t for t in resolved_targets.get(insn.va, ())
                           if t in body)
        if (not cuts_after and "call" in insn.groups
                and noreturn_at is not None
                and any(noreturn_at(t) for t in insn.targets)):
            cuts_after = True
        if cuts_after and insn.end_va in body:
            leaders.add(insn.end_va)
    return leaders


# ------------------------------------------------------------ terminators

def classify_terminator(insn: Insn, mode: str, noreturn_at=None) -> str:
    """Terminator kind for a block ending at `insn`. "fallthrough" means
    the block was cut by a leader, not by control flow stopping."""
    if insn.is_invalid:
        return "invalid"
    if "ret" in insn.groups:
        return "ret"
    if "jump" in insn.groups:
        if insn.is_indirect:
            return "indirect"
        return "jmp" if is_uncond_jump(insn, mode) else "jcc"
    if ("call" in insn.groups and noreturn_at is not None
            and any(noreturn_at(t) for t in insn.targets)):
        return "call_noreturn"
    if insn.mnemonic in _TRAPS:
        return "halt"
    return "fallthrough"


# ------------------------------------------------------------ splitting

def split_blocks(body: dict[int, Insn], entry: int, mode: str, *,
                 confidence: str = "high",
                 noreturn_at=None,
                 resolved_targets: dict[int, tuple[int, ...]] | None = None,
                 is_function_start=None,
                 off_at=None) -> BlockGraph:
    """Cut `body` into basic blocks and classify every outgoing edge.

    `resolved_targets` maps an indirect-jump VA to validated targets from
    the jump-table matcher; anything absent stays explicitly unresolved.
    `is_function_start(va)` marks another function's entry, so a `jmp`
    there is recorded as a tail call rather than absorbed as an edge.
    """
    if not body:
        return BlockGraph()
    resolved_targets = resolved_targets or {}
    leaders = find_leaders(body, entry, noreturn_at, resolved_targets)
    ordered = sorted(body)

    g = BlockGraph()
    current: list[Insn] = []
    for idx, va in enumerate(ordered):
        insn = body[va]
        current.append(insn)
        nxt = ordered[idx + 1] if idx + 1 < len(ordered) else None
        contiguous = nxt == insn.end_va
        term = classify_terminator(insn, mode, noreturn_at)
        flows = term in ("fallthrough", "jcc")
        if flows and contiguous and nxt not in leaders:
            continue
        blk = Block(current[0].va, insn.end_va, current, term,
                    confidence=confidence,
                    file_off=off_at(current[0].va) if off_at else -1)
        g.blocks.append(blk)
        _add_edges(g, blk, insn, body, resolved_targets, is_function_start,
                   may_fall=flows and contiguous)
        current = []

    starts = {b.va for b in g.blocks}
    # an edge into the middle of a block would be a lie about the CFG;
    # keep the unresolved record instead
    for e in g.edges:
        if e.dst not in starts:
            g.unresolved.append((e.src, "edge_into_block_interior",
                                 f"{e.dst:#x}"))
    g.edges = [e for e in g.edges if e.dst in starts]
    return g


def _add_edges(g: BlockGraph, blk: Block, insn: Insn, body: dict[int, Insn],
               resolved: dict[int, tuple[int, ...]], is_function_start,
               *, may_fall: bool) -> None:
    src, term = blk.va, blk.terminator

    if term == "invalid":
        g.unresolved.append((insn.va, "decode_failure", "data in code?"))
        return
    if term in ("ret", "halt"):
        return
    if term == "call_noreturn":
        g.calls_out.extend((insn.va, t, "direct") for t in insn.targets)
        return

    if term == "indirect":
        targets = resolved.get(insn.va, ())
        if targets:
            g.edges.extend(Edge(src, t, "uncond") for t in targets)
        else:
            g.unresolved.append((insn.va, "indirect_jump", "jump_table?"))
        return

    if term in ("jmp", "jcc"):
        for t in insn.targets:
            if t in body:
                g.edges.append(
                    Edge(src, t, "true" if term == "jcc" else "uncond"))
            elif is_function_start is not None and is_function_start(t):
                g.calls_out.append((insn.va, t, "tail"))
            else:
                g.unresolved.append(
                    (insn.va, "target_outside_function", f"{t:#x}"))
        if term == "jcc" and may_fall:
            g.edges.append(Edge(src, insn.end_va, "false"))
        return

    if may_fall:  # fallthrough
        g.edges.append(Edge(src, insn.end_va, "fallthrough"))
