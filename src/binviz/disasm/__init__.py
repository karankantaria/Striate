"""Instruction decoding core (Phase 4).

Only this package touches Capstone. `backend` defines the decoder-facing
types, `capstone_be` the required backend, `sweep` the two decode
strategies. Function discovery and CFG assembly build on these in
Phase 5 (`blocks`, `recover`); an optional radare2 oracle backend
(`r2_be`) also lands there.
"""

from .backend import (DisassemblyBackend, Insn, INVALID_MNEMONIC,
                      mode_at, mode_for_model)
from .capstone_be import CapstoneBackend, default_backend
from .sweep import linear_sweep, recursive_descent

__all__ = [
    "DisassemblyBackend", "Insn", "INVALID_MNEMONIC",
    "mode_at", "mode_for_model",
    "CapstoneBackend", "default_backend",
    "linear_sweep", "recursive_descent",
]
