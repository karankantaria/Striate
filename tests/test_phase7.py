"""Phase 7 headless success criteria.

The frontend itself is exercised by `npm test` in web/ (hilbert round-trip,
off<->va, colormaps); what belongs here is the wire-level spike regression:
a single 4 KiB high-entropy block injected into a large zero file must
survive server-side binning into the `max` band — the whole reason the
plot view renders min/mean/max envelopes instead of a downsampled line.
"""

import json
import time

import numpy as np
import pytest

from conftest import authed_client, make_app

SIZE = 32 * 1024 * 1024      # windows >> bins so naive aggregation would
N_BINS = 500                 # average the spike away (8192 windows -> 500)
SPIKE_OFF = 11 * 1024 * 1024 + 4096


def xmeta(r) -> dict:
    return json.loads(r.headers["X-Meta"])


@pytest.fixture(scope="module")
def spiked(tmp_path_factory):
    path = tmp_path_factory.mktemp("phase7") / "spiked.bin"
    rng = np.random.default_rng(7)
    with open(path, "wb") as f:
        f.truncate(SIZE)                       # sparse zeros
        f.seek(SPIKE_OFF)
        f.write(rng.integers(0, 256, 4096, dtype=np.uint8).tobytes())
    return str(path)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("p7cache"))
    with authed_client(app) as c:
        yield c


@pytest.fixture(scope="module")
def sha(client, spiked):
    r = client.post("/api/open", json={"path": spiked})
    assert r.status_code == 200, r.text
    sha = r.json()["id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        resp = client.get(f"/api/{sha}/status")
        # never 404, from the first poll onwards (§3.7) — this loop used to
        # tolerate one because /status raced the analysis thread's first
        # meta.json write
        assert resp.status_code == 200, resp.text
        s = resp.json()
        arts = s.get("artifacts") or {}
        if arts.get("signals") == "ready":
            return sha
        if s.get("state") == "error":
            pytest.fail(f"analysis error: {s}")
        time.sleep(0.1)
    pytest.fail("signals never became ready")


def band(client, sha, name, n):
    r = client.get(f"/api/{sha}/signal/{name}?n={n}")
    assert r.status_code == 200, r.text
    arr = np.frombuffer(r.content, dtype="<f4").reshape(3, n)
    return arr[0], arr[1], arr[2]      # min, mean, max


def test_spike_survives_max_band(client, sha):
    """One random 4 KiB window in 32 MiB of zeros: the max band at the
    spike's bin must exceed 7.5 bits/byte after binning (PLAN P7)."""
    mins, means, maxs = band(client, sha, "entropy_4096", N_BINS)
    spike_bin = SPIKE_OFF * N_BINS // SIZE
    hit = maxs[max(0, spike_bin - 1):spike_bin + 2].max()
    assert hit > 7.5, f"spike lost in binning: max around bin {spike_bin} = {hit}"
    # and it is a spike, not a plateau: the file is otherwise zero entropy
    assert float(np.median(maxs)) == 0.0


def test_mean_band_alone_would_hide_the_spike(client, sha):
    """Documents why the envelope exists: at this bin width the mean is
    diluted ~16x, far below any visual threshold."""
    _, means, _ = band(client, sha, "entropy_4096", N_BINS)
    assert means.max() < 1.0


def test_signal_wire_shape(client, sha):
    n = 777
    r = client.get(f"/api/{sha}/signal/entropy_4096?n={n}")
    assert len(r.content) == 3 * 4 * n
    assert xmeta(r)["layout"] == "min|mean|max f32"


def test_zoomed_refetch_matches_direct_slice(client, sha):
    """The zoomed view refetches the selection at full n rather than
    slicing the coarse full-file response — assert the refetch really
    resolves the spike window exactly."""
    lo = SPIKE_OFF - 64 * 1024
    hi = SPIKE_OFF + 64 * 1024
    n = 32          # 128 KiB / 4 KiB windows = 32 -> one window per bin
    r = client.get(f"/api/{sha}/signal/entropy_4096?n={n}&start={lo}&end={hi}")
    arr = np.frombuffer(r.content, dtype="<f4").reshape(3, n)
    spike_bin = (SPIKE_OFF - lo) * n // (hi - lo)
    assert arr[2][spike_bin] > 7.5
    assert arr[2][spike_bin] == arr[0][spike_bin]   # one window: min == max
