"""One bounded, validated jump-table matcher (x86-64 PIC switch).

Deliberately narrow. Full indirect-target resolution needs value-set
analysis; the honest fallback for everything this does not match is an
`unresolved` record and a "?" edge (blocks.py). What it does match is the
single most common cause of missing CFG edges: the gcc/clang x86-64 PIC
switch dispatch.

    cmp   <idx32>, N              ; optional bound
    ja    <default>               ;   "
    lea   <base>, [rip + disp]    ; table_va
    movsxd <t>, dword ptr [<base> + <idx>*4]
    add   <t>, <base>
    jmp   <t>

Every stage is validated and **any failure abandons the whole table**
rather than emitting some targets — a half-resolved switch is worse than
an honestly unresolved one, because it looks complete.
"""

from __future__ import annotations

import struct

import capstone
from capstone import x86_const as x86

from .backend import Insn

# absolute ceiling when no `cmp` bound is recoverable
MAX_ENTRIES = 256
# instructions to look back from the indirect jump for the dispatch sequence
_LOOKBACK = 24

_BOUND_JCC = {"ja", "jae", "jnbe", "jnb", "jg", "jge", "jnle", "jnl"}


def _canon(cs, reg: int) -> str:
    """Canonical 64-bit name for a GP register, so eax and rax match."""
    name = cs.reg_name(reg) or ""
    if not name:
        return ""
    aliases = {
        "eax": "rax", "ebx": "rbx", "ecx": "rcx", "edx": "rdx",
        "esi": "rsi", "edi": "rdi", "ebp": "rbp", "esp": "rsp",
        "ax": "rax", "bx": "rbx", "cx": "rcx", "dx": "rdx",
        "si": "rsi", "di": "rdi", "al": "rax", "bl": "rbx",
        "cl": "rcx", "dl": "rdx",
    }
    if name in aliases:
        return aliases[name]
    if name.startswith("r") and name[-1] in "dwb" and name[1:-1].isdigit():
        return name[:-1]          # r8d/r8w/r8b -> r8
    return name


def _read_i32(buf, off: int) -> int | None:
    b = bytes(buf[off:off + 4])
    if len(b) != 4:
        return None
    return struct.unpack("<i", b)[0]


def resolve_jump_table(buf, model, backend, insns_before: list[Insn],
                       jmp: Insn, mode: str) -> tuple[tuple[int, ...], str]:
    """Try to resolve one indirect jump. Returns (targets, reason).

    `insns_before` is the address-ordered run of instructions immediately
    preceding `jmp` (the dispatch sequence is contiguous in practice).
    An empty target tuple means unresolved and `reason` says why.
    """
    if mode != "x86_64" or not insns_before:
        return (), "unsupported_mode"

    window = insns_before[-_LOOKBACK:]
    start = window[0].va
    off = model.va_to_off(start)
    if off is None:
        return (), "no_file_backing"
    span = jmp.end_va - start
    ops = list(backend.decode_ops(start, bytes(buf[off:off + span]), mode))
    if not ops or ops[-1].address != jmp.va:
        return (), "redecode_misaligned"

    cs = backend.cs_handle(mode)
    dispatch = _match_dispatch(cs, ops)
    if dispatch is None:
        return (), "pattern_mismatch"
    table_va, index_reg, index_at = dispatch
    aliases = _index_aliases(cs, ops, index_reg, index_at)

    # A `cmp`-derived bound is REQUIRED, not a nicety. Scanning an
    # unbounded table until an entry looks wrong yields a silently
    # truncated switch — measured on hello_O0, the unbounded path produced
    # 90 bad tables and 1248 downstream decode failures from descending
    # into data, while the bounded path produced none. An honestly
    # unresolved indirect jump beats a confidently wrong target list.
    count = _match_bound(cs, ops, aliases)
    if count is None:
        return (), "no_recoverable_bound"

    table_off = model.va_to_off(table_va)
    if table_off is None:
        return (), "table_not_file_backed"

    # every target must land in the same executable region as the jump
    home = model.region_at_va(jmp.va)
    if home is None or "x" not in home.perms:
        return (), "jump_outside_executable_region"
    lo, hi = home.vaddr, home.vaddr + home.file_size

    targets: list[int] = []
    for i in range(count):
        entry = _read_i32(buf, table_off + i * 4)
        if entry is None:
            return (), "table_truncated"
        target = (table_va + entry) & 0xFFFFFFFFFFFFFFFF
        if not (lo <= target < hi):
            # the count came from a `cmp`, so this is a genuinely bad
            # entry: abandon the whole table rather than emit a subset
            return (), "target_outside_region"
        targets.append(target)

    if not targets:
        return (), "no_valid_targets"
    return tuple(dict.fromkeys(targets)), "jump_table"


def _match_dispatch(cs, ops) -> tuple[int, str, int] | None:
    """Match `jmp <t>` / `add <t>,<base>` / `movsxd <t>,[<base>+<idx>*4]`
    / `lea <base>,[rip+disp]`.

    Returns (table_va, index_reg, index_position) where index_position is
    the offset of the `movsxd` in `ops`, the point at which the index
    register is live.
    """
    jmp = ops[-1]
    if jmp.mnemonic != "jmp" or len(jmp.operands) != 1:
        return None
    if jmp.operands[0].type != capstone.CS_OP_REG:
        return None
    target_reg = _canon(cs, jmp.operands[0].reg)

    base_reg = None
    for ins in reversed(ops[:-1]):
        if ins.mnemonic != "add" or len(ins.operands) != 2:
            continue
        dst, src = ins.operands
        if (dst.type == capstone.CS_OP_REG and src.type == capstone.CS_OP_REG
                and _canon(cs, dst.reg) == target_reg):
            base_reg = _canon(cs, src.reg)
            break
    if base_reg is None:
        return None

    index_reg = None
    index_at = -1
    for pos in range(len(ops) - 2, -1, -1):
        ins = ops[pos]
        if ins.mnemonic not in ("movsxd", "movslq") or len(ins.operands) != 2:
            continue
        dst, src = ins.operands
        if (dst.type != capstone.CS_OP_REG
                or _canon(cs, dst.reg) != target_reg
                or src.type != capstone.CS_OP_MEM):
            continue
        mem = src.mem
        if src.size != 4 or mem.scale != 4 or mem.disp != 0:
            return None           # not the 4-byte-relative-entry form
        if _canon(cs, mem.base) != base_reg:
            return None
        index_reg = _canon(cs, mem.index)
        index_at = pos
        break
    if index_reg is None:
        return None

    for ins in reversed(ops[:-1]):
        if ins.mnemonic != "lea" or len(ins.operands) != 2:
            continue
        dst, src = ins.operands
        if (dst.type != capstone.CS_OP_REG
                or _canon(cs, dst.reg) != base_reg
                or src.type != capstone.CS_OP_MEM):
            continue
        if src.mem.base != x86.X86_REG_RIP or src.mem.index != 0:
            return None
        return ins.address + ins.size + src.mem.disp, index_reg, index_at
    return None


def _index_aliases(cs, ops, index_reg: str, index_at: int) -> set[str]:
    """Registers holding the switch index at some point before the load.

    The bound and the load rarely name the same register: gcc emits
    `cmp edi, N` / `ja` / `mov eax, esi` / `movsxd rax, [rcx+rax*4]`, so
    the index travels rdi -> rsi -> rax through plain copies. Walking that
    chain backwards is what lets the `cmp` bound be found at all. A write
    to an alias by anything other than a register copy drops it, so a
    clobbered register can never supply the bound.
    """
    aliases = {index_reg}
    for ins in reversed(ops[:max(index_at, 0)]):
        if len(ins.operands) != 2:
            continue
        dst, src = ins.operands
        if dst.type != capstone.CS_OP_REG:
            continue
        name = _canon(cs, dst.reg)
        if name not in aliases:
            continue
        if ins.mnemonic in ("mov", "movsxd", "movslq", "movzx", "movsx") \
                and src.type == capstone.CS_OP_REG:
            aliases.add(_canon(cs, src.reg))
        else:
            aliases.discard(name)   # clobbered: no longer the index
    return aliases


def _match_bound(cs, ops, aliases: set[str]) -> int | None:
    """Entry count from a `cmp <idx>, N` guarded by an unsigned-above jcc."""
    for i, ins in enumerate(ops):
        if ins.mnemonic != "cmp" or len(ins.operands) != 2:
            continue
        dst, src = ins.operands
        if (dst.type != capstone.CS_OP_REG or src.type != capstone.CS_OP_IMM
                or _canon(cs, dst.reg) not in aliases):
            continue
        nxt = ops[i + 1] if i + 1 < len(ops) else None
        if nxt is None or nxt.mnemonic not in _BOUND_JCC:
            continue
        n = src.imm
        if 0 <= n < MAX_ENTRIES:
            return n + 1          # `ja N` allows indices 0..N
    return None
