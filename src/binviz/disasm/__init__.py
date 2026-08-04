"""Instruction decoding core (Phase 4).

Only this package touches Capstone. `backend` defines the decoder-facing
types, `capstone_be` the required backend, `sweep` the two decode
strategies. Function discovery and CFG assembly build on these in
Phase 5 (`blocks`, `recover`); an optional radare2 oracle backend
(`r2_be`) also lands there.
"""

from .backend import (DisassemblyBackend, Insn, INVALID_MNEMONIC,
                      mode_at, mode_for_model)
from .blocks import Block, BlockGraph, Edge, is_noreturn, split_blocks
from .capstone_be import CapstoneBackend, default_backend
from .jumptable import resolve_jump_table
from .recover import (CONFIDENCE, DISCOVERY, Function, Program, looks_packed,
                      recover)
from .sweep import falls_through, linear_sweep, recursive_descent

__all__ = [
    "DisassemblyBackend", "Insn", "INVALID_MNEMONIC",
    "mode_at", "mode_for_model",
    "CapstoneBackend", "default_backend",
    "linear_sweep", "recursive_descent", "falls_through",
    "Block", "BlockGraph", "Edge", "split_blocks", "is_noreturn",
    "resolve_jump_table",
    "recover", "Program", "Function", "DISCOVERY", "CONFIDENCE",
    "looks_packed",
]
