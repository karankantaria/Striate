"""binviz command-line interface.

Subcommands land phase by phase: probe (P0), model (P1), signal/hist (P2),
surface (P3), disasm (P4), cfg (P5), triage/serve (P6+).
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    # Windows consoles may be cp1252; never let output encoding crash the CLI
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="binviz")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="identify a file from its header magic")
    p_probe.add_argument("file")

    p_model = sub.add_parser("model", help="parse a file into its address-space model")
    p_model.add_argument("file")
    p_model.add_argument("--json", action="store_true", help="full model as JSON")
    p_model.add_argument("--arch", help="arch override for raw/headerless input")

    p_signal = sub.add_parser("signal", help="compute a named signal over a file")
    p_signal.add_argument("file")
    p_signal.add_argument("--name", default="entropy_4096")
    p_signal.add_argument("--png", help="render to PNG (min/max envelope + mean)")
    p_signal.add_argument("--json", action="store_true",
                          help="print summary stats as JSON")

    p_surf = sub.add_parser("surface", help="render a surface to PNG")
    p_surf.add_argument("file")
    p_surf.add_argument("--name", default="linear",
                        help="linear | hilbert | image | ngram2 | ngram3 | dotplot")
    p_surf.add_argument("--png", help="output PNG path")
    p_surf.add_argument("--start", type=int, default=0)
    p_surf.add_argument("--end", type=int, default=-1)
    p_surf.add_argument("-w", "--width", type=int, default=512)
    p_surf.add_argument("-H", "--height", type=int, default=512)
    p_surf.add_argument("--dtype", default="u8")
    p_surf.add_argument("--scale", type=int, default=1,
                        help="nearest-neighbour upscale of the PNG")
    p_surf.add_argument("-p", "--param", action="append", default=[],
                        metavar="K=V", help="surface parameter (repeatable)")

    p_stride = sub.add_parser("stride",
                              help="suggest image row strides (autocorrelation)")
    p_stride.add_argument("file")
    p_stride.add_argument("--mode", default="grey8",
                          help="image mode, for pixel-stride conversion")
    p_stride.add_argument("--start", type=int, default=0)
    p_stride.add_argument("--end", type=int, default=-1)
    p_stride.add_argument("--top", type=int, default=3)

    p_dis = sub.add_parser("disasm", help="decode instructions (recursive "
                           "descent from entry, or linear sweep of a range)")
    p_dis.add_argument("file")
    p_dis.add_argument("--linear", action="store_true",
                       help="linear sweep instead of recursive descent")
    p_dis.add_argument("--start", type=int, default=None,
                       help="file offset (linear mode; default: .text)")
    p_dis.add_argument("--end", type=int, default=None)
    p_dis.add_argument("--va", type=lambda s: int(s, 0), action="append",
                       default=[], help="extra seed VA (repeatable)")
    p_dis.add_argument("--sym", action="append", default=[],
                       help="extra seed symbol name (repeatable)")
    p_dis.add_argument("--no-entry", action="store_true",
                       help="do not seed from the entry point")
    p_dis.add_argument("--arch", help="arch override for raw/headerless input")
    p_dis.add_argument("--limit", type=int, default=40,
                       help="max instructions to print (0 = all)")
    p_dis.add_argument("--json", action="store_true")

    p_hist = sub.add_parser("hist", help="n-gram histogram of a file")
    p_hist.add_argument("file")
    p_hist.add_argument("--n", type=int, default=1, choices=(1, 2, 3))
    p_hist.add_argument("--dtype", default="u8")
    p_hist.add_argument("--mode", default="log1p",
                        choices=("log1p", "rank", "sqrt", "linear"))
    p_hist.add_argument("--png", help="render to PNG (n=2 only)")

    args = parser.parse_args(argv)

    if args.command == "probe":
        from .probe import probe

        try:
            result = probe(args.file)
        except OSError as e:
            print(f"binviz: cannot read {args.file}: {e}", file=sys.stderr)
            return 1
        json.dump(result, sys.stdout, indent=2)
        print()
        return 0

    if args.command == "model":
        from .parse import parse as parse_binary

        try:
            model = parse_binary(args.file, arch=args.arch)
        except OSError as e:
            print(f"binviz: cannot read {args.file}: {e}", file=sys.stderr)
            return 1
        if args.json:
            json.dump(model.to_json(), sys.stdout, indent=2)
            print()
        else:
            _print_model_summary(model)
        return 0

    if args.command == "signal":
        return _cmd_signal(args)
    if args.command == "disasm":
        return _cmd_disasm(args)
    if args.command == "hist":
        return _cmd_hist(args)
    if args.command == "surface":
        return _cmd_surface(args)
    if args.command == "stride":
        return _cmd_stride(args)

    return 1


def _parse_params(pairs: list[str]) -> dict:
    """K=V strings into typed params (int, float, bool, else str)."""
    out: dict = {}
    for item in pairs:
        key, _, raw = item.partition("=")
        if not _:
            raise SystemExit(f"binviz: --param expects K=V, got {item!r}")
        low = raw.lower()
        if low in ("true", "false"):
            out[key] = low == "true"
            continue
        try:
            out[key] = int(raw)
            continue
        except ValueError:
            pass
        try:
            out[key] = float(raw)
        except ValueError:
            out[key] = raw
    return out


def _jsonable(v):
    import numpy as np

    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


def _cmd_surface(args) -> int:
    from .loader import MappedFile
    from .surfaces import SurfaceRequest, get_surface

    params = _parse_params(args.param)
    surface = get_surface(args.name)
    with MappedFile.open(args.file) as mf:
        req = SurfaceRequest(args.start, args.end, args.width, args.height,
                             args.dtype, params).clamp(mf.size)
        raster = surface.render(mf.view, req)
        raster.pixels = raster.pixels.copy()  # release the mmap view
    summary = {"surface": args.name, "shape": list(raster.pixels.shape),
               "kind": raster.kind, "meta": _jsonable(raster.meta)}
    if args.png:
        from .render import save_raster_png

        save_raster_png(raster, args.png, scale=args.scale)
        summary["png"] = args.png
    json.dump(summary, sys.stdout, indent=2)
    print()
    return 0


def _cmd_stride(args) -> int:
    from .loader import MappedFile
    from .surfaces.image import suggest_stride_pixels

    with MappedFile.open(args.file) as mf:
        cands = suggest_stride_pixels(mf.view, args.mode, start=args.start,
                                      end=args.end, top=args.top)
    json.dump({"file": args.file, "mode": args.mode,
               "candidates": _jsonable(cands)}, sys.stdout, indent=2)
    print()
    return 0


def _cmd_disasm(args) -> int:
    from .disasm import linear_sweep, mode_for_model, recursive_descent
    from .loader import MappedFile
    from .parse import parse as parse_binary

    model = parse_binary(args.file, arch=args.arch)
    mode = mode_for_model(model)
    if mode is None:
        print(f"binviz: no decoder for arch {model.arch!r}; "
              f"use --arch to override", file=sys.stderr)
        return 1

    info: dict = {}
    with MappedFile.open(args.file) as mf:
        if args.linear:
            if args.start is not None:
                start = args.start
                end = args.end if args.end is not None else mf.size
            else:  # default to .text (or the whole file if there is none)
                text = next((r for r in model.regions if r.name == ".text"), None)
                start = text.file_off if text else 0
                end = start + text.file_size if text else mf.size
            va = model.off_to_va(start)
            if va is None:
                va = start
            insns = linear_sweep(bytes(mf.view[start:end]), va, mode)
        else:
            seeds = [] if args.no_entry or model.entry_va is None \
                else [model.entry_va]
            seeds += args.va
            by_name = {s.name: s.va for s in model.symbols}
            for name in args.sym:
                if name not in by_name:
                    print(f"binviz: no symbol {name!r}", file=sys.stderr)
                    return 1
                seeds.append(by_name[name])
            if not seeds:
                print("binviz: no seeds (stripped + --no-entry?)",
                      file=sys.stderr)
                return 1
            insns = recursive_descent(mf.view, model, seeds, info=info)

    ordered = sorted(insns.values(), key=lambda i: i.va)
    n_invalid = sum(1 for i in ordered if i.is_invalid)
    summary = {
        "file": args.file, "mode": mode,
        "strategy": "linear" if args.linear else "recursive",
        "instructions": len(ordered), "invalid": n_invalid,
    }
    if info:
        summary.update(
            indirect_jumps=len(info["indirect_jumps"]),
            pointer_seeds=len(info["pointer_seeds"]),
            decode_errors=len(info["decode_errors"]),
            truncated=info["truncated"],
        )
    if args.json:
        limit = args.limit if args.limit > 0 else len(ordered)
        summary["insns"] = [i.to_json() for i in ordered[:limit]]
        json.dump(summary, sys.stdout, indent=2)
        print()
        return 0
    for k, v in summary.items():
        print(f"{k}: {v}")
    limit = args.limit if args.limit > 0 else len(ordered)
    prev_end = None
    for i in ordered[:limit]:
        if prev_end is not None and i.va != prev_end:
            print("  ...")
        marks = ""
        if i.targets:
            marks = "  -> " + ", ".join(f"{t:#x}" for t in i.targets)
        elif i.is_indirect:
            marks = "  -> ?"
        print(f"  {i.va:#010x}  {i.bytes_.hex():<20} {i.mnemonic} "
              f"{i.op_str}{marks}")
        prev_end = i.end_va
    if len(ordered) > limit:
        print(f"  ... {len(ordered) - limit} more (raise --limit)")
    return 0


def _cmd_signal(args) -> int:
    from .loader import MappedFile
    from .signals import SIGNALS, compute_signals

    with MappedFile.open(args.file) as mf:
        sig = compute_signals(mf.view, [args.name])[args.name]
    summary = {
        "name": sig.name, "unit": sig.unit,
        "windows": int(len(sig.values)),
        "min": float(sig.values.min()) if len(sig.values) else None,
        "mean": float(sig.values.mean()) if len(sig.values) else None,
        "max": float(sig.values.max()) if len(sig.values) else None,
        "window": SIGNALS[args.name][0], "stride": SIGNALS[args.name][1],
    }
    if args.png:
        from .render import save_signal_png

        save_signal_png(sig.values, args.png, lo=sig.lo, hi=sig.hi,
                        title=f"{args.name}  {args.file}")
        summary["png"] = args.png
    json.dump(summary, sys.stdout, indent=2)
    print()
    return 0


def _cmd_hist(args) -> int:
    from .elements import elements, quantise
    from .loader import MappedFile
    from .stats import ngram

    with MappedFile.open(args.file) as mf:
        vals = elements(mf.view, args.dtype)
        bins, meta = quantise(vals, args.dtype)
        del vals
        result = ngram(bins, args.n)
        # drop every numpy view of the mmap before close() (ngram results
        # are fresh arrays; for u8 `bins` is still a view of the file)
        del bins
    summary = {"n": args.n, "dtype": args.dtype, "quantise": meta}
    if args.n == 1:
        summary["nonzero_bins"] = int((result > 0).sum())
        summary["top"] = sorted(
            ((int(c), i) for i, c in enumerate(result)), reverse=True)[:8]
    elif args.n == 2:
        summary["nonzero_cells"] = int((result > 0).sum())
        if args.png:
            from .render import save_hist2d_png

            save_hist2d_png(result, args.png, mode=args.mode)
            summary["png"] = args.png
            summary["display_mode"] = args.mode
    else:
        coords, counts = result
        summary["sparse_points"] = int(len(counts))
        summary["max_count"] = int(counts.max()) if len(counts) else 0
    json.dump(summary, sys.stdout, indent=2)
    print()
    return 0


def _print_model_summary(m) -> None:
    entry = f"{m.entry_va:#x}" if m.entry_va is not None else "-"
    print(f"{m.path}: {m.format} {m.arch} {m.bits}-bit {m.endian}, "
          f"{m.size} bytes, entry {entry}")
    print(f"{'name':<20} {'kind':<8} {'file_off':>10} {'file_sz':>10} "
          f"{'vaddr':>12} {'vsize':>10} perms")
    for r in m.regions:
        fo = f"{r.file_off:#x}" if r.file_off >= 0 else "-"
        va = f"{r.vaddr:#x}" if r.vaddr >= 0 else "-"
        print(f"{r.name[:20]:<20} {r.kind:<8} {fo:>10} {r.file_size:>10} "
              f"{va:>12} {r.vsize:>10} {r.perms}")
    print(f"symbols: {len(m.symbols)}  imports: {len(m.imports)}  "
          f"exports: {len(m.exports)}  arch_ranges: {len(m.arch_ranges)}")
    for w in m.warnings:
        print(f"warning: {w}")


if __name__ == "__main__":
    sys.exit(main())
