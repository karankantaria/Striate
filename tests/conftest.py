import json
import os

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "corpus")
OUT = os.path.abspath(os.path.join(CORPUS, "out"))


@pytest.fixture(scope="session")
def manifest() -> dict:
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def sample_path(name: str) -> str:
    return os.path.join(OUT, name)


def require_sample(name: str, manifest: dict) -> str:
    """Path to a built sample; skips the test if an optional one is absent."""
    path = sample_path(name)
    if not os.path.exists(path):
        if manifest["samples"].get(name, {}).get("optional"):
            pytest.skip(f"optional sample {name} not built")
        pytest.fail(f"required sample {name} missing — run `make -C corpus` "
                    f"(or `python corpus/build.py`)")
    return path


# ------------------------------------------------------- service fixtures

def make_app(cache_root=None, **kw):
    """`create_app` for tests.

    Authentication stays **on** and `authed_client` sends the real token.
    There is a `--no-auth` mode, and it would be one line to use it here,
    but then the auth layer would never be exercised by the suite that is
    supposed to protect it — and a flag that is convenient for tests is
    exactly how insecure defaults escape into production.

    Filesystem confinement (S1d) is off by default because these tests open
    samples from the corpus and from pytest temp directories, which are not
    under a common root (on Windows, not even on the same volume). It is
    covered explicitly in `test_security.py` with a purpose-built root.
    """
    from binviz.service import create_app

    kw.setdefault("file_root", None)
    return create_app(cache_root, **kw)


def authed_client(app, **kw):
    """TestClient that satisfies both the token check and the Host allowlist.

    `base_url` matters: TestClient's default Host is `testserver`, which
    TrustedHostMiddleware correctly rejects. Pointing it at 127.0.0.1 makes
    the tests speak to the app the way a real client does.
    """
    from fastapi.testclient import TestClient

    kw.setdefault("base_url", "http://127.0.0.1")
    client = TestClient(app, **kw)
    if app.state.auth_token is not None:
        client.headers["Authorization"] = f"Bearer {app.state.auth_token}"
    return client
