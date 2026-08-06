"""Security regression tests (SECURITY-UI-WORKORDER §1).

These pin the second threat model: not "the binary is hostile" — the
parsing layer already handles that — but "the browser is hostile", and
"the binary's *metadata* is hostile". Every test here corresponds to a
numbered finding in the work order.
"""

from __future__ import annotations

import json
import os
import struct
import time

import pytest

from conftest import authed_client, make_app, sample_path

# The S2 payload. Two properties matter: it contains a double quote, so an
# unescaped `title="…"` closes early, and what follows is a live event
# handler rather than a tag (so escaping only `<` would not save you).
HOSTILE_NAME = b'a"onmouseover=b'


# ------------------------------------------------------------ ELF surgery

def _elf64_sections(buf: bytes):
    """(shstrtab_offset, [(sh_name_offset, section_index)]) for an ELF64."""
    if buf[:4] != b"\x7fELF" or buf[4] != 2:
        pytest.skip("fixture builder handles ELF64 only")
    e_shoff, = struct.unpack_from("<Q", buf, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", buf, 0x3A)
    names = []
    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        sh_name, = struct.unpack_from("<I", buf, base)
        names.append((sh_name, i))
    strtab_hdr = e_shoff + e_shstrndx * e_shentsize
    strtab_off, = struct.unpack_from("<Q", buf, strtab_hdr + 0x18)
    return strtab_off, names


def _patch_section_name(buf: bytes, payload: bytes) -> bytes | None:
    """Rename one section to `payload`, in place, without resizing the file.

    Same byte length is the point: rewriting the section header string
    table at a different size would move every following name and leave a
    file whose offsets no longer agree, which LIEF would reject for the
    wrong reason. We want a *valid* ELF carrying a hostile name.

    A slot of L characters holds L payload bytes plus the NUL terminator,
    so the payload must fit in the longest name the sample happens to have
    (in this corpus that is `.debug_pubnames`, exactly 15 — the same width
    as the payload, which is why it only fits in the debug builds).

    Skips any name whose byte range another section's name offset points
    into: ELF toolchains share string suffixes (".rela.text" ends with
    ".text"), and overwriting the wrong entry silently corrupts a second
    section's name instead of the one we meant.

    Returns None when no slot in this sample is wide enough.
    """
    strtab_off, names = _elf64_sections(buf)
    offsets = {n for n, _ in names}

    for sh_name, _idx in sorted(names, reverse=True):
        if sh_name == 0:
            continue
        end = buf.index(b"\0", strtab_off + sh_name) - strtab_off
        if end - sh_name < len(payload):
            continue
        # no other name may start inside the bytes we are about to write
        if any(sh_name < o <= end for o in offsets):
            continue
        out = bytearray(buf)
        at = strtab_off + sh_name
        out[at:at + len(payload) + 1] = payload + b"\0"
        return bytes(out)
    return None


# Debug builds carry the widest section names; a stripped or packed sample
# has no slot big enough, so the fixture shops around rather than pinning
# one file and skipping when that file changes.
_CANDIDATES = ("hello_O0", "hello_static", "hello_arm64", "hello_O2")


@pytest.fixture
def hostile_elf(tmp_path) -> str:
    """A real, valid ELF whose section name is an XSS payload."""
    import os

    for name in _CANDIDATES:
        src = sample_path(name)
        if not os.path.exists(src):
            continue
        with open(src, "rb") as f:
            original = f.read()
        if original[:4] != b"\x7fELF" or original[4] != 2:
            continue
        patched = _patch_section_name(original, HOSTILE_NAME)
        if patched is None:
            continue
        assert len(patched) == len(original), "patch must not resize the file"
        dst = tmp_path / "hostile.elf"
        dst.write_bytes(patched)
        return str(dst)

    pytest.skip(f"no ELF64 sample with a {len(HOSTILE_NAME)}-byte section "
                f"name slot — run `make -C corpus`")


# ------------------------------------------------- S2: hostile metadata

def test_hostile_section_name_survives_parsing(hostile_elf):
    """The backend is deliberately *not* the escaping layer.

    It must hand the frontend exactly the bytes that were in the file —
    silently sanitising here would be lying about the binary's contents,
    which is the one thing an analysis tool may never do. This test exists
    so that stays a decision rather than an accident: it documents that the
    hostile name reaches the model intact, and therefore that
    `web/src/escape.ts` is the only thing standing between it and the DOM.
    """
    from binviz.parse import parse

    model = parse(hostile_elf)
    names = [r.name for r in model.regions]
    assert HOSTILE_NAME.decode() in names, (
        "fixture is not hostile any more — the patch or the sample changed"
    )


def test_hostile_elf_is_still_a_valid_parse(hostile_elf):
    """Patching in place must not have produced a degraded raw model.

    If the fixture stopped being a real ELF, the test above would pass for
    the wrong reason (a raw model has no section names at all).
    """
    from binviz.parse import parse

    model = parse(hostile_elf)
    assert model.format == "elf", f"fixture degraded to {model.format!r}"
    assert len(model.regions) > 1


# ------------------------------------------------- S1a: API authentication

@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path / "cache")


@pytest.fixture
def client(app):
    with authed_client(app) as c:
        yield c


def test_no_token_is_rejected(app):
    """The finding was reproduced with no credentials at all. Close that."""
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://127.0.0.1") as anon:
        for path in ("/api/files?dir=.", "/api/open",
                     "/api/" + "0" * 64 + "/status"):
            r = anon.get(path)
            assert r.status_code == 401, f"{path} answered {r.status_code}"


def test_wrong_token_is_rejected(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://127.0.0.1") as bad:
        bad.headers["Authorization"] = "Bearer not-the-token"
        assert bad.get("/api/files?dir=.").status_code == 401


def test_token_is_accepted_three_ways(app, tmp_path):
    """Bearer, dedicated header, and `?token=` for the bootstrap URL."""
    from fastapi.testclient import TestClient

    from binviz.service import TOKEN_HEADER

    token = app.state.auth_token
    target = str(tmp_path)
    for headers, params in (
        ({"Authorization": f"Bearer {token}"}, ""),
        ({TOKEN_HEADER: token}, ""),
        ({}, f"&token={token}"),
    ):
        with TestClient(app, base_url="http://127.0.0.1") as c:
            r = c.get(f"/api/files?dir={target}{params}", headers=headers)
            assert r.status_code == 200, (headers, r.status_code)


def test_token_comparison_does_not_short_circuit():
    """A prefix of the real token must not be treated as closer to correct.

    This asserts the property compare_digest gives us rather than timing it
    — a timing assertion would be flaky in CI. What is actually being
    pinned is that `==` was not used: a token sharing a long prefix is
    rejected exactly like one sharing none.
    """
    from binviz.service import _token_ok

    real = "abcdefghijklmnop"
    assert _token_ok(real, real)
    assert not _token_ok(real[:-1], real)
    assert not _token_ok(real + "x", real)
    assert not _token_ok("", real)
    assert not _token_ok(None, real)
    assert not _token_ok("\u00ff" * len(real), real)   # non-ascii, no crash


def test_no_auth_mode_serves_without_a_token(tmp_path):
    """The escape hatch works — and is off unless explicitly asked for."""
    from fastapi.testclient import TestClient

    open_app = make_app(tmp_path / "cache", auth=False)
    assert open_app.state.auth_token is None
    with TestClient(open_app, base_url="http://127.0.0.1") as c:
        assert c.get(f"/api/files?dir={tmp_path}").status_code == 200


def test_tokens_differ_between_apps(tmp_path):
    a = make_app(tmp_path / "a").state.auth_token
    b = make_app(tmp_path / "b").state.auth_token
    assert a != b and len(a) >= 32


# --------------------------------------------- S1b: Host allowlist (rebind)

def test_rebound_host_is_rejected(app):
    """DNS rebinding: the browser thinks it is same-origin, so CORS does not
    apply — but the Host header still names the attacker's domain."""
    with authed_client(app) as c:
        r = c.get("/api/files?dir=.", headers={"Host": "evil.example"})
        assert r.status_code == 400


def test_loopback_hosts_are_allowed(app, tmp_path):
    for host in ("127.0.0.1", "localhost", "127.0.0.1:8000"):
        with authed_client(app) as c:
            r = c.get(f"/api/files?dir={tmp_path}", headers={"Host": host})
            assert r.status_code == 200, f"{host} -> {r.status_code}"


# ------------------------------------------------------------- S1c: CORS

def test_foreign_origin_gets_no_cors_grant(app, tmp_path):
    """`allow_origins=["*"]` was the server telling every site it could read
    these responses. A foreign origin must now get no grant at all — the
    browser then refuses to hand the body to the calling page."""
    with authed_client(app) as c:
        r = c.get(f"/api/files?dir={tmp_path}",
                  headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


def test_dev_origin_is_granted(app, tmp_path):
    with authed_client(app) as c:
        r = c.get(f"/api/files?dir={tmp_path}",
                  headers={"Origin": "http://127.0.0.1:5173"})
        assert r.headers.get("access-control-allow-origin") \
            == "http://127.0.0.1:5173"
        # the wire format depends on this one staying readable
        assert "X-Meta" in r.headers.get("access-control-expose-headers", "")


def test_credentials_are_not_allowed(app, tmp_path):
    """With a header token, cookies are never needed — and enabling them
    would make any future regression in the origin list far worse."""
    with authed_client(app) as c:
        r = c.get(f"/api/files?dir={tmp_path}",
                  headers={"Origin": "http://127.0.0.1:5173"})
        assert "access-control-allow-credentials" not in r.headers


# -------------------------------------------------- S1d: path confinement

@pytest.fixture
def confined(tmp_path):
    """An app confined to `root/`, with a secret deliberately outside it."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "sample.bin").write_bytes(b"\x00" * 64)
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("credentials")
    app = make_app(tmp_path / "cache", file_root=str(root))
    with authed_client(app) as c:
        yield c, root, secret


def test_path_inside_root_is_allowed(confined):
    client, root, _ = confined
    r = client.get(f"/api/files?dir={root}")
    assert r.status_code == 200
    assert [e["name"] for e in r.json()["files"]] == ["sample.bin"]


def test_path_outside_root_is_forbidden(confined):
    client, _, secret = confined
    assert client.post("/api/open",
                       json={"path": str(secret)}).status_code == 403
    assert client.get(
        f"/api/files?dir={secret.parent}").status_code == 403


def test_traversal_out_of_root_is_forbidden(confined):
    client, root, _ = confined
    escape = str(root / ".." / "outside" / "secret.txt")
    assert client.post("/api/open", json={"path": escape}).status_code == 403


def test_symlink_out_of_root_is_forbidden(confined, tmp_path):
    """realpath must run BEFORE the containment check.

    A textual prefix test passes here — the link really is inside root —
    and then opens whatever it points at. This is the specific mistake the
    work order calls out, so it gets its own test.
    """
    client, root, secret = confined
    link = root / "innocent.bin"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need admin or Developer Mode on Windows")
    assert client.post("/api/open",
                       json={"path": str(link)}).status_code == 403


def test_unconfined_app_allows_any_path(tmp_path):
    """file_root=None is the library default; confinement is opt-in and the
    CLI opts in. Pinned so the default is a decision, not a surprise."""
    app = make_app(tmp_path / "cache", file_root=None)
    with authed_client(app) as c:
        assert c.get(f"/api/files?dir={tmp_path}").status_code == 200


def test_junction_out_of_root_is_forbidden(confined, tmp_path):
    """The same realpath-ordering property as the symlink test, but with a
    Windows directory junction — which, unlike a symlink, needs no elevation,
    so this one actually runs on a normal Windows dev box.
    """
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("junctions are Windows-only; the symlink test covers this")
    client, root, secret = confined
    link = root / "innocent_dir"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link),
                        str(secret.parent)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"could not create junction: {r.stdout}{r.stderr}")
    assert os.path.realpath(link) != str(link), "junction did not resolve"
    assert client.get(f"/api/files?dir={link}").status_code == 403
    assert client.post(
        "/api/open", json={"path": str(link / "secret.txt")}
    ).status_code == 403


# ------------------------------- S3/S4: parameter validation and clamping

@pytest.fixture
def rendered(tmp_path):
    """A client with one small analysed binary, ready to render surfaces."""
    import time

    src = sample_path("hello_O2")
    if not os.path.exists(src):
        pytest.skip("corpus not built — run `make -C corpus`")
    app = make_app(tmp_path / "cache")
    with authed_client(app) as c:
        sha = c.post("/api/open", json={"path": src}).json()["id"]
        # Wait for the whole analysis, not just the model artifact: while
        # analyse() is still running it rewrites meta.json continuously, and
        # these tests assert on exact status codes. Racing that turns a 400
        # into an intermittent 409 and the failure looks like a param bug.
        deadline = time.time() + 180
        while time.time() < deadline:
            meta = c.get(f"/api/{sha}/status").json()
            if meta.get("state") == "complete":
                break
            if meta.get("state") == "error":
                pytest.fail(f"analysis failed: {meta.get('error')}")
            time.sleep(0.05)
        else:
            pytest.fail("analysis did not complete in time")
        yield c, sha


@pytest.mark.parametrize("width", ["0", "abc", "-5"])
def test_bad_image_width_is_400_not_500(rendered, width):
    """The three inputs from the work order, each previously an unhandled
    traceback: ZeroDivisionError, ValueError, and a reshape error."""
    client, sha = rendered
    r = client.get(f"/api/{sha}/surface/image?mode=rgb8&width={width}")
    assert r.status_code == 400, f"width={width!r} -> {r.status_code}"
    assert "width" in r.json()["detail"]


@pytest.mark.parametrize("query", [
    "mode=nonsense8",
    "mode=rgb99",
    "mode=bayerXX_1",
    "mode=bayer_RGGB_RGB_zzz",
    "mode=rgb8&max_rows=abc",
    "mode=rgb8&width=1e999",
])
def test_malformed_surface_params_are_400(rendered, query):
    client, sha = rendered
    r = client.get(f"/api/{sha}/surface/image?{query}")
    assert r.status_code == 400, f"{query} -> {r.status_code}"


@pytest.mark.parametrize("surface,query", [
    ("linear", "mode=value&reduce=nonsense"),
    ("linear", "mode=signal&signal=nonsense"),
    ("linear", "mode=nonsense"),
    ("hilbert", "mode=nonsense"),
    ("ngram2", "display=nonsense"),
    ("ngram3", "threshold=abc"),
    ("dotplot", "window=abc"),
    ("dotplot", "window=0"),
])
def test_malformed_params_on_other_surfaces_are_400(rendered, surface, query):
    """S3 was reported against the image path, but the same unguarded
    `int()` / unknown-mode pattern ran through every surface."""
    client, sha = rendered
    r = client.get(f"/api/{sha}/surface/{surface}?{query}")
    assert r.status_code == 400, f"{surface}?{query} -> {r.status_code}"


def test_oversized_raster_is_clamped(rendered):
    """`?w=20000&h=20000` asked for 400M cells and did not return in 40 s.

    The assertion is on the returned shape rather than a timing: a clamp
    that silently produced a 20000-wide raster quickly would still be the
    bug, and a slow CI box should not fail an unrelated build.
    """
    from binviz.surfaces.base import MAX_RASTER_DIM

    client, sha = rendered
    r = client.get(f"/api/{sha}/surface/linear?w=20000&h=20000")
    assert r.status_code == 200
    h, w = json.loads(r.headers["X-Meta"])["shape"][:2]
    assert w <= MAX_RASTER_DIM and h <= MAX_RASTER_DIM, f"got {w}x{h}"
    assert len(r.content) <= MAX_RASTER_DIM * MAX_RASTER_DIM


def test_clamp_bounds_both_directions():
    """The floor was already there; the ceiling is what S4 adds."""
    from binviz.surfaces.base import MAX_RASTER_DIM, SurfaceRequest

    tiny = SurfaceRequest(0, 100, 0, -5).clamp(100)
    assert (tiny.width, tiny.height) == (1, 1)

    huge = SurfaceRequest(0, 100, 20000, 20000).clamp(100)
    assert (huge.width, huge.height) == (MAX_RASTER_DIM, MAX_RASTER_DIM)


def test_valid_surface_requests_still_work(rendered):
    """The clamp must not have broken ordinary rendering."""
    client, sha = rendered
    for query in ("linear?w=256&h=256", "image?mode=rgb8&width=64",
                  "hilbert?w=128&h=128", "linear?mode=value&reduce=mean",
                  "ngram2?display=log1p"):
        r = client.get(f"/api/{sha}/surface/{query}")
        assert r.status_code == 200, f"{query} -> {r.status_code} {r.text[:200]}"


def test_ignored_params_are_not_rejected(rendered):
    """`reduce` means nothing in byteclass mode, and rejecting it there
    would be validation theatre — it would break callers that set the
    control once and switch modes. Pinned so a later tightening is a
    decision rather than a side effect."""
    client, sha = rendered
    r = client.get(f"/api/{sha}/surface/linear?mode=byteclass&reduce=nonsense")
    assert r.status_code == 200


def test_a_param_error_does_not_wedge_the_mmap(rendered):
    """A 400 must leave the file unmapped and the next request working.

    An exception crossing `MappedFile.__exit__` keeps the render frame —
    and its numpy views of the mmap — alive through the traceback, so
    close() raises BufferError on Windows and buries the real error. That
    is what this asserts is not happening.
    """
    client, sha = rendered
    assert client.get(
        f"/api/{sha}/surface/image?mode=rgb8&width=0").status_code == 400
    r = client.get(f"/api/{sha}/surface/image?mode=rgb8&width=64")
    assert r.status_code == 200, f"mmap wedged after a 400: {r.text[:200]}"


# --------------------------------------------------- S5: upload size cap

def test_oversize_upload_is_413(tmp_path):
    """The stream loop counted bytes but only ever checked `n == 0`."""
    app = make_app(tmp_path / "cache", max_upload=1024)
    with authed_client(app) as c:
        r = c.post("/api/open", content=b"\x00" * 4096,
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 413
        assert "limit" in r.json()["detail"]


def test_upload_within_the_cap_still_works(tmp_path):
    app = make_app(tmp_path / "cache", max_upload=1 << 20)
    with authed_client(app) as c:
        r = c.post("/api/open", content=b"\x7fELF" + b"\x00" * 512,
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        assert len(r.json()["id"]) == 64


def test_oversize_upload_leaves_no_temp_file(tmp_path):
    """A rejected upload must not leave a part-written .upload behind —
    otherwise the cap bounds one request but not the disk."""
    cache_root = tmp_path / "cache"
    app = make_app(cache_root, max_upload=1024)
    with authed_client(app) as c:
        c.post("/api/open", content=b"\x00" * 8192,
               headers={"Content-Type": "application/octet-stream"})
    leftovers = list(cache_root.glob("*.upload")) if cache_root.exists() else []
    assert leftovers == [], f"left behind: {leftovers}"


def test_lying_content_length_is_still_caught(tmp_path):
    """Content-Length is the client's claim; the byte counter is the control.

    A client that understates the length must still be stopped by the
    streaming check rather than sailing past the cheap header test.
    """
    app = make_app(tmp_path / "cache", max_upload=1024)
    with authed_client(app) as c:
        r = c.post("/api/open", content=b"\x00" * 8192,
                   headers={"Content-Type": "application/octet-stream",
                            "Content-Length": "8192"})
        assert r.status_code == 413


# ------------------------------------------ S7: bounded analysis concurrency

def test_analysis_concurrency_is_bounded(tmp_path, monkeypatch):
    """`_Jobs.ensure` spawned one unbounded daemon thread per distinct hash.

    Dedup only ever covered the *same* hash, so N distinct files meant N
    concurrent analyses. Uses a blocked fake `analyze` so the bound is
    tested directly rather than raced against real work.
    """
    import threading

    import binviz.cache as cache_mod
    from binviz.service import _Jobs

    release = threading.Event()
    started = threading.Semaphore(0)

    def fake_analyze(cache, source, *, stored=False):
        started.release()
        release.wait(timeout=30)

    monkeypatch.setattr(cache_mod, "analyze", fake_analyze)
    jobs = _Jobs(max_concurrent=2)
    try:
        for i in range(2):
            assert jobs.ensure(f"{i:064x}", "src", tmp_path,
                               stored=False) == "analyzing"
        for _ in range(2):
            assert started.acquire(timeout=10), "worker never started"

        with pytest.raises(Exception) as excinfo:
            jobs.ensure("f" * 64, "src", tmp_path, stored=False)
        assert getattr(excinfo.value, "status_code", None) == 503
        assert jobs.running() == 2
    finally:
        release.set()


def test_finished_analyses_free_their_slot(tmp_path, monkeypatch):
    """The bound must be on *live* work, not a high-water mark."""
    import binviz.cache as cache_mod
    from binviz.service import _Jobs

    monkeypatch.setattr(cache_mod, "analyze",
                        lambda cache, source, stored=False: None)
    jobs = _Jobs(max_concurrent=1)
    for i in range(5):
        for _ in range(200):          # let the previous one retire
            if jobs.running() == 0:
                break
            time.sleep(0.01)
        assert jobs.ensure(f"{i:064x}", "src", tmp_path,
                           stored=False) == "analyzing"


def test_open_returns_503_when_saturated(tmp_path, monkeypatch):
    """End to end: the bound surfaces as a 503 with Retry-After, not a 500."""
    import threading

    import binviz.cache as cache_mod

    release = threading.Event()
    monkeypatch.setattr(cache_mod, "analyze",
                        lambda cache, source, stored=False:
                            release.wait(timeout=30))
    app = make_app(tmp_path / "cache", max_analyses=1)
    try:
        with authed_client(app) as c:
            first = c.post("/api/open", content=b"\x7fELF" + b"\x00" * 64,
                           headers={"Content-Type": "application/octet-stream"})
            assert first.status_code == 200, first.text
            second = c.post("/api/open", content=b"\x7fELF" + b"\x01" * 64,
                            headers={"Content-Type": "application/octet-stream"})
            assert second.status_code == 503, second.text
            assert second.headers.get("Retry-After") == "5"
    finally:
        release.set()


# ------------------------------------------------ S6: bounded cache growth

def _entry(root, sha_char: str, size: int, age_seconds: float = 0.0):
    """A fake cache entry of a given size and age."""
    d = root / (sha_char * 64)
    d.mkdir(parents=True, exist_ok=True)
    (d / "blob.bin").write_bytes(b"\x00" * size)
    (d / "meta.json").write_text("{}")
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(d, (old, old))
    return d


def test_sweep_evicts_least_recently_used_first(tmp_path):
    from binviz.cache import sweep

    root = tmp_path / "cache"
    oldest = _entry(root, "a", 4000, age_seconds=9000)
    middle = _entry(root, "b", 4000, age_seconds=6000)
    newest = _entry(root, "c", 4000, age_seconds=3000)

    result = sweep(root, budget=9000)
    assert not oldest.exists(), "oldest should have gone first"
    assert middle.exists() and newest.exists()
    assert result["evicted"] == ["a" * 64]
    assert result["freed"] >= 4000


def test_sweep_is_a_no_op_under_budget(tmp_path):
    from binviz.cache import sweep

    root = tmp_path / "cache"
    kept = _entry(root, "a", 1000, age_seconds=9000)
    result = sweep(root, budget=1 << 30)
    assert kept.exists()
    assert result["evicted"] == [] and result["over_budget"] is False


def test_sweep_never_evicts_an_analysis_in_flight(tmp_path):
    """`protect` is fed from _Jobs.active(). Deleting a directory that a
    running analysis is writing into would corrupt it (and fail outright on
    Windows), so in-flight entries are off limits regardless of age."""
    from binviz.cache import sweep

    root = tmp_path / "cache"
    # both well outside the recency window, so `protect` is the only thing
    # that can explain `busy` surviving
    busy = _entry(root, "a", 8000, age_seconds=9000)     # oldest AND biggest
    other = _entry(root, "b", 8000, age_seconds=5000)

    result = sweep(root, budget=1000, protect={"a" * 64})
    assert busy.exists(), "an in-flight analysis was evicted"
    assert not other.exists()
    assert result["protected"] >= 1


def test_sweep_protects_recently_touched_entries(tmp_path):
    """The answer to 'can eviction pull data out from under an open tab'.

    An entry being viewed is being touched, so recency is the guard. The
    budget is deliberately a target rather than a hard ceiling here.
    """
    from binviz.cache import sweep

    root = tmp_path / "cache"
    fresh = _entry(root, "a", 8000, age_seconds=1)
    result = sweep(root, budget=100, protect_recent=300)

    assert fresh.exists(), "a just-touched entry was evicted"
    assert result["over_budget"] is True, (
        "sweep should report that it could not reach the budget")


def test_touch_updates_recency(tmp_path):
    from binviz.cache import BinaryCache, cache_usage

    root = tmp_path / "cache"
    _entry(root, "a", 100, age_seconds=9000)
    before = cache_usage(root)[0][2]
    BinaryCache("a" * 64, root).touch()
    after = cache_usage(root)[0][2]
    assert after > before


def test_sweep_ignores_things_that_are_not_cache_entries(tmp_path):
    """The cache root can contain .upload temp files and whatever else the
    user put there. Only sha256-shaped directories are ours to delete."""
    from binviz.cache import sweep

    root = tmp_path / "cache"
    root.mkdir(parents=True)
    stray_file = root / "notes.txt"
    stray_file.write_text("x" * 5000)
    stray_dir = root / "not-a-sha"
    stray_dir.mkdir()
    (stray_dir / "data").write_bytes(b"\x00" * 5000)
    _entry(root, "a", 5000, age_seconds=9000)

    sweep(root, budget=1)
    assert stray_file.exists() and stray_dir.exists()
    assert not (root / ("a" * 64)).exists()


def test_sweep_survives_a_missing_root(tmp_path):
    from binviz.cache import sweep

    result = sweep(tmp_path / "nope", budget=1000)
    assert result["evicted"] == []


def test_server_sweeps_after_analysis(tmp_path):
    """End to end: opening a file triggers a sweep that reclaims an old,
    unrelated entry — with the freshly analysed one left alone."""
    src = sample_path("hello_O2")
    if not os.path.exists(src):
        pytest.skip("corpus not built")
    cache_root = tmp_path / "cache"
    stale = _entry(cache_root, "d", 200_000, age_seconds=9000)

    app = make_app(cache_root, max_cache=100_000)
    with authed_client(app) as c:
        sha = c.post("/api/open", json={"path": src}).json()["id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            if (c.get(f"/api/{sha}/status").json().get("state")
                    in ("complete", "error")):
                break
            time.sleep(0.05)
        for _ in range(100):                 # sweep runs in the job thread
            if not stale.exists():
                break
            time.sleep(0.05)

    assert not stale.exists(), "stale entry survived the post-analysis sweep"
    assert (cache_root / sha).exists(), "the just-analysed entry was evicted"
