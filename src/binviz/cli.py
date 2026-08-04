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

    return 1


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
