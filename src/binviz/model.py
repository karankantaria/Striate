"""The address-space model: Region, Symbol, BinaryModel, off<->va.

One authoritative mapping between file offsets and virtual addresses.
Everything downstream (entropy strips, disassembly, cross-view links)
converts through exactly these two functions; get them wrong and every
view is silently a few KB off.

The mapping is a sorted, non-overlapping interval table (`mappings`),
built and sanitised by parse.py from load segments / PE sections — not
from the display `regions`, which may be absent (sectionless binaries)
or lie (malformed ones). It is also the compact table the frontend uses
for client-side conversion (Phase 6).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Region:
    name: str          # ".text", "PT_LOAD[1]", "<overlay>", "<gap>"
    kind: str          # section | segment | header | overlay | gap
    file_off: int      # -1 if not file-backed (e.g. .bss)
    file_size: int
    vaddr: int         # -1 if not mapped
    vsize: int
    perms: str         # subset of "rwx"
    entropy: float | None = None   # filled by Phase 2

    def to_json(self) -> dict:
        return {
            "name": self.name, "kind": self.kind,
            "file_off": self.file_off, "file_size": self.file_size,
            "vaddr": self.vaddr, "vsize": self.vsize,
            "perms": self.perms, "entropy": self.entropy,
        }


@dataclass(frozen=True)
class Symbol:
    name: str
    va: int
    size: int
    kind: str          # func | object | import | export | unknown
    source: str        # symtab | dynsym | export_table | none

    def to_json(self) -> dict:
        return {"name": self.name, "va": self.va, "size": self.size,
                "kind": self.kind, "source": self.source}


@dataclass
class BinaryModel:
    path: str
    sha256: str
    size: int
    format: str        # elf | pe | macho | raw
    arch: str          # x86 | x86_64 | arm | arm64 | mips | unknown
    bits: int
    endian: str        # little | big
    entry_va: int | None
    regions: list[Region]                    # sorted by file_off, gaps materialised
    symbols: list[Symbol]
    imports: list[str]                       # "libc.so.6!memcpy" / "KERNEL32.dll!VirtualAlloc"
    exports: list[str]
    arch_ranges: list[tuple[int, int, str]]  # (va0, va1, "arm"|"thumb"|"data")
    warnings: list[str]
    # sanitised (file_off, size, vaddr) intervals; non-overlapping on both axes
    mappings: list[tuple[int, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_off = sorted(self.mappings)
        self._off_starts = [m[0] for m in self._by_off]
        self._by_va = sorted(self.mappings, key=lambda m: m[2])
        self._va_starts = [m[2] for m in self._by_va]
        backed = sorted(
            (r for r in self.regions if r.file_off >= 0),
            key=lambda r: r.file_off,
        )
        self._regions_by_off = backed
        self._region_starts = [r.file_off for r in backed]

    def off_to_va(self, off: int) -> int | None:
        i = bisect_right(self._off_starts, off) - 1
        if i >= 0:
            fo, size, va = self._by_off[i]
            if off < fo + size:
                return va + (off - fo)
        return None

    def va_to_off(self, va: int) -> int | None:
        i = bisect_right(self._va_starts, va) - 1
        if i >= 0:
            fo, size, v0 = self._by_va[i]
            if va < v0 + size:
                return fo + (va - v0)
        return None

    def region_at_off(self, off: int) -> Region | None:
        i = bisect_right(self._region_starts, off) - 1
        if i >= 0:
            r = self._regions_by_off[i]
            if off < r.file_off + max(r.file_size, 0):
                return r
        return None

    def region_at_va(self, va: int) -> Region | None:
        off = self.va_to_off(va)
        return self.region_at_off(off) if off is not None else None

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "format": self.format,
            "arch": self.arch,
            "bits": self.bits,
            "endian": self.endian,
            "entry_va": self.entry_va,
            "regions": [r.to_json() for r in self.regions],
            "symbols": [s.to_json() for s in self.symbols],
            "imports": self.imports,
            "exports": self.exports,
            "arch_ranges": [list(t) for t in self.arch_ranges],
            "warnings": self.warnings,
            "mappings": [list(m) for m in self.mappings],
        }
