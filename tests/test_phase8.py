"""Phase 8 headless success criteria — the wire level behind the 2D/3D
histogram views.

The visual criteria (diagonal on ramp16 as u16le, pattern box highlight)
have exact headless proxies here: the dtype plumbing must reach /hist, and
POST /hist/locate must put density exactly where the brushed byte pairs
live and nowhere else.
"""

import json
import time

import numpy as np
import pytest

from conftest import authed_client, make_app

MB = 1024 * 1024

# 16-byte pattern (values chosen inside one brushable box) fills the first
# half; the second half is zeros. Every pattern pair has first and second
# in [0x10, 0x1F]; the zero half contributes only (0, 0) pairs.
PATTERN = bytes(range(0x10, 0x20))
HALF = 512 * 1024


def xmeta(r) -> dict:
    return json.loads(r.headers["X-Meta"])


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("p8cache"))
    with authed_client(app) as c:
        yield c


def _open_and_wait(client, path: str) -> str:
    r = client.post("/api/open", json={"path": path})
    assert r.status_code == 200, r.text
    sha = r.json()["id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        resp = client.get(f"/api/{sha}/status")
        assert resp.status_code == 200, resp.text   # never 404 (§3.7)
        s = resp.json()
        if s.get("state") == "complete":
            return sha
        if s.get("state") == "error":
            pytest.fail(f"analysis error: {s}")
        time.sleep(0.1)
    pytest.fail("analysis never completed")


@pytest.fixture(scope="module")
def pattern_sha(client, tmp_path_factory):
    path = tmp_path_factory.mktemp("p8") / "pattern_half.bin"
    with open(path, "wb") as f:
        f.write(PATTERN * (HALF // len(PATTERN)))
        f.write(b"\x00" * HALF)
    return _open_and_wait(client, str(path))


@pytest.fixture(scope="module")
def ramp_sha(client, tmp_path_factory):
    path = tmp_path_factory.mktemp("p8") / "ramp16.bin"
    vals = np.arange(65536, dtype="<u2")     # one full ramp, no wraparound
    path.write_bytes(vals.tobytes())
    return _open_and_wait(client, str(path))


def locate(client, sha, rect, n=256, **kw):
    r = client.post(f"/api/{sha}/hist/locate", json={**rect, "n": n, **kw})
    assert r.status_code == 200, r.text
    return np.frombuffer(r.content, dtype="<u4"), xmeta(r)


# ------------------------------------------------------------ /hist wire

def test_hist2_u8_wire_shape(client, pattern_sha):
    r = client.get(f"/api/{pattern_sha}/hist?n=2")
    assert r.status_code == 200
    counts = np.frombuffer(r.content, dtype="<u4")
    assert counts.shape == (65536,)
    meta = xmeta(r)
    assert meta["dtype"] == "u8" and meta["n"] == 2


def test_hist2_pattern_cells(client, pattern_sha):
    """Pattern half: exactly the 16 pattern pairs. Zero half adds (0,0),
    and the two junctions (pattern->zero, zero->EOF drop) at most 2 more."""
    r = client.get(f"/api/{pattern_sha}/hist?n=2")
    counts = np.frombuffer(r.content, dtype="<u4").reshape(256, 256)
    nz = {(int(i), int(j)) for i, j in zip(*np.nonzero(counts))}
    expected = {(PATTERN[k], PATTERN[(k + 1) % 16]) for k in range(16)}
    expected.add((0, 0))
    junction = {(PATTERN[-1], 0)}
    assert expected <= nz <= expected | junction


def test_hist2_ramp_u16le_collapses_to_diagonal(client, ramp_sha):
    """The frontend dtype-switch criterion, headless: as u8 the ramp fills
    the plane; as u16le consecutive quantised elements differ by at most
    one bin, so all bigram mass hugs the diagonal."""
    r = client.get(f"/api/{ramp_sha}/hist?n=2&dtype=u16le")
    counts = np.frombuffer(r.content, dtype="<u4").reshape(256, 256)
    ii, jj = np.nonzero(counts)
    assert len(ii) > 0
    assert int(np.abs(ii.astype(int) - jj.astype(int)).max()) <= 1
    # control: same file as u8 is nowhere near diagonal-only
    r8 = client.get(f"/api/{ramp_sha}/hist?n=2&dtype=u8")
    c8 = np.frombuffer(r8.content, dtype="<u4").reshape(256, 256)
    i8, j8 = np.nonzero(c8)
    assert int(np.abs(i8.astype(int) - j8.astype(int)).max()) > 1


# ------------------------------------------------------------ /hist3 limit

def test_hist3_limit_is_prefix_of_cached_points(client, pattern_sha):
    """Cached whole-file path: limit slices the count-descending prefix."""
    full = client.get(f"/api/{pattern_sha}/hist3")
    n_full = xmeta(full)["points"]
    assert n_full > 5
    r = client.get(f"/api/{pattern_sha}/hist3?limit=5")
    pts = np.frombuffer(r.content, dtype="<i4").reshape(-1, 4)
    meta = xmeta(r)
    assert meta["points"] == 5 and meta["capped"] is True
    assert meta["total_points"] == n_full
    assert (np.diff(pts[:, 3]) <= 0).all()      # count-descending
    assert pts[:5].tobytes() == full.content[: 5 * 16]


def test_hist3_limit_computed_subrange(client, pattern_sha):
    """Computed path (subrange): capped result keeps the densest points."""
    r = client.get(f"/api/{pattern_sha}/hist3?start=0&end={HALF}&limit=4")
    pts = np.frombuffer(r.content, dtype="<i4").reshape(-1, 4)
    meta = xmeta(r)
    assert meta["capped"] is True and len(pts) == 4
    assert (np.diff(pts[:, 3]) <= 0).all()
    # pattern half: every trigram is one of the 16 pattern triples, all at
    # nearly equal counts — any 4 of them are "densest"
    assert all(0x10 <= v <= 0x1F for v in pts[:, :3].ravel())


# ------------------------------------------------------------ /hist/locate

def test_locate_pattern_box_highlights_first_half_only(client, pattern_sha):
    """PLAN P8: brush-to-locate on the pattern highlights exactly the
    pattern's offsets — every bin of the pattern half, none of the zeros."""
    density, meta = locate(client, pattern_sha, {
        "first0": 0x10, "first1": 0x1F, "second0": 0x10, "second1": 0x1F})
    n = meta["n"]
    first_half = density[: n // 2]
    second_half = density[n // 2:]
    assert (first_half > 0).all()
    assert (second_half == 0).all()
    # every pattern pair except the one at the junction matches
    assert meta["matches"] == HALF - 1


def test_locate_zero_box_highlights_second_half(client, pattern_sha):
    density, meta = locate(client, pattern_sha, {
        "first0": 0, "first1": 0, "second0": 0, "second1": 0})
    n = meta["n"]
    assert (density[: n // 2] == 0).all()
    assert (density[n // 2:] > 0).all()


def test_locate_single_pair(client, pattern_sha, tmp_path):
    """One planted pair in a zero file lands in exactly one bin."""
    path = tmp_path / "needle.bin"
    data = bytearray(MB)
    off = 700 * 1024 + 13
    data[off:off + 2] = b"\x41\x6f"
    path.write_bytes(bytes(data))
    sha = _open_and_wait(client, str(path))
    density, meta = locate(client, sha, {
        "first0": 0x41, "first1": 0x41, "second0": 0x6F, "second1": 0x6F})
    assert meta["matches"] == 1
    hits = np.nonzero(density)[0]
    assert list(hits) == [off * meta["n"] // MB]


def test_locate_respects_subrange(client, pattern_sha):
    """Restricted to the zero half, the pattern box matches nothing."""
    density, meta = locate(
        client, pattern_sha,
        {"first0": 0x10, "first1": 0x1F, "second0": 0x10, "second1": 0x1F},
        start=HALF, end=2 * HALF)
    assert meta["matches"] == 0
    assert (density == 0).all()
    assert meta["start"] == HALF and meta["end"] == 2 * HALF


def test_locate_u16le_dtype(client, ramp_sha):
    """Non-u8 locate: ramp bins are position/256 of the range, so brushing
    the first bin row/col localises the start of the file."""
    density, meta = locate(client, ramp_sha, {
        "first0": 0, "first1": 0, "second0": 0, "second1": 1,
        "dtype": "u16le"}, n=64)
    assert meta["quantise"]["method"] == "linear"
    assert meta["matches"] > 0
    hits = np.nonzero(density)[0]
    # all matches live in the first 1/256 of the file: bin 0 of 64
    assert list(hits) == [0]


def test_locate_rect_normalised_and_clamped(client, pattern_sha):
    """Reversed and out-of-range rect coordinates are normalised, and the
    echoed rect says what was actually used."""
    _, meta = locate(client, pattern_sha, {
        "first0": 300, "first1": -5, "second0": 40, "second1": 20})
    assert meta["rect"] == {"first0": 0, "first1": 255,
                            "second0": 20, "second1": 40}


def test_locate_matches_equals_density_sum(client, pattern_sha):
    density, meta = locate(client, pattern_sha, {
        "first0": 0, "first1": 255, "second0": 0, "second1": 255}, n=1000)
    assert int(density.sum()) == meta["matches"] == meta["pairs"]
