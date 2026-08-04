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

# non-branch mnemonics whose immediates commonly materialise code pointers
_PTR_MNEMONICS = {"lea", "mov", "movabs", "adr"}
# immediates below this are treated as plain constants, not addresses
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

    def _from_cs(self, i) -> Insn:
        groups = _interned(frozenset(
            _GROUP_NAMES[g] for g in i.groups if g in _GROUP_NAMES))
        targets: tuple[int, ...] = ()
        ptr_imms: tuple[int, ...] = ()
        is_indirect = False
        if "jump" in groups or "call" in groups:
            imms = tuple(op.imm for op in i.operands
                         if op.type == capstone.CS_OP_IMM)
            targets = imms
            is_indirect = not imms
        elif i.mnemonic in _PTR_MNEMONICS:
            ptr_imms = self._pointer_imms(i)
        return Insn(i.address, i.size, bytes(i.bytes), i.mnemonic, i.op_str,
                    groups, targets, is_indirect, ptr_imms)

    @staticmethod
    def _pointer_imms(i) -> tuple[int, ...]:
        """Address-sized immediates this insn materialises (RIP-relative lea,
        mov imm, arm64 adr). Callers filter against executable ranges."""
        out = []
        for op in i.operands:
            if op.type == capstone.CS_OP_IMM and op.imm >= _PTR_MIN:
                out.append(op.imm)
            elif (op.type == capstone.CS_OP_MEM
                  and i.mnemonic == "lea"
                  and getattr(op.mem, "base", 0) == x86_const.X86_REG_RIP):
                out.append(i.address + i.size + op.mem.disp)
        return tuple(out)


_DEFAULT: CapstoneBackend | None = None


def default_backend() -> CapstoneBackend:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CapstoneBackend()
    return _DEFAULT
