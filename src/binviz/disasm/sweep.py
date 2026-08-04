"""Linear sweep and recursive descent over a BinaryModel.

Recursive descent is primary: x86 self-synchronises within a few bytes of
a wrong start offset, so a linear sweep's output *looks* plausible even
when it is garbage — its blocks are low-confidence gap filler (Phase 5).

Descent follows direct branch/call targets and, because startup code
hands `main` to libc as a *pointer* rather than a call (`lea rdi,
[rip+main]` / `adr x0, main`), also queues address-sized immediates that
land in executable regions. Those pointer seeds are reported separately
in `info` so Phase 5 can grade their provenance below true call targets.
"""

from __future__ import annotations

from bisect import bisect_right

from .backend import Insn, mode_at, mode_for_model
from .capstone_be import default_backend

# decode window: bytes copied out of the mmap per decode run; margin covers
# the longest x86 instruction so a window edge is never mistaken for an
# undecodable byte
_WINDOW = 4096
_MAX_INSN = 16

# mnemonics that end fall-through without being ret/uncond-jump groups
_STOP_MNEMONICS = {"hlt", "ud2", "int3", "brk", "udf"}

# unconditional jump mnemonics per mode family (conditional ARM branches
# carry the condition in the mnemonic: "beq", "b.eq", "bne", ...)
_UNCOND_JUMPS = {
    "x86": {"jmp", "ljmp"},
    "x86_64": {"jmp", "ljmp"},
    "arm": {"b", "b.w", "bx", "bxj"},
    "thumb": {"b", "b.w", "bx", "bxj"},
    "arm64": {"b", "br"},
    "mips": {"j", "jr", "b"},
    "mipsel": {"j", "jr", "b"},
}


def linear_sweep(data, va: int, mode: str, backend=None,
                 detail: bool = False) -> dict[int, Insn]:
    """Decode a buffer sequentially; every byte is covered, undecodable
    bytes appear as explicit invalid instructions. Never raises, never
    loops: the cursor advances by at least one unit per instruction."""
    backend = backend or default_backend()
    return {i.va: i for i in backend.decode(va, data, mode, detail=detail)}


# ------------------------------------------------------------ exec ranges

class _ExecRanges:
    """Sorted (va0, va1, file_off) intervals of file-backed executable
    bytes, with windowed byte access."""

    def __init__(self, model, buf) -> None:
        self.buf = buf
        ranges = [
            (r.vaddr, r.vaddr + r.file_size, r.file_off)
            for r in model.regions
            if "x" in r.perms and r.file_off >= 0 and r.file_size > 0
            and r.vaddr >= 0
        ]
        self.from_perms = bool(ranges)
        if not ranges:
            # raw/headerless models carry no permissions; treat every
            # mapped byte as potentially code rather than doing nothing
            ranges = [(va, va + size, fo) for fo, size, va in model.mappings]
        self.ranges = sorted(ranges)
        self._starts = [r[0] for r in self.ranges]

    def find(self, va: int) -> tuple[int, int, int] | None:
        i = bisect_right(self._starts, va) - 1
        if i >= 0:
            va0, va1, fo = self.ranges[i]
            if va < va1:
                return self.ranges[i]
        return None

    def window(self, va: int) -> tuple[bytes, int] | None:
        """(bytes starting at va, bytes remaining in the range) or None."""
        r = self.find(va)
        if r is None:
            return None
        va0, va1, fo = r
        off = fo + (va - va0)
        remaining = va1 - va
        return bytes(self.buf[off:off + min(remaining, _WINDOW)]), remaining


# ------------------------------------------------------------ descent

def is_uncond_jump(insn: Insn, mode: str) -> bool:
    """True for unconditional jumps. Conditional ARM/MIPS branches carry
    the condition in the mnemonic ("beq", "b.eq"), so this is a set test,
    not a group test."""
    return ("jump" in insn.groups
            and insn.mnemonic in _UNCOND_JUMPS.get(mode, ()))


def falls_through(insn: Insn, mode: str) -> bool:
    """Whether control can reach the next address in sequence. Ignores
    noreturn calls — the callee name is not known at this level; Phase 5's
    block splitter layers that on."""
    if insn.is_invalid or "ret" in insn.groups:
        return False
    if insn.mnemonic in _STOP_MNEMONICS:
        return False
    return not is_uncond_jump(insn, mode)




def recursive_descent(buf, model, seeds, backend=None, *,
                      max_insns: int = 1_000_000,
                      harvest_pointers: bool = True,
                      info: dict | None = None) -> dict[int, Insn]:
    """Decode reachable instructions from seed VAs. Returns {va: Insn}.

    Never follows a target outside executable regions; never re-decodes a
    visited address; hard-capped at `max_insns` so pathological input
    terminates. If `info` is given it is filled with:
      pointer_seeds   VAs queued from harvested code-pointer immediates
      indirect_jumps  VAs of unresolved indirect jumps (switch dispatch &c)
      decode_errors   VAs where decoding hit an undecodable byte
      mode_retries    (va, from_mode, to_mode) ARM/Thumb retry events
      truncated       True if the max_insns cap stopped the walk
    """
    backend = backend or default_backend()
    if info is None:
        info = {}
    info.update(pointer_seeds=[], indirect_jumps=[], decode_errors=[],
                mode_retries=[], truncated=False)

    exec_ranges = _ExecRanges(model, buf)
    default_mode = mode_for_model(model)
    if default_mode is None:
        raise ValueError(
            f"no decoder for arch {model.arch!r}; pass --arch for raw input")

    is_arm = model.arch == "arm"
    insns: dict[int, Insn] = {}
    work: list[tuple[int, str]] = []

    def queue(va: int, mode_hint: str | None = None) -> None:
        if is_arm and va & 1:  # Thumb bit on function pointers
            va, mode_hint = va & ~1, "thumb"
        if va in insns or exec_ranges.find(va) is None:
            return
        mode = mode_hint or mode_at(model, va) or default_mode
        work.append((va, mode))

    def on_insn(insn: Insn, mode: str) -> None:
        """Queue everything a recorded instruction points at."""
        for t in insn.targets:
            hint = None
            if is_arm and insn.mnemonic.startswith("blx"):
                hint = "thumb" if mode == "arm" else "arm"
            queue(t, hint)
        if harvest_pointers:
            for p in insn.ptr_imms:
                if exec_ranges.find(p & ~1 if is_arm else p) is not None:
                    info["pointer_seeds"].append(p)
                    queue(p)
        if insn.is_indirect and "jump" in insn.groups:
            info["indirect_jumps"].append(insn.va)

    def walk(va: int, mode: str) -> None:
        """Decode one straight-line run, following only fall-through."""
        retried = False
        recorded_any = False
        while va not in insns:
            if len(insns) >= max_insns:
                info["truncated"] = True
                return
            w = exec_ranges.window(va)
            if w is None:
                return
            window, remaining = w
            window_start = va
            stopped_flow = False
            merged = False
            straddled = False
            for insn in backend.decode(va, window, mode, detail=True):
                if insn.va in insns:
                    merged = True
                    break
                # never trust an instruction straddling the window edge
                # unless the window already reaches the end of the range
                if (insn.end_va - window_start > len(window) - _MAX_INSN
                        and remaining > len(window)):
                    straddled = True
                    break
                insns[insn.va] = insn
                recorded_any = True
                on_insn(insn, mode)
                va = insn.end_va
                if not falls_through(insn, mode):
                    stopped_flow = True
                    break
                if len(insns) >= max_insns:
                    info["truncated"] = True
                    return
            if merged or stopped_flow:
                return
            consumed = va - window_start
            if straddled or (consumed >= len(window) - _MAX_INSN
                             and remaining > len(window)):
                continue  # clean window edge: refetch at the current va
            if consumed == len(window):
                return  # decoded exactly to the end of the range
            # the decoder stopped short inside the buffer: undecodable byte
            if not recorded_any and mode in ("arm", "thumb") and not retried:
                other = "thumb" if mode == "arm" else "arm"
                info["mode_retries"].append((va, mode, other))
                mode, retried = other, True
                continue
            info["decode_errors"].append(va)
            insns[va] = Insn(va, 1, window[consumed:consumed + 1],
                             "(bad)", "", frozenset({"invalid"}))
            return

    for s in seeds:
        queue(s)
    while work:
        va, mode = work.pop()
        if va not in insns:
            walk(va, mode)
        if info["truncated"]:
            break
    return insns
