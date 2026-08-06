"""Optional local sign-in (§2.2, §2.3).

The thing these tests are really defending is §2.1: the login screen is not
the security boundary. Every test that proves the form works is paired with
one proving the API is still gated whether or not anyone signed in.
"""

import json
import os
import re
import stat

import pytest
from conftest import authed_client, make_app, require_sample

from binviz import auth as auth_mod
from binviz.service import create_app


def app_local(tmp_path, **kw):
    return create_app(tmp_path, auth_mode="local", file_root=str(tmp_path), **kw)


# ----------------------------------------------------------- credentials

def test_credential_is_never_stored_in_the_clear(tmp_path):
    auth_mod.set_credential(tmp_path, "analyst", "correct horse battery")
    raw = auth_mod.auth_path(tmp_path).read_text(encoding="utf-8")
    assert "correct horse battery" not in raw
    doc = json.loads(raw)
    assert doc["kdf"] == "scrypt"
    # a bare hash of the password would be recoverable at billions of
    # guesses a second; the salt is what stops one rainbow table covering
    # every install of this tool
    assert len(bytes.fromhex(doc["salt"])) == auth_mod.SALT_BYTES
    assert doc["n"] >= 2 ** 14


def test_two_installs_of_the_same_password_do_not_match(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    auth_mod.set_credential(a, "u", "same password here")
    auth_mod.set_credential(b, "u", "same password here")
    ha = json.loads(auth_mod.auth_path(a).read_text())["hash"]
    hb = json.loads(auth_mod.auth_path(b).read_text())["hash"]
    assert ha != hb, "per-install salt is not being applied"


@pytest.mark.skipif(os.name == "nt",
                    reason="POSIX mode bits are advisory on Windows")
def test_credential_file_is_not_world_readable(tmp_path):
    p = auth_mod.set_credential(tmp_path, "u", "a password")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_verify_accepts_only_the_right_pair(tmp_path):
    auth_mod.set_credential(tmp_path, "analyst", "a good password")
    assert auth_mod.verify(tmp_path, "analyst", "a good password")
    assert not auth_mod.verify(tmp_path, "analyst", "a good passwore")
    assert not auth_mod.verify(tmp_path, "someone", "a good password")
    assert not auth_mod.verify(tmp_path, "analyst", "")


def test_no_credential_means_no_accidental_pass(tmp_path):
    # the failure that would matter most: an install with no credential
    # must not verify *everything*
    assert not auth_mod.verify(tmp_path, "", "")
    assert not auth_mod.verify(tmp_path, "root", "root")


def test_weak_credentials_are_refused(tmp_path):
    with pytest.raises(auth_mod.AuthError):
        auth_mod.set_credential(tmp_path, "u", "short")
    with pytest.raises(auth_mod.AuthError):
        auth_mod.set_credential(tmp_path, "  ", "a long enough password")


# ---------------------------------------------------------------- modes

def test_default_mode_injects_a_token_into_the_page(tmp_path):
    """`none` mode: one click, still authenticated on the wire (§2.2)."""
    app = create_app(tmp_path, file_root=str(tmp_path))
    with authed_client(app) as c:
        r = c.get("/")
        if r.status_code == 404:
            pytest.skip("no packaged UI staged; run tools/build_ui.py")
        assert r.status_code == 200
        boot = _boot_of(r.text)
        assert boot["auth_mode"] == "none"
        assert boot["token"] == app.state.auth_token


def test_local_mode_does_not_put_the_token_in_the_page(tmp_path):
    """The whole point of `local`: the page must not hand out the token,
    or the login screen would be pure theatre."""
    app = app_local(tmp_path)
    with authed_client(app) as c:
        r = c.get("/")
        if r.status_code == 404:
            pytest.skip("no packaged UI staged; run tools/build_ui.py")
        boot = _boot_of(r.text)
        assert boot["auth_mode"] == "local"
        assert "token" not in boot
        assert app.state.auth_token not in r.text


def _boot_of(html: str) -> dict:
    m = re.search(r'<meta name="binviz-boot" content="([^"]*)"', html)
    assert m, "the bootstrap placeholder is gone from index.html"
    import html as html_mod
    return json.loads(html_mod.unescape(m.group(1)))


# ---------------------------------------------------------------- login

def test_login_hands_back_the_token(tmp_path):
    auth_mod.set_credential(tmp_path, "analyst", "a good password")
    app = app_local(tmp_path)
    with authed_client(app) as c:
        r = c.post("/api/login",
                   json={"username": "analyst", "password": "a good password"})
        assert r.status_code == 200, r.text
        assert r.json()["token"] == app.state.auth_token
        assert r.json()["created"] is False


def test_login_is_reachable_without_a_token(tmp_path):
    """It has to be — its job is handing the token out. Verified rather
    than assumed, because the middleware exemption is easy to lose."""
    auth_mod.set_credential(tmp_path, "u", "a good password")
    app = app_local(tmp_path)
    with authed_client(app, token=None) as c:
        r = c.post("/api/login", json={"username": "u",
                                       "password": "a good password"})
        assert r.status_code == 200, r.text


def test_login_is_the_only_exemption(tmp_path):
    """A prefix match would let /api/login-anything through."""
    auth_mod.set_credential(tmp_path, "u", "a good password")
    app = app_local(tmp_path)
    with authed_client(app, token=None) as c:
        assert c.get("/api/config").status_code == 401
        assert c.get("/api/files?dir=.").status_code == 401
        assert c.post("/api/login/nope", json={}).status_code == 401


def test_a_wrong_password_says_nothing_about_which_half(tmp_path):
    auth_mod.set_credential(tmp_path, "analyst", "a good password")
    app = app_local(tmp_path)
    with authed_client(app) as c:
        bad_user = c.post("/api/login", json={"username": "nobody",
                                              "password": "a good password"})
        bad_pass = c.post("/api/login", json={"username": "analyst",
                                              "password": "wrong entirely"})
    assert bad_user.status_code == bad_pass.status_code == 401
    # identical text: differing messages turn this into a username oracle
    assert bad_user.json()["detail"] == bad_pass.json()["detail"]


def test_first_sign_in_sets_the_credential(tmp_path):
    """§2.3: the first run *sets* a password rather than comparing against
    a shipped default. There is no default; that is the point."""
    app = app_local(tmp_path)
    with authed_client(app) as c:
        r = c.post("/api/login", json={"username": "analyst",
                                       "password": "chosen right now"})
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True
    assert auth_mod.has_credential(tmp_path)
    # and it is now a real credential, not a permanently open door
    assert auth_mod.verify(tmp_path, "analyst", "chosen right now")
    assert not auth_mod.verify(tmp_path, "analyst", "anything else")


def test_repeated_failures_are_throttled(tmp_path):
    """Not a defence against a determined attacker — scrypt is that. This
    stops the form being a free, fast oracle (§2.3)."""
    auth_mod.set_credential(tmp_path, "u", "a good password")
    app = app_local(tmp_path)
    codes = []
    with authed_client(app) as c:
        for _ in range(auth_mod.FREE_ATTEMPTS + 2):
            codes.append(c.post("/api/login",
                                json={"username": "u", "password": "no"})
                         .status_code)
    assert codes[0] == 401
    assert 429 in codes, f"never throttled: {codes}"


def test_login_is_absent_unless_the_mode_asks_for_it(tmp_path):
    app = create_app(tmp_path, file_root=str(tmp_path))   # default: none
    with authed_client(app) as c:
        r = c.post("/api/login", json={"username": "u", "password": "p"})
        assert r.status_code == 404


# ------------------------------------------------- §2.1, the actual point

def test_signing_in_is_not_what_protects_the_api(tmp_path, manifest):
    """A login screen that only gates the UI is cosmetic. Prove the API is
    gated independently: with `local` mode on and no sign-in at all, every
    route still refuses — and it refuses for a caller that never even saw
    the form."""
    path = require_sample("hello_O2", manifest)
    app = app_local(tmp_path)
    with authed_client(app, token=None) as c:
        assert c.post("/api/open", json={"path": path}).status_code == 401
        assert c.get("/api/config").status_code == 401
        assert c.get(f"/api/{'0' * 64}/status").status_code == 401


def test_a_credential_does_not_replace_the_token(tmp_path):
    """Having signed in once does not make later requests authenticated by
    itself — the token still has to be presented. (No cookies: the token
    travels in a header, which is why allow_credentials stays off.)"""
    auth_mod.set_credential(tmp_path, "u", "a good password")
    app = app_local(tmp_path)
    with authed_client(app, token=None) as c:
        assert c.post("/api/login", json={"username": "u",
                                          "password": "a good password"}
                      ).status_code == 200
        # same client, same session, no Authorization header
        assert c.get("/api/config").status_code == 401


def test_the_kdf_does_not_stall_the_server(tmp_path, monkeypatch):
    """scrypt is memory-hard on purpose and costs ~80 ms a call. The login
    handler is `async`, so running it inline would hold the event loop for
    that long — stalling every other request, including a running
    analysis's status polling. The KDF being slow must cost the attacker,
    not the rest of the server."""
    import binviz.service as service_mod

    auth_mod.set_credential(tmp_path, "u", "a good password")
    offloaded = []
    real = service_mod.run_in_threadpool

    async def counting(fn, *a, **kw):
        offloaded.append(fn.__name__)
        return await real(fn, *a, **kw)

    monkeypatch.setattr(service_mod, "run_in_threadpool", counting)
    app = app_local(tmp_path)
    with authed_client(app) as c:
        c.post("/api/login", json={"username": "u",
                                   "password": "a good password"})
    assert "verify" in offloaded, "the KDF ran on the event loop"
