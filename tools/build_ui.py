#!/usr/bin/env python
"""Build the web UI and stage the package's generated data.

`web/dist` sits outside `src/binviz/`, so setuptools cannot see it and a
wheel built without this step ships a backend with no frontend at all —
`pip install binviz` would give you a JSON API and no way to look at it.
This copies the built assets to `src/binviz/webui/`, which pyproject.toml
declares as package data.

Run before building a wheel:

    python tools/build_ui.py
    python -m build            # or: pip wheel .

The staged directories are generated output and are gitignored; they are
rebuilt from `web/` and `packaging/` whenever this runs.

Also stages the window icon for `binviz app` (ARCHITECTURE.md §2). It is copied
rather than committed to `src/binviz/icons/` because `packaging/icons/` is
the canonical branding and two copies in one repo is precisely what
ARCHITECTURE.md warns against.

And `corpus/calibration.json`, for the same reason and with more at stake:
`corpus/` is not in the wheel, so an installed binviz used to fall back to
the hardcoded defaults in `signals._FALLBACK_CAL` — quietly classifying
windows differently from a checkout, which is the exact folklore
ARCHITECTURE.md §2.1 exists to refuse. Canonical copy stays in `corpus/`,
written by `corpus/calibrate.py`; this one is generated and gitignored.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist"
TARGET = ROOT / "src" / "binviz" / "webui"
ICON_SRC = ROOT / "packaging" / "icons"
ICON_TARGET = ROOT / "src" / "binviz" / "icons"
CAL_SRC = ROOT / "corpus" / "calibration.json"
CAL_TARGET = ROOT / "src" / "binviz" / "calibration.json"
#: What the desktop window needs, one per platform: Windows' backend goes
#: through System.Drawing.Icon and accepts nothing but .ico, GTK and Qt take
#: the PNG. Not the whole icon set — the 1024px master and the other sizes
#: are for deriving assets, and there is no reason to carry them in a wheel.
ICON_FILES = ("icon.ico", "icon-256.png")

# Source maps are ~600 KB and are a development aid, not something a user of
# the wheel needs. --with-sourcemaps keeps them.
DEFAULT_IGNORES = ("*.map",)


def _npm() -> str | None:
    for name in ("npm", "npm.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-build", action="store_true",
                    help="stage the existing web/dist without running npm")
    ap.add_argument("--with-sourcemaps", action="store_true",
                    help="include .map files in the package")
    args = ap.parse_args(argv)

    if not args.skip_build:
        npm = _npm()
        if npm is None:
            print("binviz: npm not found on PATH; install Node, or pass "
                  "--skip-build to stage an existing web/dist",
                  file=sys.stderr)
            return 1
        print(f"binviz: building {WEB} …", flush=True)
        result = subprocess.run([npm, "run", "build"], cwd=WEB)
        if result.returncode != 0:
            print("binviz: frontend build failed", file=sys.stderr)
            return result.returncode

    index = DIST / "index.html"
    if not index.is_file():
        print(f"binviz: {index} missing — nothing to stage", file=sys.stderr)
        return 1

    ignores = () if args.with_sourcemaps else DEFAULT_IGNORES
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(DIST, TARGET,
                    ignore=shutil.ignore_patterns(*ignores) if ignores else None)

    staged_icons = _stage_icons()
    if not _stage_calibration():
        return 1

    files = sorted(p for p in TARGET.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    print(f"binviz: staged {len(files)} file(s), {total / 1e6:.2f} MB "
          f"-> {TARGET.relative_to(ROOT)}")
    for p in files:
        print(f"  {p.relative_to(TARGET).as_posix()}  "
              f"{p.stat().st_size / 1e3:.1f} kB")
    if staged_icons:
        print(f"binviz: staged {len(staged_icons)} icon(s) -> "
              f"{ICON_TARGET.relative_to(ROOT)}")
    print(f"binviz: staged {CAL_TARGET.relative_to(ROOT)}")
    return 0


def _stage_calibration() -> bool:
    """Copy the measured thresholds into the package. Fatal when missing,
    unlike the icons: a wheel without branding looks plainer, a wheel
    without calibration *analyses differently* and says nothing about it.

    `corpus/calibration.json` is tracked, so the only way to reach this
    message is to have deleted it — in which case building a wheel is the
    wrong next step anyway.
    """
    if not CAL_SRC.is_file():
        # the full path, not one relative to ROOT: an error handler that
        # raises ValueError of its own explains nothing
        print(f"binviz: no {CAL_SRC} — a wheel built now "
              f"would fall back to hardcoded thresholds and classify "
              f"windows differently from this checkout.\n"
              f"  Run `python corpus/calibrate.py` (needs a built corpus).",
              file=sys.stderr)
        return False
    shutil.copy2(CAL_SRC, CAL_TARGET)
    return True


def _stage_icons() -> list[Path]:
    """Copy the window icon into the package. Never fatal: a wheel without
    branding still runs, and refusing to build over a missing PNG would be
    a worse outcome than a default window icon."""
    staged: list[Path] = []
    if ICON_TARGET.exists():
        shutil.rmtree(ICON_TARGET)
    for name in ICON_FILES:
        src = ICON_SRC / name
        if not src.is_file():
            print(f"binviz: no {src.relative_to(ROOT)}; the desktop window "
                  f"will use the default icon", file=sys.stderr)
            continue
        ICON_TARGET.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, ICON_TARGET / name)
        staged.append(ICON_TARGET / name)
    return staged


if __name__ == "__main__":
    sys.exit(main())
