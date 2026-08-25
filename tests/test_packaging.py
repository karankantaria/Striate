"""The PyInstaller spec (RELEASE.md §5.1).

Nothing here builds anything — PyInstaller is not a test dependency and a
99 MB freeze does not belong in a suite that runs on every change. What
these tests pin is the set of choices in the spec that are *arguments*
rather than defaults, because an argument that lives only in a comment
gets flipped by the next person who runs `pyi-makespec` and pastes the
result over it.

Each choice is the same shape: the safe value is the one that costs
something (disk, a console window, build time), so drift has a direction.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "binviz.spec"
LAUNCHER = ROOT / "packaging" / "launcher.py"


def _code(path: Path) -> str:
    """Source with docstrings and comments stripped — the prose here argues
    *for* the constraints, so a naive scan would read the reasoning as the
    violation."""
    src = path.read_text(encoding="utf-8")
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return re.sub(r"#.*$", "", src, flags=re.M)


def test_the_spec_exists():
    """§1 promises you can build the desktop app yourself, and §5.1 is that
    promise's only moving part: no spec, no build, and the promise is a
    paragraph in a README."""
    assert SPEC.is_file(), "packaging/binviz.spec is missing (RELEASE.md §5.1)"


def test_the_build_is_onedir():
    """onefile self-extracts ~99 MB to a temp directory on every launch —
    slow, and the behaviour heuristic AV scores as suspicious, which is the
    same objection that keeps prebuilt executables off releases (§1)."""
    code = _code(SPEC)
    assert "COLLECT(" in code, "no COLLECT: this is a onefile build"
    assert "exclude_binaries=True" in code, \
        "EXE without exclude_binaries=True bundles everything into one file"


def test_nothing_is_upx_packed():
    """A tool whose own corpus uses UPX as the example of a packed sample
    should not ship UPX-packed, to save disk on a build nobody downloads."""
    code = _code(SPEC)
    assert "upx=True" not in code
    assert code.count("upx=False") >= 2, "EXE and COLLECT must both set it"


def test_the_app_keeps_its_console():
    """§2.4: a desktop wrapper makes the user *less* likely to realise a
    network listener is running, so the one place there is still a terminal
    must not stay quiet. console=False discards that banner — and the
    output of every non-`app` subcommand with it, since a windowed build
    has no stdout at all."""
    assert "console=True" in _code(SPEC)


def test_the_windows_icon_is_the_ico():
    """Same reason as `binviz app`'s window icon: the format is not
    cosmetic. PyInstaller's `icon=` is the executable's resource, and .ico
    is the only thing Windows takes."""
    assert 'icon.ico"' in SPEC.read_text(encoding="utf-8")


def test_the_frozen_app_ships_the_ui_and_refuses_to_build_without_it():
    """§4.1, one layer down. `web/dist` is outside the Python package, so a
    freeze that skips tools/build_ui.py produces an app that opens a window
    on a JSON 404 — and does it silently. The wheel has the release
    workflow to catch that; a local freeze has only the spec."""
    code = _code(SPEC)
    assert '"binviz/webui"' in code, "the frontend is not bundled"
    assert '"binviz/icons"' in code, "the window icon is not bundled"
    assert "raise SystemExit(" in code and 'webui" / "index.html"' in code, \
        "a freeze with no staged frontend must fail loudly, not ship broken"


def test_the_frozen_app_ships_its_calibration():
    """The wheel's calibration bug (§5), one build system over. `corpus/`
    is not collected, so without an explicit datas entry the frozen app
    resolves no calibration and falls back to `_FALLBACK_CAL` — analysing
    on `code_h_lo` 4.5 against the measured 5.31, and disagreeing with a
    pip-installed binviz about the same file.

    It is asserted here because the obvious manual test cannot see it:
    `_find_calibration()` checks `$CWD/corpus/` before the packaged copy,
    so a bundle launched from the repo root picks up the real thresholds
    and looks fine. Only a launch from somewhere else exposes it, and that
    is the double-click case.
    """
    code = _code(SPEC)
    assert '"calibration.json"' in code,         "the frozen app would analyse on fallback thresholds (RELEASE.md §5)"
    assert "raise SystemExit(" in code and 'PKG / "calibration.json"' in code,         "a freeze with no staged calibration must fail loudly, not ship wrong"


def test_the_native_dependencies_are_collected():
    """Neither survives static import analysis: capstone ships a DLL beside
    its wrapper, lief resolves lief.PE/lief.ELF at runtime, and uvicorn
    picks its protocol and loop implementations by string at startup."""
    code = _code(SPEC)
    for dist in ("capstone", "lief"):
        assert f'bundle("{dist}"' in code, f"{dist} is not collected"
    assert 'collect_submodules("uvicorn")' in code


def test_the_launcher_is_the_same_program():
    """§2.4's reasoning about the desktop build only holds if the desktop
    build *is* binviz. The launcher exists because a frozen app has no
    console-script entry point — not to become a second front end with its
    own defaults."""
    code = _code(LAUNCHER)
    assert "from binviz.cli import main" in code
    assert "--no-auth" not in code, \
        "the frozen build must not be able to disable authentication (§2.4)"
    assert "--root" not in code, \
        "the launcher must not widen the confinement default (S1d)"


def test_a_double_clicked_executable_opens_the_window():
    """No argv at all is the double-click case; passing it through
    unchanged exits 2 with argparse usage text, into a console window that
    closes before anyone can read it."""
    assert '"app"' in _code(LAUNCHER)


def test_a_double_clicked_executable_asks_for_the_credential():
    """§2.4: the window is the launch path with no terminal behind it, so
    it is the one that must not sign the user in silently. `binviz app`
    typed into a shell keeps its own `--auth none` default — this pins the
    *double-click* default only, which is why it lives in the launcher
    rather than in the parser."""
    code = _code(LAUNCHER)
    assert '"--auth", "local"' in code, \
        "a double-clicked executable must show the sign-in screen (§2.4)"
    assert "sys.argv[1:]" in code and "argv if argv" in code, \
        "an explicit --auth on the command line must still win"


@pytest.mark.skipif(not (ROOT / "packaging" / "icons" / "icon.ico").is_file(),
                    reason="no icon.ico in packaging/icons")
def test_the_spec_points_at_branding_that_exists():
    """The spec names asset paths as strings; a rename in packaging/icons
    would leave them pointing at nothing and PyInstaller only complains
    about the icon at build time."""
    assert (ROOT / "packaging" / "icons" / "icon.ico").is_file()


# ------------------------------------------------------- the macOS icon
#
# `icon.icns` is written by tools/make_icns.py rather than by `iconutil`,
# which runs on macOS and nowhere else. That is only defensible if the
# result is what `iconutil` would have produced, so the container is
# checked here rather than trusted — the machine that would notice a
# malformed one is the machine we do not have.

ICNS = ROOT / "packaging" / "icons" / "icon.icns"

#: What `iconutil -c icns` emits from a standard .iconset: five logical
#: sizes, each at 1x and 2x. An icon missing its @2x halves is the one
#: that looks soft on every Mac sold in the last decade.
ICNS_ENTRIES = {
    b"icp4": 16, b"ic11": 32, b"icp5": 32, b"ic12": 64,
    b"ic07": 128, b"ic13": 256, b"ic08": 256, b"ic14": 512,
    b"ic09": 512, b"ic10": 1024,
}


def _walk_icns(raw: bytes):
    """(type, payload) pairs, asserting the container agrees with itself."""
    import struct

    assert raw[:4] == b"icns", "not an icns container"
    declared = struct.unpack(">I", raw[4:8])[0]
    assert declared == len(raw), f"header says {declared}, file is {len(raw)}"
    out, off = [], 8
    while off < len(raw):
        code = raw[off:off + 4]
        length = struct.unpack(">I", raw[off + 4:off + 8])[0]
        assert 8 < length <= len(raw) - off, f"{code!r} length {length}"
        out.append((code, raw[off + 8:off + length]))
        off += length
    assert off == len(raw), "trailing bytes after the last entry"
    return out


def test_the_macos_icon_exists():
    """§3's inventory lists it; the spec's darwin branch reaches for it."""
    assert ICNS.is_file(), "run python tools/make_icns.py"


def test_the_icns_container_is_well_formed():
    """A length that disagrees with the file is the failure mode of writing
    this by hand, and Finder's response to it is to show a generic icon
    with no diagnostic anywhere."""
    entries = _walk_icns(ICNS.read_bytes())
    assert [c for c, _ in entries] == list(ICNS_ENTRIES), \
        "entry set or order differs from iconutil's"


def test_every_icns_entry_is_a_png_of_the_size_its_code_promises():
    """The type code *is* the size declaration — nothing in the entry
    header repeats it, so a mismatched payload is undetectable until macOS
    draws the wrong thing."""
    import io

    from PIL import Image

    for code, payload in _walk_icns(ICNS.read_bytes()):
        im = Image.open(io.BytesIO(payload))
        assert im.format == "PNG", (code, im.format)
        assert im.size == (ICNS_ENTRIES[code],) * 2, (code, im.size)
        # flat colour on a rounded field: without alpha the corners are
        # black squares on every dark Dock
        assert im.mode == "RGBA", (code, im.mode)


def test_the_icns_is_the_same_artwork_as_the_rest_of_the_branding():
    """§3: `icon.svg` is the master and everything else is derived. The
    1024px entry is the master re-encoded, not resampled, so any drift
    between the two means someone edited one copy of the mark — the exact
    failure §3 keeps a single canonical set to avoid."""
    import io

    from PIL import Image, ImageChops

    master = Image.open(ROOT / "packaging" / "icons" / "icon-1024.png")
    entries = dict(_walk_icns(ICNS.read_bytes()))
    biggest = Image.open(io.BytesIO(entries[b"ic10"]))
    diff = ImageChops.difference(master.convert("RGBA"), biggest.convert("RGBA"))
    assert diff.getbbox() is None, \
        "icon.icns and icon-1024.png are different images"


def test_the_mac_bundle_uses_it():
    code = _code(SPEC)
    assert 'BUNDLE(' in code and '"icon.icns"' in code
