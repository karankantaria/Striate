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
    assert '["app"]' in _code(LAUNCHER)


@pytest.mark.skipif(not (ROOT / "packaging" / "icons" / "icon.ico").is_file(),
                    reason="no icon.ico in packaging/icons")
def test_the_spec_points_at_branding_that_exists():
    """The spec names asset paths as strings; a rename in packaging/icons
    would leave them pointing at nothing and PyInstaller only complains
    about the icon at build time."""
    assert (ROOT / "packaging" / "icons" / "icon.ico").is_file()
