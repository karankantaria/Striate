"""Desktop window and the js_api bridge (§2.4).

§2.4 is not a bug report, it is a set of constraints on code that did not
exist yet. Constraints like that erode silently — someone adds "just one
more" bridge method a year from now and nothing complains — so each one is
pinned here.

The premise, restated because it is what makes these tests worth having:
any JavaScript running in the desktop window can call
`pywebview.api.<method>()`, and this tool's entire purpose is opening files
an attacker chose. Every public method on the bridge is therefore reachable
from a hostile section name that survives escaping. The surface is the
attack surface.
"""

import inspect
import re
from pathlib import Path

import pytest

from binviz import app as app_mod
from binviz.cli import main as cli_main

SRC = Path(__file__).resolve().parents[1] / "src" / "binviz"


# ------------------------------------------------- the js_api surface

def public_methods(obj_or_cls):
    """What pywebview would expose: every non-underscore attribute."""
    return sorted(n for n in dir(obj_or_cls) if not n.startswith("_"))


def test_the_bridge_exposes_exactly_one_method():
    """§2.4: keep the js_api surface minimal.

    If this fails because you added a method, the question to answer is not
    "is it useful" but "what does this let a hostile section name do".
    """
    assert public_methods(app_mod.Bridge) == ["pick_file"]


def test_the_bridge_takes_no_arguments():
    """§2.4 names it explicitly: expose nothing that takes a path.

    A no-argument method cannot be steered at all, which is a stronger
    property than validating a path argument would be.
    """
    sig = inspect.signature(app_mod.Bridge.pick_file)
    assert list(sig.parameters) == ["self"], sig


def test_the_bridge_state_is_private():
    """Attributes are exposed too, not just methods — a public `_root`
    would hand out the served root, and a public window handle would hand
    out the whole pywebview API."""
    bridge = app_mod.Bridge(lambda: None, "/some/root")
    assert public_methods(bridge) == ["pick_file"]


def test_the_bridge_spawns_no_subprocess_and_opens_no_file():
    """§2.4's other explicit prohibition. Checked against the source rather
    than by mocking, because the point is that the capability is absent,
    not that one code path avoids it."""
    src = (SRC / "app.py").read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)      # drop docstrings
    code = re.sub(r"#.*$", "", code, flags=re.M)   # and comments
    for banned in ("subprocess", "os.system", "os.popen", "eval(", "exec(",
                   "open("):
        assert banned not in code, f"the bridge module reaches for {banned!r}"


def test_pick_file_returns_nothing_without_a_window():
    # the only reachable path in a headless test, and it must not raise:
    # an exception here would surface as an unhandled rejection in the UI
    assert app_mod.Bridge(lambda: None, None).pick_file() is None


def test_a_second_dialog_cannot_be_opened_while_one_is_open():
    """Injected script could otherwise open dialogs in a loop and make the
    window unusable. Not a breach, but a denial of service the user cannot
    escape without killing the app."""
    calls = []

    class FakeWindow:
        def create_file_dialog(self, *a, **kw):
            calls.append(kw)
            # re-enter exactly as looping script would
            assert bridge.pick_file() is None, "re-entered the dialog"
            return None

    bridge = app_mod.Bridge(lambda: FakeWindow(), None)
    pytest.importorskip("webview", reason="pick_file imports webview")
    bridge.pick_file()
    assert len(calls) == 1


def test_the_dialog_opens_at_the_served_root():
    """§3.1: default the native dialog to --root, so the common case does
    not immediately 403 at the confinement layer."""
    seen = {}

    class FakeWindow:
        def create_file_dialog(self, kind, **kw):
            seen.update(kw)
            return None

    pytest.importorskip("webview", reason="pick_file imports webview")
    app_mod.Bridge(lambda: FakeWindow(), "/served/root").pick_file()
    assert seen.get("directory") == "/served/root"
    assert seen.get("allow_multiple") is False


# --------------------------------------------------------- the command

def test_devtools_are_off_unless_asked_for():
    """§2.4: `debug=True` enables devtools in the shipped app. The default
    must be off, and turning it on must be a deliberate act."""
    src = (SRC / "cli.py").read_text(encoding="utf-8")
    assert 'start_kw = {"debug": bool(args.devtools)}' in src, \
        "debug is no longer derived from the explicit --devtools flag"
    assert 'p_app.add_argument("--devtools", action="store_true"' in src, \
        "--devtools is no longer opt-in"


def test_the_desktop_build_cannot_disable_authentication():
    """§2.4: a window does not remove the network listener, it only makes it
    less obvious there is one. So `binviz app` has no --no-auth, and the
    server it starts is always authenticated."""
    src = (SRC / "cli.py").read_text(encoding="utf-8")
    app_block = src[src.index('sub.add_parser(\n        "app"'):
                    src.index('p_passwd = sub.add_parser')]
    # comments stripped: the block explains *why* --no-auth is absent, and a
    # naive scan would read that explanation as the offence
    app_block = re.sub(r"#.*$", "", app_block, flags=re.M)
    assert "--no-auth" not in app_block
    assert "auth=True" in src[src.index("def _cmd_app"):]


def test_app_falls_back_to_a_browser_without_pywebview(monkeypatch, tmp_path,
                                                       capsys):
    """ARCHITECTURE.md §2: pywebview is an optional extra, so the base install
    must still be able to open the app."""
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    started = {}

    def fake_serve(app, host, port):
        started["app"] = app
        thread = type("T", (), {"join": lambda self: None,
                                "is_alive": lambda self: False})()
        return object(), thread, 54321

    monkeypatch.setattr(app_mod, "serve_in_thread", fake_serve)
    monkeypatch.setitem(__import__("sys").modules, "webview", None)

    rc = cli_main(["app", "--cache", str(tmp_path), "--root", str(tmp_path),
                   "--browser"])
    assert rc == 0
    assert opened == ["http://127.0.0.1:54321/"], opened
    # and the fallback is still authenticated — same rule as the window
    assert started["app"].state.auth_token


def test_app_refuses_to_open_a_window_with_no_ui(monkeypatch, tmp_path,
                                                 capsys):
    """Without the staged bundle the window would open on a JSON 404 and
    look like a broken app rather than a missing build step (§4.1)."""
    def fake_serve(app, host, port):
        app.state.ui_root = None          # simulate a wheel with no UI
        thread = type("T", (), {"join": lambda self: None,
                                "is_alive": lambda self: False})()
        return object(), thread, 54321

    monkeypatch.setattr(app_mod, "serve_in_thread", fake_serve)
    rc = cli_main(["app", "--cache", str(tmp_path), "--root", str(tmp_path)])
    assert rc == 1
    assert "no packaged UI" in capsys.readouterr().err


def test_the_window_icon_is_a_format_this_platform_can_load():
    """Not cosmetic. Handing the Windows backend a PNG throws
    `System.ArgumentException` from inside .NET — an unhandled exception on
    a foreign thread that kills the process before Python sees it, so the
    app simply fails to open. Found by launching an installed wheel."""
    import sys as _sys

    path = app_mod.icon_path()
    if path is None:
        pytest.skip("no staged icon; run tools/build_ui.py")
    if _sys.platform == "win32":
        assert path.endswith(".ico"), path
    else:
        assert path.endswith(".png"), path


def test_the_command_says_a_server_is_running():
    """§2.4: a desktop wrapper makes the user *less* likely to realise there
    is a listener, because there is no terminal and no tab. The one place
    there is still a terminal must not stay quiet about it."""
    src = (SRC / "cli.py").read_text(encoding="utf-8")
    body = src[src.index("def _cmd_app"):]
    assert "a local server is running at" in body


# --------------------------------------------------------- the listener

def test_the_server_thread_reports_the_port_it_actually_bound(tmp_path):
    """Port 0 is the default, so the URL is not known until the socket is
    bound. Pointing the window at the wrong port shows an error page the
    user cannot diagnose."""
    from binviz.service import create_app

    app = create_app(tmp_path, file_root=str(tmp_path))
    server, thread, port = app_mod.serve_in_thread(app, "127.0.0.1", 0)
    try:
        assert 1024 < port < 65536, port
        import urllib.request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/config", timeout=5) as r:
            pytest.fail(f"unauthenticated request succeeded: {r.status}")
    except urllib.error.HTTPError as e:
        # the whole point of §2.4: the desktop listener is a real listener
        # and it is still gated
        assert e.code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=5)
