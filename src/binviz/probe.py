"""Header-magic format sniffing. No container parsing — that is Phase 1's job.

`guess_format` answers "what does the first handful of bytes claim this is",
nothing more. It must never raise on any input, including empty files.
"""

from __future__ import annotations

import os
import struct

from .loader import read_head, sha256_file

# Mach-O magics, both widths and endiannesses, plus fat headers.
_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",  # MH_MAGIC (32, be-stored)
    b"\xce\xfa\xed\xfe",  # MH_CIGAM
    b"\xfe\xed\xfa\xcf",  # MH_MAGIC_64
    b"\xcf\xfa\xed\xfe",  # MH_CIGAM_64
    b"\xca\xfe\xba\xbe",  # FAT_MAGIC
    b"\xbe\xba\xfe\xca",  # FAT_CIGAM
}


def guess_format(head: bytes, path: str | None = None) -> str:
    """Best-effort format guess from the first bytes of the file.

    `path` is only used to read the PE header at e_lfanew when the file
    starts with an MZ stub; pass None to classify from `head` alone.
    """
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head[:4] in _MACHO_MAGICS:
        return "macho"
    if head.startswith(b"MZ"):
        return _classify_mz(head, path)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    return "raw"


def _classify_mz(head: bytes, path: str | None) -> str:
    """MZ stub: 'pe' if a valid PE\\0\\0 signature sits at e_lfanew, else 'dos'."""
    if len(head) < 0x40:
        return "dos"
    e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
    sig = b""
    if e_lfanew + 4 <= len(head):
        sig = head[e_lfanew : e_lfanew + 4]
    elif path is not None:
        try:
            with open(path, "rb") as f:
                f.seek(e_lfanew)
                sig = f.read(4)
        except OSError:
            sig = b""
    return "pe" if sig == b"PE\x00\x00" else "dos"


def probe(path: str | os.PathLike) -> dict:
    """`binviz probe` payload: identity + magic + format guess."""
    path = os.fspath(path)
    head = read_head(path, 64)
    return {
        "path": path,
        "size": os.path.getsize(path),
        "sha256": sha256_file(path),
        "magic": head[:8].hex(),
        "guessed_format": guess_format(head, path),
    }
