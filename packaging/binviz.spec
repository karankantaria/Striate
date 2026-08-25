# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the binviz desktop app (ARCHITECTURE.md §2).

    pip install pyinstaller
    python tools/build_ui.py            # MUST run first — see below
    pyinstaller packaging/binviz.spec   # -> dist/binviz/

This is **not** the release artifact. ARCHITECTURE.md §1 is explicit: releases
ship a wheel, because an unsigned frozen Python executable that bundles
capstone and lief and exists to dissect packed binaries is the exact
profile SmartScreen and AV heuristics false-positive on. This spec is the
other half of that promise — you can build the desktop app yourself, on
your own machine, and sidestep code signing entirely.

Three decisions here are load-bearing rather than taste:

**onedir, not onefile.** onefile is a self-extracting archive: every launch
unpacks ~150 MB to a temp directory before any code runs. Slow, and it is
also precisely the behaviour heuristic AV scores as suspicious — the same
objection that keeps prebuilt executables off releases in the first place.

**upx=False.** UPX-packing the binary would compress a tool whose own test
corpus uses UPX as its example of a packed sample, to save disk on a
build nobody downloads. The saving is not worth one more reason for a
scanner to quarantine it.

**console=True.** `binviz app` prints that a network listener is running
and where (§2.4: a desktop wrapper makes the user *less* likely to notice
the listener, because there is no terminal and no tab — so the one place
there is still a terminal must not stay quiet). Setting this to False
throws that message away, along with every non-`app` subcommand's output,
since a windowed build has no stdout at all.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller into the spec's namespace — a
# namespace that has no `__file__`, hence the try/except rather than a
# `globals().get(...)` default, whose fallback would be evaluated eagerly
# and raise NameError every time.
try:
    _SPEC_DIR = Path(SPECPATH)          # noqa: F821 — injected by PyInstaller
except NameError:                       # imported as ordinary Python
    _SPEC_DIR = Path(__file__).resolve().parent
ROOT = _SPEC_DIR.resolve().parent
PKG = ROOT / "src" / "binviz"

# --------------------------------------------------------------- the UI

# `web/dist` lives outside the Python package, so the frontend is only in
# `src/binviz/webui/` if tools/build_ui.py has staged it. Freezing without
# that step produces an app that opens a window on a JSON 404 — the §4.1
# failure, wearing a different hat. The wheel build has the release
# workflow to catch this; a local freeze has only this check, so it is
# fatal rather than a warning.
if not (PKG / "webui" / "index.html").is_file():
    raise SystemExit(
        "binviz.spec: no staged frontend at src/binviz/webui/index.html.\n"
        "  Run `python tools/build_ui.py` first — freezing without it "
        "builds an app with no UI, and it fails silently at runtime."
    )

datas = [(str(PKG / "webui"), "binviz/webui")]

# Staged by the same tool. Missing branding is cosmetic (app.icon_path()
# returns None and the window gets the default icon), so this one is not
# fatal — unlike the window icon's *format*, which is not cosmetic at all:
# handing the Windows backend a PNG kills the process from inside .NET
# before Python sees it. That choice lives in app.icon_path(); both files
# ship so it has both to choose from.
if (PKG / "icons").is_dir():
    datas.append((str(PKG / "icons"), "binviz/icons"))
else:
    print("binviz.spec: no staged icons; the window will use the default "
          "icon (run tools/build_ui.py to stage them)")

# Staged by the same tool, and fatal for the same reason the frontend is.
# `corpus/` is not collected, so without this the frozen app resolves no
# calibration and silently falls back to `_FALLBACK_CAL` — `code_h_lo` 4.5
# against the measured 5.31 — classifying windows differently from the
# wheel while claiming to be the same program. Worse, it is *masked* in
# the obvious test: `_find_calibration()` checks `$CWD/corpus/` before the
# packaged copy, so a bundle launched from the repo root finds the real
# thresholds and looks correct. Double-clicked from anywhere else, it does
# not. The thresholds also feed `params_fingerprint()`, so the two builds
# would disagree about which cached analyses are still valid.
if not (PKG / "calibration.json").is_file():
    raise SystemExit(
        "binviz.spec: no staged calibration at src/binviz/calibration.json.\n"
        "  Run `python tools/build_ui.py` first — freezing without it "
        "builds an app that analyses on fallback thresholds, and it says "
        "so nowhere."
    )
datas.append((str(PKG / "calibration.json"), "binviz"))

# MIT, and the app is being redistributed as a binary — ship the text next
# to the executable rather than only inside the wheel metadata (§4.3).
if (ROOT / "LICENSE").is_file():
    datas.append((str(ROOT / "LICENSE"), "."))

# ------------------------------------------------------- the dependencies

binaries = []
hiddenimports = []


def bundle(dist, *, required, why):
    """collect_all() one distribution, tolerating its absence when optional."""
    try:
        d, b, h = collect_all(dist)
    except Exception as exc:                       # not installed, usually
        if required:
            raise SystemExit(
                f"binviz.spec: cannot collect {dist!r} ({exc}).\n"
                f"  {why}\n"
                f"  Install the project into this environment first:\n"
                f"    pip install -e \".[app]\" -c constraints-dev.txt"
            )
        print(f"binviz.spec: {dist} not installed — {why}")
        return
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# The two native parsers. Both carry shared libraries and data files that
# no static import scan can see: capstone ships its own DLL/.so beside the
# Python wrapper, and lief is a compiled extension whose submodules
# (lief.ELF, lief.PE, …) are resolved at runtime.
bundle("capstone", required=True, why="the disassembler")
bundle("lief", required=True, why="the binary parser")

# uvicorn picks its protocol, loop and lifespan implementations by string
# at startup ("h11", "auto", …), so none of them are reachable by import
# analysis. Missing one is a server that fails to bind with a confusing
# traceback, in a window with no console if console=False.
hiddenimports += collect_submodules("uvicorn")

# The CLI imports its subcommand modules inside the functions that use
# them, to keep `binviz probe` from paying for numpy and capstone. Bytecode
# analysis does find function-level imports, but this costs nothing and
# means a new module added the same way cannot go missing.
hiddenimports += collect_submodules("binviz")

# Optional: without pywebview `binviz app` opens the default browser
# instead, so a frozen build missing it still works — it is just not a
# desktop app any more, which is the whole point of freezing it. On
# Windows pywebview reaches WebView2 through pythonnet, whose `clr` module
# and runtime config are invisible to import analysis.
bundle("webview", required=False,
       why="the frozen app will fall back to opening a browser window")
if sys.platform == "win32":
    bundle("clr_loader", required=False, why="pywebview's .NET bridge")
    bundle("pythonnet", required=False, why="pywebview's .NET bridge")
    hiddenimports.append("clr")

# Nothing in binviz imports these; they arrive through other packages'
# optional paths and cost tens of MB each. Pruned rather than shipped
# because §1's whole argument is about how a large opaque binary is
# received, and a GUI toolkit binviz never opens is pure weight.
excludes = [
    "tkinter", "test", "unittest",
    "pytest", "_pytest", "httpx", "iced_x86", "ziglang",
    "matplotlib", "scipy", "pandas", "IPython", "notebook",
    "numpy.f2py", "numpy.distutils", "setuptools", "pip",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir: the rest goes to COLLECT
    name="binviz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # see the module docstring
    console=True,                   # see the module docstring
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "icons" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="binviz",
)

# macOS only. `icon.icns` is committed (derive it again with
# tools/make_icns.py), but the `is_file()` check stays: a .app with a
# generic icon is a cosmetic loss, and refusing to build over one would
# not be. Untested — ARCHITECTURE.md §5 records that everything here was
# verified on Windows.
if sys.platform == "darwin":
    icns = ROOT / "packaging" / "icons" / "icon.icns"
    app = BUNDLE(
        coll,
        name="Striate.app",
        icon=str(icns) if icns.is_file() else None,
        bundle_identifier="io.github.karankantaria.binviz",
        info_plist={
            # The window is the UI; a Dock icon with no menu bar is fine,
            # but a *background* app would have no way to be raised.
            "LSBackgroundOnly": False,
            "NSHighResolutionCapable": True,
        },
    )
