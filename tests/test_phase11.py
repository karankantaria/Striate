"""Phase 11 success criteria — the triage verdict.

The manifest's `expect` blocks are the ground truth: hello_upx is
likely_packed with the two headline findings, hello_O2 is benign with
nothing high-severity, non-executables stay non_executable, a truncated
ELF is corrupt, and — the false-positive check that proves the
classifier is region-aware — high-entropy *data* (sample.zip) must
never read as packing.
"""

import json
import time

import pytest

from binviz.disasm import recover
from binviz.loader import MappedFile
from binviz.parse import parse
from binviz.triage import triage
from conftest import authed_client, make_app, require_sample


def run_triage(path: str, with_functions: bool = True) -> dict:
    model = parse(path)
    with MappedFile.open(path) as mf:
        functions = (recover(mf.view, model).to_json()
                     if with_functions else None)
        return triage(mf.view, model, functions)


def codes(doc: dict) -> set[str]:
    return {f["code"] for f in doc["findings"]}


# ---------------------------------------------------- manifest verdicts

def test_upx_is_likely_packed(manifest):
    doc = run_triage(require_sample("hello_upx", manifest))
    assert doc["verdict"] == "likely_packed"
    expected = set(manifest["samples"]["hello_upx"]["expect"]
                   ["triage_findings_include"])
    assert expected <= codes(doc), doc["findings"]
    assert doc["confidence"] > 0.5


def test_hello_o2_is_benign(manifest):
    doc = run_triage(require_sample("hello_O2", manifest))
    assert doc["verdict"] == "likely_benign_binary"
    assert not [f for f in doc["findings"] if f["severity"] == "high"], \
        doc["findings"]


def test_png_is_non_executable(manifest):
    doc = run_triage(require_sample("sample.png", manifest),
                     with_functions=False)
    assert doc["verdict"] == "non_executable"


def test_truncated_elf_is_corrupt(manifest, tmp_path):
    data = open(require_sample("hello_O2", manifest), "rb").read()
    p = tmp_path / "trunc"
    p.write_bytes(data[: int(len(data) * 0.6)])
    doc = run_triage(str(p), with_functions=False)
    assert doc["verdict"] == "corrupt"
    assert "TRUNCATED" in codes(doc)


# ------------------------------------- the region-awareness FP check

def test_zip_is_not_likely_packed(manifest):
    """High entropy in non-executable bytes is a *different* finding.
    Failure here means the classifier isn't region-aware (§5.3)."""
    doc = run_triage(require_sample("sample.zip", manifest),
                     with_functions=False)
    assert doc["verdict"] != "likely_packed"
    assert "HIGH_ENTROPY_EXEC" not in codes(doc)
    # the entropy is still reported — as data, at low severity
    assert "HIGH_ENTROPY_NONEXEC" in codes(doc)
    sev = {f["code"]: f["severity"] for f in doc["findings"]}
    assert sev["HIGH_ENTROPY_NONEXEC"] == "low"


def test_compressed_resource_in_binary_is_not_packing(manifest, tmp_path):
    """An executable carrying a big compressed blob in a *data* region
    must not be called packed."""
    elf = open(require_sample("hello_O2", manifest), "rb").read()
    blob = open(require_sample("sample.zip", manifest), "rb").read()
    p = tmp_path / "resourceful"
    p.write_bytes(elf + blob)          # lands in the overlay: non-exec
    doc = run_triage(str(p), with_functions=False)
    assert doc["verdict"] != "likely_packed"
    assert "HIGH_ENTROPY_EXEC" not in codes(doc)


def test_zeros_has_no_entropy_findings(manifest):
    doc = run_triage(require_sample("zeros.bin", manifest),
                     with_functions=False)
    assert doc["verdict"] == "non_executable"
    assert "HIGH_ENTROPY_NONEXEC" not in codes(doc)


# ------------------------------------------------------ image finding

def test_bayer_raw_triggers_embedded_image(manifest):
    doc = run_triage(require_sample("bayer_raw.bin", manifest),
                     with_functions=False)
    assert "EMBEDDED_IMAGE_LIKELY" in codes(doc), doc["findings"]
    f = next(f for f in doc["findings"]
             if f["code"] == "EMBEDDED_IMAGE_LIKELY")
    assert f["offsets"] == [0, doc["size"]]
    assert f["stride_bytes"] > 0


def test_urandom_no_embedded_image(manifest):
    doc = run_triage(require_sample("urandom.bin", manifest),
                     with_functions=False)
    assert "EMBEDDED_IMAGE_LIKELY" not in codes(doc)


# -------------------------------------------------- findings navigate

def test_findings_offsets_are_navigable(manifest):
    """Every finding with offsets carries a valid in-file half-open
    range — that is what the frontend feeds the SelectionStore."""
    for name in ("hello_upx", "hello_O2", "bayer_raw.bin"):
        doc = run_triage(require_sample(name, manifest),
                         with_functions=name.startswith("hello"))
        for f in doc["findings"]:
            assert f["severity"] in ("high", "medium", "low")
            assert f["code"] and f["detail"]
            if f.get("offsets") is not None:
                a, b = f["offsets"]
                assert 0 <= a < b <= doc["size"], (name, f)


def test_verdict_survives_missing_functions(manifest):
    doc = run_triage(require_sample("hello_upx", manifest),
                     with_functions=False)
    assert doc["verdict"] == "likely_packed"


# ------------------------------------------------------- HTTP contract

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("p11cache"))
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


def test_triage_endpoint(client, manifest):
    sha = _open_and_wait(client, require_sample("hello_upx", manifest))
    st = client.get(f"/api/{sha}/status").json()
    assert st["artifacts"]["triage"] == "ready"
    doc = client.get(f"/api/{sha}/triage").json()
    assert doc["verdict"] == "likely_packed"
    assert {"HIGH_ENTROPY_EXEC", "IMPORT_STARVED"} <= \
        {f["code"] for f in doc["findings"]}
    # stable under re-request (served from the cached artifact)
    assert client.get(f"/api/{sha}/triage").json() == doc


def test_files_endpoint_lists_siblings(client, manifest):
    """P11 file navigation reads the sibling directory of the open file."""
    path = require_sample("hello_O2", manifest)
    import os
    r = client.get("/api/files", params={"dir": os.path.dirname(path)})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert "hello_O2" in names
    assert names == sorted(names, key=str.lower)
