#!/usr/bin/env python3
"""Build the ground-truth corpus into corpus/out/.

Cross-platform: compiled samples are produced with `zig cc` (pip package
`ziglang`) cross-targeting x86_64-linux, so a native Linux toolchain is not
required. The samples are parsed by binviz, never executed, so host OS is
irrelevant. UPX is located via $UPX, corpus/tools/, or PATH.

Usage:  python corpus/build.py [--force] [--only NAME]
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SRC = os.path.join(HERE, "src")

MiB = 1 << 20


# ---------------------------------------------------------------- toolchain

def zig(args: list[str]) -> list[str]:
    return [sys.executable, "-m", "ziglang"] + args


def find_upx() -> str | None:
    if os.environ.get("UPX"):
        return os.environ["UPX"]
    exe = "upx.exe" if os.name == "nt" else "upx"
    hits = glob.glob(os.path.join(HERE, "tools", "upx-*", exe))
    if hits:
        return sorted(hits)[-1]
    return shutil.which("upx")


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))}\n{r.stdout}{r.stderr}")


# ---------------------------------------------------------------- synthetic

def gen_zeros(path: str) -> None:
    with open(path, "wb") as f:
        f.write(b"\x00" * MiB)


def gen_urandom(path: str) -> None:
    # CSPRNG by design (entropy ceiling calibration); not reproducible,
    # which is fine — every test asserts ranges, never exact bytes.
    with open(path, "wb") as f:
        f.write(os.urandom(MiB))


_WORDS = (
    "the of and to in is that it was for on are as with his they at be this "
    "have from or one had by word but not what all were we when your can said "
    "there use an each which she do how their if will up other about out many "
    "then them these so some her would make like him into time has look two "
    "more write go see number no way could people my than first water been "
    "called who oil its now find long down day did get come made may part over "
    "new sound take only little work know place year live me back give most "
    "very after thing our just name good sentence man think say great where "
    "help through much before line right too mean old any same tell boy follow "
    "came want show also around form three small set put end does another well "
    "large must big even such because turn here why ask went men read need land "
    "different home us move try kind hand picture again change off play spell "
    "air away animal house point page letter mother answer found study still "
    "learn should america world"
).split()

_PUNCT_EVERY = 9  # roughly one comma/semicolon per 9 words


def gen_ascii(path: str) -> None:
    """~1 MiB of deterministic pseudo-English prose."""
    rng = np.random.default_rng(1889)
    out: list[str] = []
    total = 0
    while total < MiB:
        n_words = int(rng.integers(6, 15))
        words = [_WORDS[int(i)] for i in rng.integers(0, len(_WORDS), n_words)]
        for j in range(_PUNCT_EVERY, n_words, _PUNCT_EVERY):
            words[j - 1] += "," if rng.random() < 0.8 else ";"
        if rng.random() < 0.12:
            words.insert(int(rng.integers(1, n_words)), str(int(rng.integers(0, 2000))))
        sentence = " ".join(words).capitalize() + ("." if rng.random() < 0.9 else "?")
        out.append(sentence)
        total += len(sentence) + 1
    text = ""
    # rewrap to ~72-char lines
    line = ""
    chunks = " ".join(out).split(" ")
    lines = []
    for w in chunks:
        if len(line) + len(w) + 1 > 72:
            lines.append(line)
            line = w
        else:
            line = w if not line else line + " " + w
    lines.append(line)
    text = "\n".join(lines) + "\n"
    with open(path, "w", newline="\n", encoding="ascii") as f:
        f.write(text[: MiB + 4096])


PATTERN = bytes(range(0x00, 0x100, 0x11))  # 16 distinct bytes: 00 11 22 .. FF


def gen_pattern(path: str) -> None:
    reps = (256 * 1024) // len(PATTERN)
    with open(path, "wb") as f:
        f.write(PATTERN * reps)


REPEAT_BLOCK = 64 * 1024
REPEAT_BLOCK_OFFSETS = [1 * REPEAT_BLOCK, 3 * REPEAT_BLOCK, 5 * REPEAT_BLOCK]


def gen_repeats(path: str) -> None:
    """sep0 | block | sep1 | block | sep2 | block | sep3 — 448 KiB total."""
    rng = np.random.default_rng(4242)
    block = rng.integers(0, 256, REPEAT_BLOCK, dtype=np.uint8).tobytes()
    parts = []
    for i in range(3):
        parts.append(rng.integers(0, 256, REPEAT_BLOCK, dtype=np.uint8).tobytes())
        parts.append(block)
    parts.append(rng.integers(0, 256, REPEAT_BLOCK, dtype=np.uint8).tobytes())
    with open(path, "wb") as f:
        f.write(b"".join(parts))


def gen_ramp16(path: str) -> None:
    vals = (np.arange(131072, dtype=np.uint32) % 65536).astype("<u2")
    vals.tofile(path)


def gen_floats(path: str) -> None:
    n = 256 * 1024
    t = np.arange(n, dtype=np.float64)
    sig = np.sin(2 * np.pi * 300.0 * t / n).astype("<f4")
    sig.tofile(path)


def pack_u12(vals: np.ndarray) -> bytes:
    """Pack pairs of 12-bit values into 3 bytes: [a>>4][(a&15)<<4|b>>8][b&255].

    This is the packing convention Phase 2's elements() must match; it is
    recorded in manifest.json as `u12_packing`.
    """
    v = vals.astype(np.uint16).reshape(-1, 2)
    a, b = v[:, 0], v[:, 1]
    out = np.empty((len(a), 3), dtype=np.uint8)
    out[:, 0] = a >> 4
    out[:, 1] = ((a & 0xF) << 4) | (b >> 8)
    out[:, 2] = b & 0xFF
    return out.tobytes()


BAYER_W, BAYER_H = 640, 480


def gen_bayer(path: str) -> None:
    """640x480 12-bit RGGB mosaic of a smooth synthetic gradient scene.

    Scene: R ramps with x, G ramps with y, B ramps with (x+y)/2 — smooth
    everywhere, so demosaicing with the correct CFA phase is smooth while any
    wrong phase mixes channels and checkerboards.
    """
    x = np.linspace(0, 4095, BAYER_W)
    y = np.linspace(0, 4095, BAYER_H)
    xx, yy = np.meshgrid(x, y)
    r = xx
    g = yy
    b = (xx + yy) / 2
    mosaic = np.empty((BAYER_H, BAYER_W))
    mosaic[0::2, 0::2] = r[0::2, 0::2]  # R at even row, even col
    mosaic[0::2, 1::2] = g[0::2, 1::2]  # G
    mosaic[1::2, 0::2] = g[1::2, 0::2]  # G
    mosaic[1::2, 1::2] = b[1::2, 1::2]  # B
    vals = np.clip(np.round(mosaic), 0, 4095).astype(np.uint16).ravel()
    with open(path, "wb") as f:
        f.write(pack_u12(vals))


RGB_W, RGB_H = 320, 240
RGB_BARS = [
    (255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
    (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0),
]


def gen_rgb(path: str) -> None:
    """320x240 RGB8 colour bars, 8 vertical bars of 40 px."""
    img = np.zeros((RGB_H, RGB_W, 3), dtype=np.uint8)
    for i, colour in enumerate(RGB_BARS):
        img[:, i * 40 : (i + 1) * 40] = colour
    with open(path, "wb") as f:
        f.write(img.tobytes())


def gen_png(path: str) -> None:
    from PIL import Image

    rng = np.random.default_rng(7)
    grad = np.linspace(0, 255, 256, dtype=np.uint8)
    img = np.stack(
        [
            np.tile(grad, (256, 1)),
            np.tile(grad[:, None], (1, 256)),
            rng.integers(0, 256, (256, 256), dtype=np.uint8),
        ],
        axis=-1,
    )
    Image.fromarray(img, "RGB").save(path, "PNG")


def gen_zip(path: str) -> None:
    ascii_path = os.path.join(OUT, "ascii.txt")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(ascii_path, "ascii.txt")
        z.writestr("notes/readme.md", "corpus control sample\n" * 200)


# ----------------------------------------------------------------- compiled

X64_GNU = ["-target", "x86_64-linux-gnu"]
X64_MUSL = ["-target", "x86_64-linux-musl"]


def cc(src: str, out: str, flags: list[str]) -> None:
    run(zig(["cc"] + flags + ["-o", out, os.path.join(SRC, src)]))


def gen_hello_O0(path: str) -> None:
    cc("hello.c", path, X64_GNU + ["-O0"])


def gen_hello_O2(path: str) -> None:
    cc("hello.c", path, X64_GNU + ["-O2"])


def gen_hello_static(path: str) -> None:
    cc("hello.c", path, X64_MUSL + ["-O2", "-static"])


def gen_hello_stripped(path: str) -> None:
    """Stripped twin of hello_O2 with identical code addresses.

    `zig objcopy --strip-all` is unimplemented for dynamic ELFs, so instead
    relink with the exact hello_O2 flags plus -Wl,-s: lld is deterministic
    and stripping only drops the non-alloc .symtab/.strtab, so every alloc
    section (and e_entry) lands at the same address as the unstripped twin.
    Tests assert that identity.
    """
    cc("hello.c", path, X64_GNU + ["-O2", "-Wl,-s"])


def gen_hello_upx(path: str) -> None:
    upx = find_upx()
    if upx is None:
        raise RuntimeError(
            "upx not found: set $UPX, install upx, or unzip a release "
            "into corpus/tools/upx-*/"
        )
    if os.path.exists(path):
        os.remove(path)  # upx refuses to overwrite
    run([upx, "-9", "-o", path, os.path.join(OUT, "hello_static")])


def gen_switchy(path: str) -> None:
    cc("switchy.c", path, X64_GNU + ["-O2"])


def gen_hello_arm64(path: str) -> None:
    cc("hello.c", path, ["-target", "aarch64-linux-musl", "-O2", "-static"])


def gen_hello_thumb(path: str) -> None:
    cc("hello.c", path, ["-target", "arm-linux-musleabihf", "-O2", "-mthumb", "-static"])


def gen_hello_pe(path: str) -> None:
    cc("hello.c", path, ["-target", "x86_64-windows-gnu", "-O2"])


# -------------------------------------------------------------------- main

# name -> (generator, required, dependencies)
SAMPLES: dict[str, tuple] = {
    "zeros.bin": (gen_zeros, True, []),
    "urandom.bin": (gen_urandom, True, []),
    "ascii.txt": (gen_ascii, True, []),
    "pattern.bin": (gen_pattern, True, []),
    "repeats.bin": (gen_repeats, True, []),
    "ramp16.bin": (gen_ramp16, True, []),
    "floats.bin": (gen_floats, True, []),
    "bayer_raw.bin": (gen_bayer, True, []),
    "rgb_raw.bin": (gen_rgb, True, []),
    "sample.png": (gen_png, True, []),
    "sample.zip": (gen_zip, True, ["ascii.txt"]),
    "hello_O0": (gen_hello_O0, True, []),
    "hello_O2": (gen_hello_O2, True, []),
    "hello_static": (gen_hello_static, True, []),
    "hello_stripped": (gen_hello_stripped, True, []),
    "hello_upx": (gen_hello_upx, True, ["hello_static"]),
    "switchy": (gen_switchy, True, []),
    "hello_arm64": (gen_hello_arm64, False, []),
    "hello_thumb": (gen_hello_thumb, False, []),
    "hello_pe.exe": (gen_hello_pe, False, []),
}


def build(names: list[str], force: bool) -> int:
    os.makedirs(OUT, exist_ok=True)
    done: set[str] = set()
    failed: list[str] = []

    def make(name: str) -> bool:
        if name in done:
            return True
        gen, required, deps = SAMPLES[name]
        for d in deps:
            if not make(d):
                print(f"[skip] {name}: dependency {d} failed")
                failed.append(name)
                done.add(name)
                return False
        path = os.path.join(OUT, name)
        if os.path.exists(path) and not force:
            print(f"[ok  ] {name} (cached)")
            done.add(name)
            return True
        try:
            gen(path)
            print(f"[built] {name} ({os.path.getsize(path)} bytes)")
        except Exception as e:
            tag = "FAIL" if required else "skip-optional"
            print(f"[{tag}] {name}: {e}")
            if required:
                failed.append(name)
            done.add(name)
            return False
        done.add(name)
        return True

    for name in names:
        make(name)

    required_failed = [n for n in failed if SAMPLES[n][1]]
    if required_failed:
        print(f"\nFAILED required samples: {', '.join(required_failed)}")
        return 1
    print(f"\ncorpus complete: {len(done) - len(failed)} samples in {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="rebuild even if present")
    ap.add_argument("--only", action="append", help="build only these samples")
    args = ap.parse_args()
    names = args.only if args.only else list(SAMPLES)
    unknown = [n for n in (names or []) if n not in SAMPLES]
    if unknown:
        ap.error(f"unknown samples: {unknown}; known: {list(SAMPLES)}")
    return build(names, args.force)


if __name__ == "__main__":
    sys.exit(main())
