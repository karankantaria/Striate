"""Phase 9 headless success criteria — the wire level behind the image
view, dot plot, and hex viewer.

The visual criteria (colour bars at stride 320, shear at 321, dot-plot
bands) are asserted at the engine level in test_surfaces.py; here we pin
the HTTP plumbing the new views depend on: the stride-suggester endpoint,
the PNG path of /surface/image, and the dotplot progressive contract the
frontend's refinement loop drives.
"""

import json
import time

import pytest

from conftest import authed_client, make_app, require_sample


def xmeta(r) -> dict:
    return json.loads(r.headers["X-Meta"])


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("p9cache"))
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
def rgb_sha(client, manifest):
    return _open_and_wait(client, require_sample("rgb_raw.bin", manifest))


@pytest.fixture(scope="module")
def bayer_sha(client, manifest):
    return _open_and_wait(client, require_sample("bayer_raw.bin", manifest))


@pytest.fixture(scope="module")
def repeats_sha(client, manifest):
    return _open_and_wait(client, require_sample("repeats.bin", manifest))


# ------------------------------------------------------- stride endpoint

def test_stride_rgb_top_candidate(client, rgb_sha):
    r = client.get(f"/api/{rgb_sha}/image/stride?mode=rgb8")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["mode"] == "rgb8"
    cands = doc["candidates"]
    assert cands, "no stride candidates for rgb_raw.bin"
    assert cands[0]["pixels"] == 320, cands
    assert cands[0]["exact"] is True
    assert cands[0]["bytes"] == 960


def test_stride_bayer_in_top3(client, bayer_sha):
    r = client.get(f"/api/{bayer_sha}/image/stride?mode=bayer12_0")
    assert r.status_code == 200, r.text
    cands = r.json()["candidates"]
    # the CFA repeats every two rows, so the raw peak is 1920 bytes; the
    # true 960-byte / 640-px stride must still surface via sub-multiples
    assert any(c["pixels"] == 640 for c in cands), cands


def test_stride_bad_mode_400(client, rgb_sha):
    assert client.get(
        f"/api/{rgb_sha}/image/stride?mode=nonsense").status_code == 400


def test_stride_respects_range(client, rgb_sha):
    r = client.get(f"/api/{rgb_sha}/image/stride?mode=rgb8&start=0&end=64")
    assert r.status_code == 200
    doc = r.json()
    assert doc["end"] == 64
    assert doc["candidates"] == []      # range too short for any lag


# ----------------------------------------------------- image surface PNG

def test_image_surface_ships_png(client, rgb_sha, manifest):
    spec = manifest["samples"]["rgb_raw.bin"]
    r = client.get(f"/api/{rgb_sha}/surface/image?mode=rgb8&width=320")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    meta = xmeta(r)
    assert meta["kind"] == "rgb"
    assert meta["shape"] == [spec["height"], spec["width"], 3]


def test_image_surface_bayer_mode_param(client, bayer_sha):
    r = client.get(
        f"/api/{bayer_sha}/surface/image?mode=bayer_RGGB_RGB_12&width=640")
    assert r.status_code == 200, r.text
    meta = xmeta(r)
    assert meta["meta"]["cfa_phase"] == "RGGB"
    assert meta["shape"][1] == 640


# -------------------------------------------- dotplot progressive contract

def test_dotplot_progressive_refines(client, repeats_sha, manifest):
    """The frontend loop re-requests with an advancing cursor; progress and
    hit counts must be monotone, and the mode label must be present."""
    size = manifest["samples"]["repeats.bin"]["size"]
    q = (f"/api/{repeats_sha}/surface/dotplot?w=128&h=128"
         f"&window=8&max_samples=20000&seed=1&mode=sampled"
         f"&start=0&end={size}")
    m1 = xmeta(client.get(q + "&cursor=0"))["meta"]
    m2 = xmeta(client.get(q + "&cursor=1"))["meta"]
    assert m1["mode"] == m2["mode"] == "sampled"
    assert m2["resolved"] > m1["resolved"]
    assert m2["progress"] >= m1["progress"]
    assert m2["hits"] >= m1["hits"]


def test_dotplot_exact_under_threshold(client, repeats_sha):
    """A selection-sized range must run exact and say so."""
    r = client.get(f"/api/{repeats_sha}/surface/dotplot?w=128&h=128"
                   f"&window=8&start=0&end={128 * 1024}")
    meta = xmeta(r)["meta"]
    assert meta["mode"] == "exact"
    assert meta["progress"] == 1.0
