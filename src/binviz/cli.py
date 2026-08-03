"""binviz command-line interface.

Subcommands land phase by phase: probe (P0), model (P1), signal/hist (P2),
surface (P3), cfg (P5), triage/serve (P6+).
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="binviz")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="identify a file from its header magic")
    p_probe.add_argument("file")

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

    return 1


if __name__ == "__main__":
    sys.exit(main())
