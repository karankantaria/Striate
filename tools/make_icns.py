#!/usr/bin/env python
"""Derive `packaging/icons/icon.icns` from the 1024px master
(ARCHITECTURE.md §3).

    python tools/make_icns.py

The macOS icon is the one asset the inventory said needed a Mac —
`iconutil` ships with Xcode and runs nowhere else. But `iconutil` is a
*container* tool: the file it emits is a header, a length, and a run of
PNGs under four-character type codes, and the scaling it does on the way
is an ordinary Lanczos downsample of the same master. None of that needs
macOS, so this writes the container directly and the asset stops being
blocked on hardware nobody may have.

**Downsampled from `icon-1024.png`, never re-drawn.** Redrawing the mark
at 16px would look better and would also be a second copy of the branding
in the repo, which §3 names as exactly how branding drifts. The master is
`icon.svg`; `icon-1024.png` is its raster; everything else is derived.

The entry list mirrors what `iconutil -c icns` produces from a standard
`.iconset` — same ten type codes, same ten sizes — so a Mac owner can
regenerate the file the canonical way and get the same thing:

    python tools/make_icns.py --iconset packaging/icons/icon.iconset
    iconutil -c icns packaging/icons/icon.iconset      # on a Mac

The one difference is the optional `TOC ` entry `iconutil` writes first,
an index of the entries that follow. It is a lookup optimisation, every
reader treats it as optional, and emitting one that disagreed with the
entries would be worse than not having it.
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "packaging" / "icons" / "icon-1024.png"
TARGET = ROOT / "packaging" / "icons" / "icon.icns"

#: (icns type, pixel size, .iconset filename) — the exact set `iconutil`
#: emits, in its order. The @2x entries are not duplicates: macOS picks
#: between `icp4` (16px) and `ic11` (32px drawn at 16pt) by display scale,
#: and an icon missing the retina half is the one that looks soft.
ENTRIES = (
    (b"icp4", 16, "icon_16x16.png"),
    (b"ic11", 32, "icon_16x16@2x.png"),
    (b"icp5", 32, "icon_32x32.png"),
    (b"ic12", 64, "icon_32x32@2x.png"),
    (b"ic07", 128, "icon_128x128.png"),
    (b"ic13", 256, "icon_128x128@2x.png"),
    (b"ic08", 256, "icon_256x256.png"),
    (b"ic14", 512, "icon_256x256@2x.png"),
    (b"ic09", 512, "icon_512x512.png"),
    (b"ic10", 1024, "icon_512x512@2x.png"),
)


def _png(image, size: int) -> bytes:
    """One square PNG at `size`, RGBA, from the master."""
    from PIL import Image

    # LANCZOS on the full-resolution master rather than a chain of halvings:
    # the mark is a 128px-wide stroke on a flat field, and successive
    # resamples of an already-resampled image soften it for nothing.
    scaled = (image if image.size == (size, size)
              else image.resize((size, size), Image.LANCZOS))
    buf = io.BytesIO()
    # optimize is worth it here: the file is committed, and these are flat
    # colours that deflate extremely well.
    scaled.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build(master: Path, target: Path, iconset: Path | None = None) -> int:
    from PIL import Image

    if not master.is_file():
        print(f"binviz: no master at {master}", file=sys.stderr)
        return 1

    image = Image.open(master).convert("RGBA")
    if image.size != (1024, 1024):
        # Not fatal-by-accident: the 1024 master is what every size below is
        # derived from, and silently upscaling from something smaller would
        # ship a blurry app icon that nobody would trace back to here.
        print(f"binviz: {master.name} is {image.size[0]}x{image.size[1]}, "
              f"expected 1024x1024", file=sys.stderr)
        return 1

    chunks: list[bytes] = []
    for code, size, filename in ENTRIES:
        png = _png(image, size)
        # length covers the 8-byte header itself, not just the payload
        chunks.append(code + struct.pack(">I", len(png) + 8) + png)
        if iconset is not None:
            iconset.mkdir(parents=True, exist_ok=True)
            (iconset / filename).write_bytes(png)

    body = b"".join(chunks)
    target.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)

    print(f"binviz: wrote {target.relative_to(ROOT)} "
          f"({target.stat().st_size / 1e3:.1f} kB, {len(ENTRIES)} sizes)")
    for code, size, _ in ENTRIES:
        print(f"  {code.decode('ascii')}  {size:>4}x{size}")
    if iconset is not None:
        print(f"binviz: wrote {iconset.relative_to(ROOT)} — on a Mac, "
              f"`iconutil -c icns {iconset.name}` rebuilds the same file")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--master", type=Path, default=MASTER)
    ap.add_argument("--out", type=Path, default=TARGET)
    ap.add_argument("--iconset", type=Path, default=None, metavar="DIR",
                    help="also write the .iconset directory iconutil takes")
    args = ap.parse_args(argv)
    return build(args.master, args.out, args.iconset)


if __name__ == "__main__":
    sys.exit(main())
