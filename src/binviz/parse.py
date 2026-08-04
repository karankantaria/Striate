"""Container parsing: file bytes -> BinaryModel.

Every LIEF call in the project lives in this module. LIEF's job is
structure (sections, segments, symbols, imports); identity facts
(format, arch, bits, endian, entry) are read straight from the raw
headers so a LIEF quirk can never misreport them. Any parse failure
degrades to a raw single-region model — malformed and packed binaries
are the interesting ones, so this module must never hard-fail.
"""

from __future__ import annotations

import os
import struct

import lief

from .loader import read_head, sha256_file
from .model import BinaryModel, Region, Symbol
from .probe import guess_format

lief.logging.disable()

_ELF_MACHINES = {0x03: "x86", 0x3E: "x86_64", 0x28: "arm", 0xB7: "arm64", 0x08: "mips"}
_PE_MACHINES = {0x14C: ("x86", 32), 0x8664: ("x86_64", 64),
                0x1C0: ("arm", 32), 0xAA64: ("arm64", 64)}
_MACHO_CPUTYPES = {7: ("x86", 32), 0x01000007: ("x86_64", 64),
                   12: ("arm", 32), 0x0100000C: ("arm64", 64)}

_PE_SCN_X, _PE_SCN_R, _PE_SCN_W = 0x20000000, 0x40000000, 0x80000000

# vsize this many times raw size (and materially larger) smells of unpacking room
_VSIZE_RATIO, _VSIZE_SLACK = 4, 0x4000

_MAPPING_SYM_KIND = {"$a": "arm", "$t": "thumb", "$d": "data"}


def _int(x) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return int(getattr(x, "value", 0))


# ------------------------------------------------------------ header facts

def _elf_identity(head: bytes) -> tuple[str, int, str, int | None]:
    bits = 64 if head[4] == 2 else 32
    endian = "big" if head[5] == 2 else "little"
    fmt = "<" if endian == "little" else ">"
    machine = struct.unpack_from(fmt + "H", head, 18)[0]
    entry = struct.unpack_from(fmt + ("Q" if bits == 64 else "I"), head, 24)[0]
    return _ELF_MACHINES.get(machine, "unknown"), bits, endian, entry or None


def _pe_identity(path: str, head: bytes) -> tuple[str, int, str]:
    e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
    with open(path, "rb") as f:
        f.seek(e_lfanew + 4)
        machine = struct.unpack("<H", f.read(2))[0]
    arch, bits = _PE_MACHINES.get(machine, ("unknown", 0))
    return arch, bits, "little"


def _macho_identity(head: bytes) -> tuple[str, int, str]:
    magic = head[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        return "unknown", 0, "big"  # fat container; slices carry the real arch
    little = magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")
    fmt = "<I" if little else ">I"
    cputype = struct.unpack_from(fmt, head, 4)[0]
    arch, bits = _MACHO_CPUTYPES.get(cputype, ("unknown", 0))
    return arch, bits, "little" if little else "big"


# ------------------------------------------------------- interval hygiene

def _sanitise_mappings(
    cands: list[tuple[int, int, int]], file_size: int, warnings: list[str]
) -> list[tuple[int, int, int]]:
    """Clamp to EOF and trim overlaps on both axes so bisect stays honest.

    Earlier (lower-file-offset) mappings win; packed binaries genuinely map
    the same file bytes at several VAs, and the trim is reported, not hidden.
    """
    clamped: list[tuple[int, int, int]] = []
    n_trunc = 0
    for fo, size, va in sorted(cands):
        if fo < 0 or size <= 0 or va < 0 or fo >= file_size:
            if fo >= file_size and size > 0:
                n_trunc += 1
            continue
        if fo + size > file_size:
            n_trunc += 1
            size = file_size - fo
        clamped.append((fo, size, va))
    if n_trunc:
        warnings.append(
            f"{n_trunc} mapping(s) extend past EOF (file truncated?); clamped"
        )

    out: list[tuple[int, int, int]] = []
    end = 0
    n_overlap = 0
    for fo, size, va in clamped:
        if fo < end:  # overlaps previous on the file axis
            n_overlap += 1
            delta = end - fo
            fo, size, va = fo + delta, size - delta, va + delta
            if size <= 0:
                continue
        out.append((fo, size, va))
        end = fo + size

    by_va: list[tuple[int, int, int]] = []
    va_end = -1
    for fo, size, va in sorted(out, key=lambda m: m[2]):
        if va < va_end:
            n_overlap += 1
            delta = va_end - va
            fo, size, va = fo + delta, size - delta, va + delta
            if size <= 0:
                continue
        by_va.append((fo, size, va))
        va_end = va + size
    if n_overlap:
        warnings.append(
            f"{n_overlap} overlapping address mapping(s) trimmed "
            "(same bytes mapped more than once; common in packed binaries)"
        )
    return sorted(by_va)


def _materialise(
    backed: list[Region], other: list[Region], file_size: int, warnings: list[str]
) -> list[Region]:
    """Partition [0, file_size) into regions; gaps and overlay made explicit."""
    backed = sorted(
        (r for r in backed if r.file_off >= 0 and r.file_size > 0),
        key=lambda r: r.file_off,
    )
    out: list[Region] = []
    pos = 0
    n_clamped = 0
    for r in backed:
        if r.file_off >= file_size:
            n_clamped += 1
            continue
        if r.file_off + r.file_size > file_size:
            n_clamped += 1
            r = Region(r.name, r.kind, r.file_off, file_size - r.file_off,
                       r.vaddr, r.vsize, r.perms)
        if r.file_off > pos:
            kind = "header" if pos == 0 else "gap"
            name = "<header>" if pos == 0 else "<gap>"
            out.append(Region(name, kind, pos, r.file_off - pos, -1, 0, ""))
        if r.file_off < pos:  # overlapping display regions: trim the later one
            delta = pos - r.file_off
            if r.file_size <= delta:
                continue
            r = Region(r.name, r.kind, pos, r.file_size - delta,
                       r.vaddr + delta if r.vaddr >= 0 else -1, r.vsize, r.perms)
        out.append(r)
        pos = r.file_off + r.file_size
    if n_clamped:
        warnings.append(
            f"{n_clamped} region(s) extend past EOF (file truncated?); clamped"
        )
    if pos < file_size:
        out.append(Region("<overlay>", "overlay", pos, file_size - pos, -1, 0, ""))
        warnings.append(
            f"overlay: {file_size - pos} bytes past the last mapped region"
        )
    return out + sorted(other, key=lambda r: r.vaddr)


# ------------------------------------------------------------- ELF

def _elf_perms_from_section(sec) -> str:
    f = _int(sec.flags)
    perms = ""
    if f & 0x2:      # SHF_ALLOC
        perms += "r"
    if f & 0x1:      # SHF_WRITE
        perms += "w"
    if f & 0x4:      # SHF_EXECINSTR
        perms += "x"
    return perms


def _seg_perms(raw_flags: int) -> str:
    return (("r" if raw_flags & 4 else "")
            + ("w" if raw_flags & 2 else "")
            + ("x" if raw_flags & 1 else ""))


def _from_elf(b, path: str, head: bytes, size: int, warnings: list[str]):
    arch, bits, endian, entry = _elf_identity(head)

    loads = [s for s in b.segments if str(s.type).endswith("LOAD")]
    mappings = [(s.file_offset, s.physical_size, s.virtual_address)
                for s in loads if s.physical_size > 0]

    backed: list[Region] = []
    other: list[Region] = []
    sections = [s for s in b.sections if s.name or s.size]
    if sections:
        for s in sections:
            perms = _elf_perms_from_section(s)
            alloc = "r" in perms
            if str(s.type).endswith("NOBITS"):
                other.append(Region(s.name, "section", -1, 0,
                                    s.virtual_address, s.size, perms))
            else:
                backed.append(Region(
                    s.name, "section", s.offset, s.size,
                    s.virtual_address if alloc else -1,
                    s.size if alloc else 0, perms))
    else:
        warnings.append("no section headers (stripped or packed)")
        for i, s in enumerate(loads):
            backed.append(Region(
                f"PT_LOAD[{i}]", "segment", s.file_offset, s.physical_size,
                s.virtual_address, s.virtual_size, _seg_perms(s.raw_flags)))

    symbols: list[Symbol] = []
    mapping_syms: list[tuple[int, str]] = []
    for source, it in (("symtab", getattr(b, "symtab_symbols", [])),
                       ("dynsym", getattr(b, "dynamic_symbols", []))):
        for s in it:
            name = s.name
            if not name:
                continue
            base = name.split(".")[0]
            if base in _MAPPING_SYM_KIND:
                mapping_syms.append((s.value, _MAPPING_SYM_KIND[base]))
                continue
            if s.value == 0:
                continue
            kind = ("func" if s.is_function
                    else "object" if s.is_variable else "unknown")
            symbols.append(Symbol(name, s.value, s.size, kind, source))

    libs = list(b.libraries)
    prefix = f"{libs[0]}!" if len(libs) == 1 else ""
    imports = sorted({f"{prefix}{f.name}" for f in b.imported_functions if f.name})
    exports = sorted({f.name for f in b.exported_functions if f.name})

    arch_ranges: list[tuple[int, int, str]] = []
    if arch == "arm" and mapping_syms:
        mapping_syms.sort()
        exec_end = max(
            (s.virtual_address + s.virtual_size for s in loads
             if s.raw_flags & 1), default=0)
        for i, (va, kind) in enumerate(mapping_syms):
            end = (mapping_syms[i + 1][0] if i + 1 < len(mapping_syms)
                   else max(exec_end, va))
            if end > va:
                arch_ranges.append((va, end, kind))

    has_dynamic = any(str(s.type).endswith("DYNAMIC") for s in b.segments)
    return (arch, bits, endian, entry, backed, other, mappings,
            symbols, imports, exports, arch_ranges, has_dynamic)


# ------------------------------------------------------------- PE

def _from_pe(b, path: str, head: bytes, size: int, warnings: list[str]):
    arch, bits, endian = _pe_identity(path, head)
    oh = b.optional_header
    base = oh.imagebase
    entry = base + oh.addressof_entrypoint

    backed = [Region("<pe_headers>", "header", 0, oh.sizeof_headers,
                     base, oh.sizeof_headers, "r")]
    mappings = [(0, oh.sizeof_headers, base)]
    for s in b.sections:
        c = _int(s.characteristics)
        perms = (("r" if c & _PE_SCN_R else "")
                 + ("w" if c & _PE_SCN_W else "")
                 + ("x" if c & _PE_SCN_X else ""))
        va = base + s.virtual_address
        if s.sizeof_raw_data > 0:
            backed.append(Region(s.name, "section", s.pointerto_raw_data,
                                 s.sizeof_raw_data, va, s.virtual_size, perms))
            mappings.append((s.pointerto_raw_data, s.sizeof_raw_data, va))
        else:
            backed.append(Region(s.name, "section", -1, 0, va,
                                 s.virtual_size, perms))

    imports = sorted({
        f"{imp.name}!{e.name or '#' + str(e.ordinal)}"
        for imp in b.imports for e in imp.entries
    })
    exports = sorted({f.name for f in b.exported_functions if f.name})
    symbols = [Symbol(f.name, base + f.address, 0, "export", "export_table")
               for f in b.exported_functions if f.name]

    return (arch, bits, endian, entry, backed, [], mappings,
            symbols, imports, exports, [], True)


# ------------------------------------------------------------- Mach-O

def _from_macho(b, path: str, head: bytes, size: int, warnings: list[str]):
    if hasattr(b, "at"):  # fat binary: model the first slice, say so
        warnings.append(f"fat Mach-O: modelling slice 0 of {b.size}")
        b = b.at(0)
    arch, bits, endian = _macho_identity(read_head(path, 8) if hasattr(b, "header") else head)
    if arch == "unknown":
        cputype = _int(b.header.cpu_type)
        arch, bits = _MACHO_CPUTYPES.get(cputype, ("unknown", 64))

    backed: list[Region] = []
    other: list[Region] = []
    mappings: list[tuple[int, int, int]] = []
    for seg in b.segments:
        # vm_prot bit order differs from ELF phdr flags: r=1, w=2, x=4
        perms = (("r" if _int(seg.init_protection) & 1 else "")
                 + ("w" if _int(seg.init_protection) & 2 else "")
                 + ("x" if _int(seg.init_protection) & 4 else ""))
        if seg.file_size > 0:
            backed.append(Region(seg.name, "segment", seg.file_offset,
                                 seg.file_size, seg.virtual_address,
                                 seg.virtual_size, perms))
            mappings.append((seg.file_offset, seg.file_size, seg.virtual_address))
        elif seg.virtual_size > 0:
            other.append(Region(seg.name, "segment", -1, 0,
                                seg.virtual_address, seg.virtual_size, perms))

    symbols = [Symbol(s.name, s.value, 0, "unknown", "symtab")
               for s in getattr(b, "symbols", []) if s.name and s.value]
    imports = sorted({f.name for f in b.imported_functions if f.name})
    exports = sorted({f.name for f in b.exported_functions if f.name})
    entry = getattr(b, "entrypoint", None) or None
    return (arch, bits, endian, entry, backed, other, mappings,
            symbols, imports, exports, [], True)


# ------------------------------------------------------------- assembly

def _raw_model(path: str, size: int, sha: str, arch: str | None,
               warnings: list[str]) -> BinaryModel:
    region = Region("<raw>", "segment", 0, size, 0, size, "")
    return BinaryModel(
        path=path, sha256=sha, size=size, format="raw",
        arch=arch or "unknown", bits=0, endian="little", entry_va=None,
        regions=[region] if size else [], symbols=[], imports=[], exports=[],
        arch_ranges=[], warnings=warnings,
        mappings=[(0, size, 0)] if size else [],
    )


def _post_checks(m: BinaryModel, has_dynamic: bool) -> None:
    for r in m.regions:
        if "w" in r.perms and "x" in r.perms:
            m.warnings.append(f"W+X region: {r.name}")
    for r in m.regions:
        if (r.file_off >= 0 and r.file_size > 0 and r.vsize > 0
                and r.vsize > _VSIZE_RATIO * r.file_size
                and r.vsize - r.file_size > _VSIZE_SLACK):
            m.warnings.append(
                f"virtual size >> raw size in {r.name} "
                f"({r.vsize:#x} vs {r.file_size:#x}); room to unpack into?")
    if m.entry_va is not None:
        r = m.region_at_va(m.entry_va)
        if r is None or "x" not in r.perms:
            where = f"in non-executable {r.name}" if r else "outside every mapped region"
            m.warnings.append(f"entry point {m.entry_va:#x} {where}")
    if has_dynamic and not m.imports and m.format in ("elf", "pe"):
        m.warnings.append("no imports in a dynamically-linked binary")


def parse(path: str | os.PathLike, arch: str | None = None) -> BinaryModel:
    """Parse any file into a BinaryModel. Never raises on malformed input."""
    path = os.fspath(path)
    size = os.path.getsize(path)
    sha = sha256_file(path)
    head = read_head(path, 64)
    fmt = guess_format(head, path)

    if fmt not in ("elf", "pe", "macho"):
        return _raw_model(path, size, sha, arch, [])

    warnings: list[str] = []
    try:
        b = lief.parse(path)
        if b is None:
            raise ValueError("LIEF returned None")
        builder = {"elf": _from_elf, "pe": _from_pe, "macho": _from_macho}[fmt]
        (arch_, bits, endian, entry, backed, other, mappings,
         symbols, imports, exports, arch_ranges, has_dynamic) = builder(
            b, path, head, size, warnings)
    except Exception as e:  # malformed input is expected input
        warnings.append(f"{fmt} parse failed ({type(e).__name__}: {e}); raw fallback")
        return _raw_model(path, size, sha, arch, warnings)

    mappings = _sanitise_mappings(mappings, size, warnings)
    regions = _materialise(backed, other, size, warnings)
    model = BinaryModel(
        path=path, sha256=sha, size=size, format=fmt,
        arch=arch or arch_, bits=bits, endian=endian, entry_va=entry,
        regions=regions, symbols=symbols, imports=imports, exports=exports,
        arch_ranges=arch_ranges, warnings=warnings, mappings=mappings,
    )
    _post_checks(model, has_dynamic)
    return model
