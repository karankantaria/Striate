"""binviz command-line interface.

Subcommands land phase by phase: probe (P0), model (P1), signal/hist (P2),
surface (P3), cfg (P5), triage/serve (P6+).
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
