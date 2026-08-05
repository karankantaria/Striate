"""Phase 6 service: binary wire formats, readiness, single-flight analysis."""

import concurrent.futures
import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from binviz.service import create_app
from conftest import OUT, require_sample


def xmeta(r) -> dict:
    return json.loads(r.headers["X-Meta"])


def open_and_wait(client, path, timeout=180):
    r = client.post("/api/open", json={"path": path})
    assert r.status_code == 200, r.text
    sha = r.json()["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/{sha}/status")
        if s.status_code == 200 and s.json()["state"] in ("complete", "error"):
            return sha, s.json()
        time.sleep(0.05)
    pytest.fail(f"analysis of {path} timed out")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(tmp_path_factory.mktemp("srvcache"))
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def hello(client, manifest):
    path = require_sample("hello_O2", manifest)
    sha, status = open_and_wait(client, path)
    assert status["state"] == "complete", status
    return path, sha


def test_open_completes_all_artifacts(hello, client):
    _, sha = hello
    status = client.get(f"/api/{sha}/status").json()
    assert all(v == "ready" for v in status["artifacts"].values()), status


def test_second_open_is_immediately_ready(hello, client):
    path, sha = hello
    t0 = time.perf_counter()
    r = client.post("/api/open", json={"path": path})
    elapsed = time.perf_counter() - t0
    assert r.json() == {"id": sha, "state": "ready"}
    assert elapsed < 1.0


def test_model_endpoint(hello, client):
    from binviz.parse import parse

    path, sha = hello
    doc = client.get(f"/api/{sha}/model").json()
    direct = parse(path).to_json()
    assert doc == direct
    assert doc["mappings"]          # the client-side off<->va interval table


def test_signals_listing(hello, client):
    _, sha = hello
    sigs = {s["name"]: s for s in
            client.get(f"/api/{sha}/signals").json()["signals"]}
    assert sigs["entropy_4096"]["ready"]
    assert sigs["entropy_4096"]["windows"] == 2      # 11008 B at window 4096
    assert sigs["entropy_256"]["windows"] == 11008 // 256


def test_signal_wire_format_and_values(hello, client):
    from binviz.loader import MappedFile
    from binviz.signals import compute_signals
    from binviz.stats import reduce_minmeanmax

    path, sha = hello
    n = 500
    r = client.get(f"/api/{sha}/signal/entropy_256?n={n}")
    assert r.status_code == 200
    assert len(r.content) == 3 * 4 * n       # min|mean|max float32
    served = np.frombuffer(r.content, dtype="<f4").reshape(3, n)

    with MappedFile.open(path) as mf:
        sig = compute_signals(mf.view, ["entropy_256"])["entropy_256"]
    direct = np.stack([x.astype(np.float32)
                       for x in reduce_minmeanmax(sig.values, n)])
    np.testing.assert_allclose(served, direct, rtol=1e-6)

    meta = xmeta(r)
    assert meta["windows"] == len(sig.values)
    assert meta["layout"] == "min|mean|max f32"


def test_signal_range_selects_windows(hello, client):
    _, sha = hello
    r = client.get(f"/api/{sha}/signal/entropy_4096?n=8&start=4096&end=8192")
    assert xmeta(r)["windows"] == 1
    vals = np.frombuffer(r.content, dtype="<f4")
    assert len(set(vals.tolist())) == 1      # one window, honestly upsampled


def test_signal_unknown_404(hello, client):
    _, sha = hello
    assert client.get(f"/api/{sha}/signal/nope").status_code == 404


def test_hist_bigram_matches_engine(hello, client):
    from binviz.stats import ngram

    path, sha = hello
    r = client.get(f"/api/{sha}/hist?n=2")
    assert len(r.content) == 65536 * 4
    with open(path, "rb") as f:
        a = np.frombuffer(f.read(), dtype=np.uint8)
    assert r.content == ngram(a, 2).astype("<u4").tobytes()


def test_hist_subrange_computed_on_demand(hello, client):
    from binviz.stats import ngram

    path, sha = hello
    r = client.get(f"/api/{sha}/hist?n=1&start=0&end=1000")
    with open(path, "rb") as f:
        a = np.frombuffer(f.read(1000), dtype=np.uint8)
    assert r.content == ngram(a, 1).astype("<u4").tobytes()
    assert xmeta(r)["quantise"]["method"]


def test_hist3_threshold_is_prefix_filter(hello, client):
    _, sha = hello
    r1 = client.get(f"/api/{sha}/hist3?threshold=1")
    r2 = client.get(f"/api/{sha}/hist3?threshold=4")
    p1 = np.frombuffer(r1.content, dtype="<i4").reshape(-1, 4)
    p2 = np.frombuffer(r2.content, dtype="<i4").reshape(-1, 4)
    assert xmeta(r1)["points"] == len(p1)
    assert len(p2) < len(p1)
    assert (p2[:, 3] >= 4).all()
    assert (p1[:, 3] >= 1).all()
    np.testing.assert_array_equal(p1[:len(p2)], p2)   # count-descending prefix


def test_surface_raster_and_disk_cache(hello, client):
    _, sha = hello
    url = f"/api/{sha}/surface/linear?w=64&h=64&mode=byteclass"
    r1 = client.get(url)
    assert r1.status_code == 200
    assert len(r1.content) == 64 * 64
    meta = xmeta(r1)
    assert meta["kind"] == "scalar" and meta["shape"] == [64, 64]
    r2 = client.get(url)                     # served from the raster cache
    assert r2.content == r1.content
    assert xmeta(r2) == meta


def test_functions_and_cfg_endpoints(hello, client):
    _, sha = hello
    doc = client.get(f"/api/{sha}/functions").json()
    assert doc["functions"] and not doc["packed"]
    main = next((f for f in doc["functions"] if f["name"] == "main"),
                doc["functions"][0])
    cfg = client.get(f"/api/{sha}/cfg/{main['va']}").json()
    assert cfg["function"]["va"] == main["va"]
    assert cfg["blocks"]
    assert client.get(f"/api/{sha}/cfg/0x1").status_code == 404


def test_bytes_endpoint(hello, client):
    path, sha = hello
    r = client.get(f"/api/{sha}/bytes?off=64&len=256")
    with open(path, "rb") as f:
        f.seek(64)
        assert r.content == f.read(256)
    big = client.get(f"/api/{sha}/bytes?off=0&len=99999999")
    assert xmeta(big)["len"] == 1 << 20      # hard cap applied


def test_dotplot_progress_is_monotonic(client, manifest):
    path = require_sample("repeats.bin", manifest)
    sha, status = open_and_wait(client, path)
    assert status["state"] == "complete", status
    url = (f"/api/{sha}/surface/dotplot?w=128&h=128"
           f"&window=8&seed=7&max_samples=4000")
    m1 = xmeta(client.get(url))["meta"]
    m2 = xmeta(client.get(url))["meta"]
    assert m1["mode"] == m2["mode"] == "sampled"
    assert m2["resolved"] == m1["resolved"] + 4000
    assert m2["progress"] > m1["progress"]
    assert m2["hits"] >= m1["hits"] > 0
    assert m2["cursor"] == m1["cursor"] + 1


def test_upload_octet_stream(client):
    body = bytes(range(256)) * 4 + b"only-in-this-upload"
    r = client.post("/api/open", content=body,
                    headers={"content-type": "application/octet-stream"})
    sha = r.json()["id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        s = client.get(f"/api/{sha}/status")
        if s.status_code == 200 and s.json()["state"] in ("complete", "error"):
            break
        time.sleep(0.05)
    assert s.json()["state"] == "complete", s.json()
    got = client.get(f"/api/{sha}/bytes?off=0&len={len(body)}")
    assert got.content == body
    # shorter than every window: the signal endpoint still answers honestly
    sig = client.get(f"/api/{sha}/signal/entropy_4096?n=10")
    assert len(sig.content) == 3 * 4 * 10
    assert xmeta(sig)["windows"] == 0


def test_files_listing(client):
    r = client.get("/api/files", params={"dir": OUT})
    names = [f["name"] for f in r.json()["files"]]
    assert "hello_O2" in names
    assert client.get("/api/files",
                      params={"dir": OUT + "/nope"}).status_code == 404


def test_triage_not_yet(hello, client):
    _, sha = hello
    assert client.get(f"/api/{sha}/triage").status_code == 501


def test_unknown_and_malformed_ids(client):
    assert client.get(f"/api/{'0' * 64}/status").status_code == 404
    assert client.get("/api/nothex/status").status_code == 400


def test_concurrent_open_analyzes_once(tmp_path, manifest, monkeypatch):
    import binviz.cache as cache_mod

    path = require_sample("pattern.bin", manifest)
    calls = []
    real = cache_mod.analyze

    def counting(cache, source, **kw):
        calls.append(source)
        return real(cache, source, **kw)

    monkeypatch.setattr(cache_mod, "analyze", counting)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            rs = list(ex.map(
                lambda _: client.post("/api/open", json={"path": path}),
                range(4)))
        assert len({r.json()["id"] for r in rs}) == 1
        sha = rs[0].json()["id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            s = client.get(f"/api/{sha}/status")
            if (s.status_code == 200
                    and s.json()["state"] in ("complete", "error")):
                break
            time.sleep(0.05)
        assert s.json()["state"] == "complete"
    assert len(calls) == 1
