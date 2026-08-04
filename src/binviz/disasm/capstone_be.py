"""Capstone decode backend — the required, in-process decoder.

Two handle configurations per mode (the plan's "two Capstone handles"):
detail=False uses disasm_lite + skipdata for bulk linear sweeps (~2-4x
faster, full byte coverage); detail=True builds CsInsn objects for
branch-target and code-pointer resolution and stops at undecodable
bytes so recursive descent can record the failure explicitly.
"""

from __future__ import annotations

from typing import Iterator

import capstone
from capstone import x86_const

from .backend import (INVALID_GROUPS, INVALID_MNEMONIC, Insn)

_CS_MODES: dict[str, tuple[int, int]] = {
    "x86":    (capstone.CS_ARCH_X86, capstone.CS_MODE_32),
    "x86_64": (capstone.CS_ARCH_X86, capstone.CS_MODE_64),
    "arm":    (capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM),
    "thumb":  (capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB),
    "arm64":  (capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN),
    "mips":   (capstone.CS_ARCH_MIPS,
               capstone.CS_MODE_MIPS32 | capstone.CS_MODE_BIG_ENDIAN),
    "mipsel": (capstone.CS_ARCH_MIPS,
               capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN),
}

_GROUP_NAMES = {
    capstone.CS_GRP_JUMP: "jump",
    capstone.CS_GRP_CALL: "call",
    capstone.CS_GRP_RET: "ret",
    capstone.CS_GRP_PRIVILEGE: "privileged",
    capstone.CS_GRP_BRANCH_RELATIVE: "branch_relative",
}

# interned group sets: millions of Insns must not each own a frozenset
_GROUP_CACHE: dict[frozenset[str], frozenset[str]] = {}

# Non-branch mnemonics that materialise a code address. Deliberately only
# the *address-forming* instructions: RIP-relative `lea` (how startup code
# hands `main` to libc) and ARM64 `adr`. `mov reg, imm` was tried and
# removed — ordinary constants land inside .text often enough on a large
# binary that descent walks into data and manufactures garbage functions.
_PTR_MNEMONICS = {"lea", "adr"}
# immediates below this are plain constants, not addresses
_PTR_MIN = 0x1000


def _interned(names: frozenset[str]) -> frozenset[str]:
    return _GROUP_CACHE.setdefault(names, names)


class CapstoneBackend:
    name = "capstone"

    def __init__(self) -> None:
        self._handles: dict[tuple[str, bool], capstone.Cs] = {}

    def _handle(self, mode: str, detail: bool) -> capstone.Cs:
        cs = self._handles.get((mode, detail))
        if cs is None:
            try:
                arch, cs_mode = _CS_MODES[mode]
            except KeyError:
                raise ValueError(f"unsupported decode mode: {mode!r}") from None
            cs = capstone.Cs(arch, cs_mode)
            cs.detail = detail
            if not detail:
                # bulk path: undecodable bytes become 1-step "(bad)" insns
                # instead of stopping the sweep
                cs.skipdata = True
                cs.skipdata_setup = (INVALID_MNEMONIC, None, None)
            self._handles[(mode, detail)] = cs
        return cs

    def decode(self, va: int, data, mode: str,
               detail: bool = True) -> Iterator[Insn]:
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)  # capstone's ctypes layer wants a real buffer
        cs = self._handle(mode, detail)
        if not detail:
            for addr, size, mnemonic, op_str in cs.disasm_lite(data, va):
                if mnemonic == INVALID_MNEMONIC:
                    yield Insn(addr, size, b"", mnemonic, "", INVALID_GROUPS)
                else:
                    yield Insn(addr, size, b"", mnemonic, op_str)
            return
        for i in cs.disasm(data, va):
            yield self._from_cs(i)

    def cs_handle(self, mode: str) -> capstone.Cs:
        """The detail-enabled handle for a mode — for `reg_name()` and
        friends alongside `decode_ops()`."""
        return self._handle(mode, True)

    def decode_ops(self, va: int, data, mode: str):
        """Yield raw CsInsn objects — the operand-level escape hatch.

        `Insn` deliberately carries no operand structure: millions of them
        exist during a sweep. The jump-table matcher needs registers and
        displacements, and re-decoding its handful of instructions here is
        both exact and cheaper than parsing `op_str` text. Keeping this on
        the backend is what stops a second module from opening its own
        Capstone handles.
        """
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
        return self._handle(mode, True).disasm(data, va)

    def _from_cs(self, i) -> Insn:
        groups = _interned(frozenset(
            _GROUP_NAMES[g] for g in i.groups if g in _GROUP_NAMES))
        targets: tuple[int, ...] = ()
        ptr_imms: tuple[int, ...] = ()
        is_indirect = False
        if "jump" in groups or "call" in groups:
            imms = [op.imm for op in i.operands
                    if op.type == capstone.CS_OP_IMM]
            # the *last* immediate, not every one: ARM64 `tbz w0, #31, label`
            # and `tbnz` put a bit position before the target, and treating
            # that as an address produces edges to VA 0x1f
            targets = (imms[-1],) if imms else ()
            is_indirect = not imms
        elif i.mnemonic in _PTR_MNEMONICS:
            ptr_imms = self._pointer_imms(i)
        return Insn(i.address, i.size, bytes(i.bytes), i.mnemonic, i.op_str,
                    groups, targets, is_indirect, ptr_imms)

    @staticmethod
    def _pointer_imms(i) -> tuple[int, ...]:
        """Code addresses this instruction materialises. Callers still
        filter against executable ranges before trusting them."""
        out = []
        for op in i.operands:
            if (op.type == capstone.CS_OP_MEM and i.mnemonic == "lea"
                    and getattr(op.mem, "base", 0) == x86_const.X86_REG_RIP):
                out.append(i.address + i.size + op.mem.disp)
            elif (op.type == capstone.CS_OP_IMM and i.mnemonic == "adr"
                  and op.imm >= _PTR_MIN):
                out.append(op.imm)
        return tuple(out)


_DEFAULT: CapstoneBackend | None = None


def default_backend() -> CapstoneBackend:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CapstoneBackend()
    return _DEFAULT
