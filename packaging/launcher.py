"""Entry point for the frozen desktop build (`packaging/binviz.spec`).

A wheel gets `binviz = binviz.cli:main` from `[project.scripts]`; a frozen
build has no console-script machinery, so it needs a real module to start
from. This is that module, and it is deliberately almost empty — anything
that behaves differently in the frozen app than in `pip install binviz` is
a second product to reason about, and §2.4's argument about the desktop
build only holds if the desktop build *is* the same program.

The one difference: **launched with no arguments, it runs `binviz app`.**
A double-clicked executable passes no argv, and `binviz` with no subcommand
exits 2 with a usage message into a console window that closes immediately.
Every subcommand is still reachable — `binviz.exe serve --port 9000`,
`binviz.exe triage sample.exe` — because the frozen build is the whole CLI,
not just the window.

Note that `--root` still defaults to the working directory, exactly as
`binviz app` does. For a double-clicked executable that is the folder the
shortcut starts in, which is usually the app's own folder and has nothing
in it worth triaging. That is not a bug to route around here: the default
is a confinement boundary (S1d), and quietly widening it to `$HOME` for
one launch path would weaken it in the case where the user is least likely
to notice. Set the shortcut's "Start in", or pass `--root`.
"""

from __future__ import annotations

import sys


def main() -> int:
    from binviz.cli import main as cli_main

    argv = sys.argv[1:]
    return cli_main(argv if argv else ["app"])


if __name__ == "__main__":
    sys.exit(main())
