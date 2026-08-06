"""Triage verdict synthesis — Phase 11.

Consumes every prior artifact (model, calibration, region entropy,
function recovery stats) and emits a machine-readable verdict plus a
list of findings. Every finding carries file offsets where they exist,
so the report is a navigation surface, not a text dump: clicking a
finding drives the SelectionStore and every view follows.

Region awareness is the whole game (§5.3): high entropy in an
*executable* region is packing evidence; the same entropy in a data
region is a compressed resource and must not tip the verdict. The
classifier that can't tell those apart is the one that calls every
installer malware.
"""

from __future__ import annotations

import numpy as np

from .model import BinaryModel, Region
from .signals import DECISION_WINDOW, load_calibration
from .stats import window_stats

# regions smaller than this carry too few bytes for a defensible call
MIN_ENTROPY_REGION = 512
MIN_NONEXEC_REGION = 16 * 1024
MIN_IMAGE_REGION = 64 * 1024

# a "function every 16 KiB" is far below any real compiler output
FUNCTION_DENSITY_BYTES = 16 * 1024
MIN_DENSITY_EXEC_BYTES = 64 * 1024

IMPORT_STARVED_MAX = 3
# an overlay is high-severity when it plausibly *is* the payload: a large
# fraction of the file AND large in absolute terms. A 1 KiB tail on a 4 KiB
# hello-world is 25% of the file and still just alignment slack.
OVERLAY_HIGH_FRACTION = 0.20
OVERLAY_HIGH_BYTES = 64 * 1024

# autocorrelation peak strength for "this is probably raster rows"; ELF
# .rodata/.debug_* sections show weak periodic structure around 0.6-0.7,
# real pixel data measures 0.84-1.0 on the corpus
IMAGE_SCORE_MIN = 0.75

_SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def _fmt_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GiB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n} B"


def _region_entropy(buf, r: Region) -> dict | None:
    """Decision-window entropy stats for a file-backed region.

    Regions shorter than the decision window fall back to one whole-
    region window — plug-in entropy is biased *low* at small windows
    (§5.3), so the fallback can only under-report, never cry wolf.
    """
    if r.file_off < 0 or r.file_size < MIN_ENTROPY_REGION:
        return None
    chunk = buf[r.file_off:r.file_off + r.file_size]
    window = min(DECISION_WINDOW, len(chunk))
    values = window_stats(chunk, window, window, which=("entropy",))["entropy"]
    if not len(values):
        return None
    return {
        "mean": float(values.mean()),
        "peak": float(values.max()),
        "windows": int(len(values)),
        "window": window,
        "values": values,
    }


def _entropy_findings(buf, model: BinaryModel, thresholds: dict) -> list[dict]:
    """HIGH_ENTROPY_EXEC (high) and HIGH_ENTROPY_NONEXEC (low)."""
    out: list[dict] = []
    thr = thresholds["packed_h_min"]
    for r in model.regions:
        if r.file_off < 0 or r.kind == "header":
            continue
        executable = "x" in r.perms
        if not executable and r.file_size < MIN_NONEXEC_REGION:
            continue
        st = _region_entropy(buf, r)
        if st is None:
            continue
        values = st["values"]
        high = int((values >= thr).sum())
        if high / len(values) < 0.5:
            continue
        offsets = [r.file_off, r.file_off + r.file_size]
        if executable:
            out.append({
                "severity": "high", "code": "HIGH_ENTROPY_EXEC",
                "detail": f"Region {r.name} entropy {st['mean']:.2f} "
                          f"bits/byte, {high}/{st['windows']} windows at or "
                          f"above the calibrated packed threshold {thr:.2f}",
                "offsets": offsets,
            })
        else:
            out.append({
                "severity": "low", "code": "HIGH_ENTROPY_NONEXEC",
                "detail": f"Region {r.name} entropy {st['mean']:.2f} "
                          f"bits/byte in a non-executable region — "
                          f"compressed or encrypted data, not packing "
                          f"evidence",
                "offsets": offsets,
            })
    return out


def _image_findings(buf, model: BinaryModel, thresholds: dict) -> list[dict]:
    """EMBEDDED_IMAGE_LIKELY: a strong autocorrelation stride over a
    structured (non-random) range reads as raw pixel rows (§5.7)."""
    from .surfaces.image import suggest_stride

    out: list[dict] = []
    for r in model.regions:
        if (r.file_off < 0 or "x" in r.perms or r.kind == "header"
                or r.file_size < MIN_IMAGE_REGION):
            continue
        chunk = buf[r.file_off:r.file_off + r.file_size]
        st = _region_entropy(buf, r)
        if st is None or st["mean"] >= thresholds["random_h_min"]:
            continue   # compressed/random data autocorrelates with nothing
        cands = suggest_stride(chunk)
        if not cands:
            continue
        best = max(cands, key=lambda c: c["score"])
        if best["score"] < IMAGE_SCORE_MIN:
            continue
        stride = cands[0]["bytes"]
        out.append({
            "severity": "low", "code": "EMBEDDED_IMAGE_LIKELY",
            "detail": f"autocorrelation stride {stride} B (peak score "
                      f"{best['score']:.2f}) + entropy {st['mean']:.1f} "
                      f"bits/byte over {_fmt_size(r.file_size)} in "
                      f"{r.name} — try the image view at this stride",
            "offsets": [r.file_off, r.file_off + r.file_size],
            "stride_bytes": stride,
        })
    return out


def _elf_truncation(buf, model: BinaryModel) -> dict | None:
    """The ELF header declares where the section-header table lives; a
    table past EOF means the tail of the file is gone. LIEF happily
    reparses a truncated ELF into a smaller self-consistent model (no
    clamp warnings fire), so this is checked from the header directly."""
    import struct

    if model.format != "elf" or model.size < 0x40:
        return None
    head = bytes(buf[:0x40])
    endian = "<" if head[5] == 1 else ">"
    if head[4] == 2:   # ELFCLASS64
        (e_shoff,) = struct.unpack_from(endian + "Q", head, 0x28)
        e_shentsize, e_shnum = struct.unpack_from(endian + "HH", head, 0x3A)
    else:              # ELFCLASS32
        (e_shoff,) = struct.unpack_from(endian + "I", head, 0x20)
        e_shentsize, e_shnum = struct.unpack_from(endian + "HH", head, 0x2E)
    table = e_shentsize * e_shnum
    if e_shoff and e_shnum and e_shoff + table > model.size:
        return {
            "severity": "high", "code": "TRUNCATED",
            "detail": f"section header table at {e_shoff:#x}"
                      f"(+{table:#x}) extends past EOF "
                      f"({model.size:#x}) — file truncated?",
            "offsets": None,
        }
    return None


def _structure_findings(model: BinaryModel) -> list[dict]:
    out: list[dict] = []

    for w in model.warnings:
        if "parse failed" in w:
            out.append({"severity": "high", "code": "PARSE_FAILED",
                        "detail": w, "offsets": None})
        elif "truncated" in w:
            out.append({"severity": "high", "code": "TRUNCATED",
                        "detail": w, "offsets": None})
        elif w.startswith("entry point"):
            off = (model.va_to_off(model.entry_va)
                   if model.entry_va is not None else None)
            out.append({"severity": "medium", "code": "ENTRY_OUTSIDE_EXEC",
                        "detail": w,
                        "offsets": [off, off + 1] if off is not None else None})
        elif "virtual size >> raw size" in w:
            out.append({"severity": "medium", "code": "VSIZE_EXCEEDS_RAW",
                        "detail": w, "offsets": None})
        elif "no section headers" in w:
            out.append({"severity": "low", "code": "SECTIONLESS",
                        "detail": w, "offsets": None})

    for r in model.regions:
        if "w" in r.perms and "x" in r.perms and r.file_size > 0:
            out.append({
                "severity": "medium", "code": "WX_REGION",
                "detail": f"writable and executable region {r.name}",
                "offsets": [r.file_off, r.file_off + r.file_size]
                if r.file_off >= 0 else None,
            })

    for r in model.regions:
        if r.kind == "overlay" and r.file_size > 0:
            frac = r.file_size / max(1, model.size)
            big = (frac >= OVERLAY_HIGH_FRACTION
                   and r.file_size >= OVERLAY_HIGH_BYTES)
            out.append({
                "severity": "high" if big else "medium",
                "code": "OVERLAY_PRESENT",
                "detail": f"{_fmt_size(r.file_size)} appended past the last "
                          f"section ({frac * 100:.0f}% of the file)",
                "offsets": [r.file_off, r.file_off + r.file_size],
            })

    if (model.format in ("elf", "pe")
            and len(model.imports) <= IMPORT_STARVED_MAX):
        out.append({
            "severity": "medium", "code": "IMPORT_STARVED",
            "detail": f"{len(model.imports)} imports for a "
                      f"{_fmt_size(model.size)} binary (packed, or "
                      f"statically linked)",
            "offsets": None,
        })
    return out


def _function_findings(model: BinaryModel,
                       functions: dict | None) -> list[dict]:
    if not functions:
        return []
    stats = functions.get("stats") or {}
    n_fns = stats.get("functions")
    exec_bytes = stats.get("exec_bytes")
    if not exec_bytes or n_fns is None:
        return []
    if exec_bytes < MIN_DENSITY_EXEC_BYTES:
        return []
    if n_fns * FUNCTION_DENSITY_BYTES >= exec_bytes:
        return []
    biggest = max(
        (r for r in model.regions if "x" in r.perms and r.file_off >= 0),
        key=lambda r: r.file_size, default=None)
    return [{
        "severity": "low", "code": "LOW_FUNCTION_DENSITY",
        "detail": f"{n_fns} function(s) over {_fmt_size(exec_bytes)} of "
                  f"executable bytes",
        "offsets": [biggest.file_off, biggest.file_off + biggest.file_size]
        if biggest else None,
    }]


def triage(buf, model: BinaryModel, functions: dict | None = None,
           cal: dict | None = None) -> dict:
    """Synthesise the verdict document (PLAN §P11 wire format).

    `functions` is the P5 program index (functions.json) or None when
    recovery failed — the verdict must still be produced.
    """
    thresholds = (cal or load_calibration())["derived"]

    findings: list[dict] = []
    findings += _entropy_findings(buf, model, thresholds)
    findings += _structure_findings(model)
    trunc = _elf_truncation(buf, model)
    if trunc and not any(f["code"] == "TRUNCATED" for f in findings):
        findings.append(trunc)
    findings += _function_findings(model, functions)
    findings += _image_findings(buf, model, thresholds)
    findings.sort(key=lambda f: (_SEV_ORDER[f["severity"]], f["code"]))

    codes = {f["code"] for f in findings}
    has_high = any(f["severity"] == "high" for f in findings)

    if "PARSE_FAILED" in codes or "TRUNCATED" in codes:
        verdict, confidence = "corrupt", 0.8
    elif model.format == "raw":
        verdict, confidence = "non_executable", 0.9
    elif "HIGH_ENTROPY_EXEC" in codes:
        # entropy is the decisive signal; the rest corroborates
        verdict, confidence = "likely_packed", 0.6
        for c in ("IMPORT_STARVED", "VSIZE_EXCEEDS_RAW", "SECTIONLESS",
                  "ENTRY_OUTSIDE_EXEC", "LOW_FUNCTION_DENSITY"):
            if c in codes:
                confidence += 0.07
        confidence = min(confidence, 0.95)
    elif not has_high:
        n_medium = sum(1 for f in findings if f["severity"] == "medium")
        verdict = "likely_benign_binary"
        confidence = max(0.5, 0.85 - 0.05 * n_medium)
    else:
        verdict, confidence = "inconclusive", 0.4

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "findings": findings,
        "format": model.format,
        "size": model.size,
    }
