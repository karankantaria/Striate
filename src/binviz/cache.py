"""Content-addressed artifact cache — Phase 6.

Analysis is deterministic and pure given file bytes plus analysis
parameters, so caching by content hash is trivially correct. Artifacts
live at `<root>/<sha256>/`; the cache key includes a fingerprint of the
analysis parameters (tool version, signal windows, ...), so changing a
window size invalidates cleanly instead of serving stale numbers.

Crash safety: every artifact is written to a `.tmp` sibling and moved
into place with `os.replace`, and `meta.json` marks an artifact ready
only after the move — a process killed mid-analysis never leaves a
half-written artifact readable as complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np

TOOL_VERSION = "0.0.1"
SCHEMA = 1

# analysis steps, in run order; meta.json tracks each one's readiness
ARTIFACTS = ("model", "signals", "hist", "trigram", "functions")

# int32 little-endian [x, y, z, count] per point, sorted by count
# descending — a threshold query is then a prefix slice of the file
TRIGRAM_DTYPE = "<i4"


def default_root() -> Path:
    env = os.environ.get("BINVIZ_CACHE")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".cache" / "binviz"


def params_fingerprint() -> str:
    """Hash of everything that changes analysis output besides the bytes."""
    from .signals import SIGNALS

    doc = {
        "schema": SCHEMA,
        "tool_version": TOOL_VERSION,
        "signals": {n: SIGNALS[n][:2] for n in sorted(SIGNALS)},
        "trigram": {"dtype": TRIGRAM_DTYPE, "order": "count_desc"},
    }
    blob = json.dumps(doc, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class BinaryCache:
    """One binary's artifact directory: `<root>/<sha256>/`."""

    def __init__(self, sha256: str, root: str | os.PathLike | None = None):
        self.sha256 = sha256
        self.root = Path(root) if root else default_root()
        self.dir = self.root / sha256

    def path(self, rel: str) -> Path:
        return self.dir / rel

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    # ---------------------------------------------------------- writes

    def write_bytes(self, rel: str, data: bytes) -> None:
        dst = self.path(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        _replace(tmp, dst)

    def write_json(self, rel: str, obj) -> None:
        self.write_bytes(rel, json.dumps(obj).encode())

    # ----------------------------------------------------------- reads

    def read_bytes(self, rel: str) -> bytes:
        return self.path(rel).read_bytes()

    def read_json(self, rel: str):
        return json.loads(self.read_bytes(rel))

    def memmap(self, rel: str, dtype: str) -> np.ndarray:
        return np.memmap(self.path(rel), dtype=dtype, mode="r")

    # ------------------------------------------------------------ meta

    def meta(self) -> dict | None:
        try:
            return self.read_json("meta.json")
        except (OSError, json.JSONDecodeError):
            return None

    def update_meta(self, **changes) -> dict:
        meta = self.meta() or {}
        meta.update(changes)
        self.write_json("meta.json", meta)
        return meta

    def mark_artifact(self, name: str, state: str) -> None:
        meta = self.meta() or {}
        meta.setdefault("artifacts", {})[name] = state
        self.write_json("meta.json", meta)

    def wipe(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir)


def _replace(tmp: Path, dst: Path, attempts: int = 5) -> None:
    """os.replace with retries: on Windows the replace (not the read)
    fails with PermissionError if a reader has the destination open."""
    for i in range(attempts):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (i + 1))


def is_ready(cache: BinaryCache) -> bool:
    """Complete, and produced by the same tool version and parameters."""
    meta = cache.meta()
    return (meta is not None
            and meta.get("state") == "complete"
            and meta.get("params_fingerprint") == params_fingerprint())


# ---------------------------------------------------------------- analysis

def analyze(cache: BinaryCache, source_path: str, *,
            stored: bool = False) -> dict:
    """Run every analysis step and populate the cache directory.

    Individual step failures are recorded per-artifact and do not abort
    the rest — a malformed binary that defeats function recovery must
    still get its entropy strip. Returns the final meta document.
    """
    from .loader import MappedFile
    from .parse import parse as parse_binary

    cache.dir.mkdir(parents=True, exist_ok=True)
    size = os.path.getsize(source_path)
    cache.update_meta(
        schema=SCHEMA, tool_version=TOOL_VERSION,
        params_fingerprint=params_fingerprint(),
        sha256=cache.sha256, size=size,
        source={"path": os.path.abspath(source_path), "stored": stored},
        state="running", error=None,
        artifacts={a: "pending" for a in ARTIFACTS},
    )

    errors: list[str] = []

    def step(name: str, fn) -> bool:
        cache.mark_artifact(name, "running")
        try:
            fn()
        except Exception as e:  # record and continue with later steps
            cache.mark_artifact(name, f"error: {e}")
            errors.append(f"{name}: {e}")
            return False
        cache.mark_artifact(name, "ready")
        return True

    model = None

    def do_model():
        nonlocal model
        model = parse_binary(source_path)
        cache.write_json("model.json", model.to_json())

    step("model", do_model)

    # mf.view is taken fresh inside each step call so no memoryview of the
    # mmap outlives its step (mmap.close refuses while exports exist)
    with MappedFile.open(source_path) as mf:
        step("signals", lambda: _do_signals(cache, mf.view))
        step("hist", lambda: _do_hist(cache, mf.view))
        step("trigram", lambda: _do_trigram(cache, mf.view))
        if model is not None:
            step("functions", lambda: _do_functions(cache, mf.view, model))
        else:
            cache.mark_artifact("functions", "error: no model")
            errors.append("functions: no model")

    state = "complete" if not errors else "error"
    return cache.update_meta(state=state,
                             error="; ".join(errors) if errors else None)


def _do_signals(cache: BinaryCache, buf) -> None:
    from .signals import compute_signals

    for name, sig in compute_signals(buf).items():
        cache.write_bytes(f"signals/{name}.f32",
                          sig.values.astype("<f4").tobytes())
        cache.write_bytes(f"signals/{name}.i64",
                          sig.offsets.astype("<i8").tobytes())


def _do_hist(cache: BinaryCache, buf) -> None:
    from .stats import ngram

    a = np.frombuffer(buf, dtype=np.uint8)
    cache.write_bytes("hist/1_u8.bin", ngram(a, 1).astype("<u4").tobytes())
    cache.write_bytes("hist/2_u8.bin", ngram(a, 2).astype("<u4").tobytes())


def _do_trigram(cache: BinaryCache, buf) -> None:
    from .stats import ngram

    a = np.frombuffer(buf, dtype=np.uint8)
    coords, counts = ngram(a, 3)
    cache.write_bytes("trigram.sparse", pack_trigram(coords, counts))


def pack_trigram(coords: np.ndarray, counts: np.ndarray) -> bytes:
    """Sparse points as int32 [x,y,z,count] rows, count-descending (ties
    broken by coordinate, so the file is deterministic)."""
    n = len(counts)
    out = np.empty((n, 4), dtype=TRIGRAM_DTYPE)
    if n:
        key = ((coords[:, 0].astype(np.int64) << 16)
               | (coords[:, 1].astype(np.int64) << 8)
               | coords[:, 2].astype(np.int64))
        order = np.lexsort((key, -counts.astype(np.int64)))
        out[:, :3] = coords[order]
        out[:, 3] = counts[order]
    return out.tobytes()


def _do_functions(cache: BinaryCache, buf, model) -> None:
    from .disasm import recover

    program = recover(buf, model)
    cache.write_json("functions.json", program.to_json())
    for fn in program.functions:
        cache.write_json(f"cfg/{fn.va:x}.json", fn.to_json())
