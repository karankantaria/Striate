"""Decoder-facing types: Insn, the DisassemblyBackend protocol, mode selection.

A backend is a *decoder*, not an analyser: it turns bytes at a VA into a
stream of instructions and resolves direct branch targets. It has no
opinion about functions or block boundaries — that is Phase 5, built on
top of this, where uncertainty can be represented instead of discarded.

Decode modes are plain strings ("x86_64", "thumb", ...) rather than
backend constants so a second backend (radare2 oracle) can share them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol

# every mode a backend may be asked for; capstone_be maps them to consts
MODES = ("x86", "x86_64", "arm", "thumb", "arm64", "mips", "mipsel")

# group tags carried on Insn (subset used per instruction)
GROUP_TAGS = ("jump", "call", "ret", "branch_relative", "privileged", "invalid")

INVALID_MNEMONIC = "(bad)"

_EMPTY: frozenset[str] = frozenset()
INVALID_GROUPS: frozenset[str] = frozenset({"invalid"})


@dataclass(frozen=True, slots=True)
class Insn:
    va: int
    size: int
    bytes_: bytes              # b"" on the bulk (detail=False) path
    mnemonic: str
    op_str: str
    groups: frozenset[str] = _EMPTY     # subset of GROUP_TAGS
    targets: tuple[int, ...] = ()       # resolved direct branch/call target VAs
    is_indirect: bool = False           # branch/call through register or memory
    # code-pointer immediates materialised by non-branch insns (lea rip-rel,
    # mov imm): candidate function seeds for recursive descent / Phase 5
    ptr_imms: tuple[int, ...] = ()

    @property
    def end_va(self) -> int:
        return self.va + self.size

    @property
    def is_invalid(self) -> bool:
        return "invalid" in self.groups

    def to_json(self) -> dict:
        return {"va": self.va, "size": self.size, "bytes": self.bytes_.hex(),
                "mnemonic": self.mnemonic, "op": self.op_str}


class DisassemblyBackend(Protocol):
    name: str

    def decode(self, va: int, data, mode: str,
               detail: bool = True) -> Iterator[Insn]:
        """Decode instructions starting at `va` until the buffer ends.

        detail=True resolves groups/targets/ptr_imms and stops at the first
        undecodable byte. detail=False is the bulk path: no groups or
        targets, undecodable bytes come back as 1-step INVALID_MNEMONIC
        instructions, and the whole buffer is always covered.
        """
        ...


# ------------------------------------------------------------ mode selection

def mode_for_model(model) -> str | None:
    """Default decode mode for a BinaryModel; None if undecodable arch."""
    if model.arch == "x86":
        return "x86"
    if model.arch == "x86_64":
        return "x86_64"
    if model.arch == "arm64":
        return "arm64"
    if model.arch == "arm":
        return "arm"
    if model.arch == "mips":
        return "mips" if model.endian == "big" else "mipsel"
    return None


def mode_at(model, va: int) -> str | None:
    """Decode mode at a VA, honouring ARM mapping-symbol ranges.

    Returns None for `$d` (data) ranges — those bytes are never swept —
    and for models whose arch has no decoder.
    """
    default = mode_for_model(model)
    if model.arch != "arm" or not model.arch_ranges:
        return default
    for va0, va1, kind in model.arch_ranges:
        if va0 <= va < va1:
            if kind == "data":
                return None
            return "thumb" if kind == "thumb" else "arm"
    return default
