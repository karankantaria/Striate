"""Desktop window (`binviz app`) — ARCHITECTURE.md §2, §2.4.

The app is the existing FastAPI service plus the packaged frontend in one
process, with a native window pointed at it. Read §2.4 before changing
anything here; the short version is that a desktop wrapper makes two things
*worse*, not better, and both are handled deliberately below.

**1. The network listener does not go away.** Wrapping the UI in a window
still means an ordinary TCP listener that every browser and process on the
machine can reach — and the user is now *less* likely to realise it, because
there is no terminal and no tab. So the desktop build keeps the token
(§S1a). With same-origin serving it costs the user nothing: the server
injects it into the page it serves and nobody ever sees it. `binviz app`
therefore has no `--no-auth`, unlike `binviz serve`.

The port defaults to 0 (the OS picks a free one), which avoids clashing with
a `binviz serve` already running. That is **not** a security control — a
malicious page can scan localhost with timed `fetch` — it is just politeness
about ports. The token is the control.

**2. A `js_api` bridge turns XSS into remote code execution.** Any
JavaScript in the window can call `pywebview.api.<method>()`, and this tool's
entire purpose is opening files an attacker chose. If a hostile section name
survived escaping, every public method here would be reachable from it. A
malware triage tool where analysing malware runs the malware is the worst
failure mode available, so the bridge is exactly as small as it can be while
still being useful: see `Bridge`.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

WINDOW_TITLE = "Striate"
#: Room for the ten-pane "All" workspace without immediately scrolling.
WINDOW_SIZE = (1440, 900)
WINDOW_MIN_SIZE = (900, 600)


def _open_dialog(webview):
    """The "open a file" dialog constant, across the supported pywebview.

    6.x renamed it to `FileDialog.OPEN` and made `OPEN_DIALOG` print a
    deprecation warning on import-use; 5.x only has the old name. The extra
    is `pywebview>=5,<7`, so both have to work — and a deprecation warning
    on stderr of a GUI app is a warning nobody will ever read.
    """
    dialogs = getattr(webview, "FileDialog", None)
    return dialogs.OPEN if dialogs is not None else webview.OPEN_DIALOG


class Bridge:
    """The **entire** `js_api` surface (§2.4).

    pywebview exposes every public attribute of this object to JavaScript,
    so the rule is simple and load-bearing: one method, and everything else
    is underscore-prefixed. Do not add a method here without re-reading
    §2.4 — "is this useful?" is the wrong question, "what does this let a
    hostile section name do?" is the right one.

    Why `pick_file` is safe enough to be the exception:

    - It **takes no arguments**, so nothing can steer it. In particular it
      cannot be handed a path, which §2.4 names explicitly.
    - It spawns no subprocess and touches no file. It opens the OS file
      chooser and returns whatever the human picked.
    - It cannot act on its own: a dialog with nobody in front of it returns
      nothing. Called from injected script the worst case is a dialog the
      user did not ask for — annoying, not dangerous.
    - The path it returns is not privileged. It goes back to the frontend,
      which POSTs it to `/api/open`, where `--root` confinement (S1d)
      applies exactly as it does to a typed path.

    The re-entrancy guard is there for the annoyance case: without it,
    injected script could open dialogs in a loop and make the window
    unusable.
    """

    def __init__(self, window_getter, root: str | None):
        # Underscored so pywebview does not expose them. `_window_getter`
        # is a callable rather than the window itself because the window
        # does not exist until after this object is constructed.
        self._window_getter = window_getter
        self._root = root
        self._busy = threading.Lock()

    def pick_file(self):
        """Open the native file chooser. Returns an absolute path, or None.

        The desktop half of §3.1. A browser's `<input type="file">`
        deliberately never reveals an absolute path, so the web build has to
        upload the bytes; here the real path is available, which means no
        copy, no upload, and directory navigation ([ / ]) keeps working.
        """
        if not self._busy.acquire(blocking=False):
            return None          # a dialog is already open
        try:
            window = self._window_getter()
            if window is None:
                return None
            try:
                import webview
            except ImportError:
                # Unreachable in a real desktop run — the bridge only exists
                # once pywebview imported. Returning None rather than
                # raising anyway, because an exception here crosses the
                # bridge as an unhandled rejection in the page.
                return None
            picked = window.create_file_dialog(
                _open_dialog(webview),
                # Open where the server can actually read from, so the
                # common case does not immediately 403 (§3.1).
                directory=self._root or "",
                allow_multiple=False,
            )
            if not picked:
                return None      # cancelled
            return os.path.abspath(picked[0])
        finally:
            self._busy.release()


def icon_path() -> str | None:
    """The packaged window icon in a format *this platform's* backend can
    load, or None.

    The format matters and getting it wrong is not a soft failure. Handing
    the Windows backend a PNG throws `System.ArgumentException: Argument
    'picture' must be a picture that can be used as a Icon` from inside
    .NET — an unhandled exception on a foreign thread that kills the
    process before any Python `except` can see it. So the file is chosen by
    platform rather than passed hopefully:

        Windows   .ico   (System.Drawing.Icon accepts nothing else)
        else      .png   (GTK and Qt both load it)

    Found the hard way, by launching the app from an installed wheel.
    """
    wanted = ("icon.ico",) if sys.platform == "win32" else ("icon-256.png",)
    for base in (Path(__file__).resolve().parent / "icons",
                 Path(__file__).resolve().parents[2] / "packaging" / "icons"):
        for name in wanted:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return None


def serve_in_thread(app, host: str, port: int):
    """Start uvicorn on a daemon thread and return (server, thread, port).

    The webview has to own the main thread — some backends require it — so
    the server goes to the side. Signal handlers are disabled because
    installing them off the main thread is an error; the window closing is
    what ends the process, and the thread is a daemon so a hard exit cannot
    hang on it.

    Returns the port the OS actually bound, which matters because the
    default is 0.
    """
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    thread = threading.Thread(target=server.run, daemon=True,
                              name="binviz-uvicorn")
    thread.start()

    # Wait for the socket, not for a fixed sleep: with port=0 we do not know
    # the URL until it is bound, and pointing the window at the wrong port
    # shows an error page the user cannot diagnose.
    deadline = threading.Event()
    bound = None
    for _ in range(600):                      # 30 s, generous for a cold start
        if server.started and getattr(server, "servers", None):
            socks = server.servers[0].sockets
            if socks:
                bound = socks[0].getsockname()[1]
                break
        if not thread.is_alive():
            raise RuntimeError("the server thread exited before binding")
        deadline.wait(0.05)
    if bound is None:
        raise RuntimeError("the server did not start within 30 s")
    return server, thread, bound
