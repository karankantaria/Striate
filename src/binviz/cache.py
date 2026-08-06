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

#: The **analysis** version, not the distribution version (that one lives in
#: `binviz.__version__`). It feeds `params_fingerprint`, so bumping it makes
#: every cached analysis stale and forces a full re-analysis on next open.
#: Bump it when analysis *output* changes; leave it alone for a release that
#: only touches the UI or packaging. The two happen to read the same today.
TOOL_VERSION = "0.0.3"   # 0.0.3: trigram artifact cap + sidecar meta (P12)
SCHEMA = 1

# analysis steps, in run order; meta.json tracks each one's readiness
ARTIFACTS = ("model", "signals", "hist", "trigram", "functions", "triage")

# int32 little-endian [x, y, z, count] per point, sorted by count
# descending — a threshold query is then a prefix slice of the file
TRIGRAM_DTYPE = "<i4"

# stored trigram points are capped (count-descending, so the cap keeps the
# densest): a 2 GiB mixed file measured 256 MiB of trigram.sparse, and the
# UI never requests more than 1M points. trigram.meta.json records the true
# total and whether the artifact is capped.
TRIGRAM_STORE_MAX_POINTS = 1 << 22   # 4.2M points = 64 MiB on disk

# Cache eviction (S6). Nothing used to shrink this directory: `wipe()` only
# fired on a params-fingerprint mismatch, so every binary ever opened was
# retained forever. Measured: 17 small corpus samples came to 514 MB, and an
# uploaded file additionally stores a full copy of itself as file.bin.
DEFAULT_MAX_CACHE = 5 << 30          # 5 GiB; --max-cache / BINVIZ_MAX_CACHE

# An entry touched this recently is never evicted, even if it is the least
# recently used and the budget is blown. This is what stops the sweep
# deleting the artifacts out from under a tab that is looking at them: an
# entry being viewed is by definition being touched.
PROTECT_RECENT_SECONDS = 300


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

    def mark_progress(self, name: str, frac: float) -> None:
        meta = self.meta() or {}
        meta.setdefault("progress", {})[name] = round(float(frac), 3)
        self.write_json("meta.json", meta)

    def wipe(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir)

    def touch(self) -> None:
        """Mark this entry as used, for the LRU sweep.

        A directory mtime bump rather than a meta.json field: this runs on
        every API request, and rewriting meta.json that often would be both
        wasteful and a good way to hit the read-during-replace race that
        `source_path` already has to retry around.
        """
        try:
            os.utime(self.dir)
        except OSError:
            pass          # entry not created yet, or evicted underneath us


def _is_entry(name: str) -> bool:
    """Cache entries are sha256 hex directories. Anything else in the root
    (stray .upload temp files, the user's own junk) is not ours to delete."""
    return len(name) == 64 and all(c in "0123456789abcdef" for c in name)


def entry_size(path: Path) -> int:
    """Bytes on disk under `path`, ignoring anything that vanishes mid-walk."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass      # raced with a writer or another sweep
    return total


def cache_usage(root: str | os.PathLike | None = None) -> list[tuple[str, int, float]]:
    """(sha, bytes, mtime) for every entry, least recently used first."""
    base = Path(root) if root else default_root()
    if not base.is_dir():
        return []
    out = []
    for child in base.iterdir():
        if not child.is_dir() or not _is_entry(child.name):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        out.append((child.name, entry_size(child), mtime))
    out.sort(key=lambda e: e[2])
    return out


def sweep(root: str | os.PathLike | None = None,
          budget: int = DEFAULT_MAX_CACHE, *,
          protect: "set[str] | frozenset[str] | tuple" = (),
          now: float | None = None,
          protect_recent: float = PROTECT_RECENT_SECONDS) -> dict:
    """Evict least-recently-used entries until the cache fits `budget`.

    Never evicts an entry in `protect` (analyses in flight) or one touched
    within `protect_recent` seconds (something is looking at it). Those two
    guards mean the budget is a target rather than a hard ceiling: if
    everything is active, the cache is allowed to exceed it rather than
    delete data a viewer still needs. That is the intended trade — the
    alternative is a tab whose data disappears underneath it.

    Returns a summary; callers may log it, but nothing depends on it.
    """
    if budget <= 0:
        return {"total": 0, "evicted": [], "freed": 0, "protected": 0,
                "over_budget": False}
    now = time.time() if now is None else now
    entries = cache_usage(root)
    total = sum(size for _sha, size, _m in entries)
    evicted: list[str] = []
    freed = 0
    n_protected = 0

    base = Path(root) if root else default_root()
    for sha, size, mtime in entries:            # least recently used first
        if total <= budget:
            break
        if sha in protect or (now - mtime) < protect_recent:
            n_protected += 1
            continue
        try:
            shutil.rmtree(base / sha)
        except OSError:
            continue      # in use on Windows; it will be a candidate again
        evicted.append(sha)
        freed += size
        total -= size

    return {"total": total, "evicted": evicted, "freed": freed,
            "protected": n_protected, "over_budget": total > budget}


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

class StepProgress:
    """Throttled per-artifact progress: meta.json gets `progress.<name>` at
    most every `interval` seconds (plus always at 1.0). Long steps on large
    files run for minutes; without this the status endpoint can only say
    "running" (P12)."""

    def __init__(self, cache: BinaryCache, name: str,
                 interval: float = 0.5):
        self.cache = cache
        self.name = name
        self.interval = interval
        self._last = 0.0

    def __call__(self, frac: float) -> None:
        now = time.monotonic()
        if frac >= 1.0 or now - self._last >= self.interval:
            self._last = now
            self.cache.mark_progress(self.name, frac)


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
        progress={},
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
        step("signals", lambda: _do_signals(
            cache, mf.view, StepProgress(cache, "signals")))
        step("hist", lambda: _do_hist(
            cache, mf.view, StepProgress(cache, "hist")))
        step("trigram", lambda: _do_trigram(
            cache, mf.view, StepProgress(cache, "trigram")))
        if model is not None:
            step("functions", lambda: _do_functions(cache, mf.view, model))
            step("triage", lambda: _do_triage(
                cache, mf.view, model, StepProgress(cache, "triage")))
        else:
            for name in ("functions", "triage"):
                cache.mark_artifact(name, "error: no model")
                errors.append(f"{name}: no model")

    state = "complete" if not errors else "error"
    return cache.update_meta(state=state,
                             error="; ".join(errors) if errors else None)


def _do_signals(cache: BinaryCache, buf, progress=None) -> None:
    from .signals import compute_signals

    for name, sig in compute_signals(buf, progress=progress).items():
        cache.write_bytes(f"signals/{name}.f32",
                          sig.values.astype("<f4").tobytes())
        cache.write_bytes(f"signals/{name}.i64",
                          sig.offsets.astype("<i8").tobytes())


def _do_hist(cache: BinaryCache, buf, progress=None) -> None:
    from .stats import ngram

    a = np.frombuffer(buf, dtype=np.uint8)
    cache.write_bytes("hist/1_u8.bin", ngram(a, 1).astype("<u4").tobytes())
    cache.write_bytes("hist/2_u8.bin",
                      ngram(a, 2, progress).astype("<u4").tobytes())


def _do_trigram(cache: BinaryCache, buf, progress=None) -> None:
    from .stats import ngram

    a = np.frombuffer(buf, dtype=np.uint8)
    coords, counts = ngram(a, 3, progress)
    total = int(len(counts))
    payload = pack_trigram(coords, counts)
    capped = total > TRIGRAM_STORE_MAX_POINTS
    if capped:
        # count-descending, so the prefix keeps the densest points
        payload = payload[: TRIGRAM_STORE_MAX_POINTS * 16]
    cache.write_bytes("trigram.sparse", payload)
    cache.write_json("trigram.meta.json", {
        "total_points": total,
        "stored_points": min(total, TRIGRAM_STORE_MAX_POINTS),
        "capped": capped,
    })


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


def _do_triage(cache: BinaryCache, buf, model, progress=None) -> None:
    from .triage import triage

    functions = None
    if cache.exists("functions.json"):   # verdict survives recovery failure
        try:
            functions = cache.read_json("functions.json")
        except (OSError, json.JSONDecodeError):
            pass
    cache.write_json("triage.json",
                     triage(buf, model, functions, progress=progress))
