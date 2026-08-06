"""HTTP service over the artifact cache — Phase 6.

The frontend is deliberately dumb: every number it draws was computed
and cached server-side, keyed by content hash. Bulk numeric data —
signals, histograms, rasters, trigram points — ships as raw
little-endian typed arrays (`new Float32Array(await r.arrayBuffer())`),
never JSON; metadata rides in an `X-Meta` header. Non-negotiable: a
100 MB file's entropy profile is ~4 MB of JSON text versus 1.5 MB of
octet-stream.

Run with `binviz serve`, or `uvicorn binviz.service:app`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import cache as cache_mod
from .cache import ARTIFACTS, TOOL_VERSION, TRIGRAM_DTYPE, BinaryCache, is_ready
from .loader import MappedFile, sha256_file

MAX_BYTES_READ = 1 << 20        # /bytes hard cap
DEFAULT_SIGNAL_BINS = 2000
DOTPLOT_LRU = 16                # in-memory progressive accumulators
DOTPLOT_ACC_VERSION = 2         # 2: int64 weighted pair-count matrix (P12)


# --------------------------------------------------------------- helpers

def _typed(raw: str):
    """Query-string value to a typed param (bool, int, float, else str)."""
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw, 0)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _meta_headers(meta: dict) -> dict[str, str]:
    return {"X-Meta": json.dumps(_jsonable(meta))}


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, np.generic):
        return v.item()
    return v


class _Jobs:
    """In-process analysis futures keyed by sha256, so a concurrent open
    of the same file never analyses twice."""

    def __init__(self):
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def ensure(self, sha: str, source: str, root, *, stored: bool) -> str:
        with self._lock:
            t = self._threads.get(sha)
            if t is not None and t.is_alive():
                return "analyzing"
            cache = BinaryCache(sha, root)
            if is_ready(cache):
                return "ready"
            meta = cache.meta()
            if meta is not None and meta.get(
                    "params_fingerprint") != cache_mod.params_fingerprint():
                cache.wipe()   # stale parameters: rebuild from scratch

            def run():
                try:
                    cache_mod.analyze(cache, source, stored=stored)
                except Exception as e:   # analyze() records its own errors;
                    try:                 # this catches setup-level failures
                        cache.update_meta(state="error", error=str(e))
                    except OSError:
                        pass

            t = threading.Thread(target=run, daemon=True,
                                 name=f"binviz-analyze-{sha[:12]}")
            self._threads[sha] = t
            t.start()
            return "analyzing"


class _DotPlots:
    """Progressive dot-plot accumulators: LRU in memory, persisted to
    `dotplot_acc/<key>.npz` so progress survives a server restart."""

    def __init__(self, limit: int = DOTPLOT_LRU):
        self._lock = threading.Lock()
        self._limit = limit
        self._live: OrderedDict[tuple, object] = OrderedDict()

    def get_or_create(self, cache: BinaryCache, req, n1: int, n2: int):
        from .surfaces.dotplot import DotPlotSurface

        key = (cache.sha256, _dotplot_key(req))
        with self._lock:
            if key in self._live:
                self._live.move_to_end(key)
                return self._live[key]
            acc = DotPlotSurface.accumulator(req, req.width, req.height,
                                             n1, n2)
            rel = f"dotplot_acc/{key[1]}.npz"
            if cache.exists(rel):
                try:
                    saved = np.load(cache.path(rel))
                    if ("version" in saved
                            and int(saved["version"]) == DOTPLOT_ACC_VERSION
                            and int(saved["n1"]) == n1
                            and int(saved["n2"]) == n2
                            and saved["matrix"].shape == acc.matrix.shape):
                        acc.matrix = saved["matrix"].astype(np.int64)
                        acc.resolved = int(saved["resolved"])
                        acc.hits = int(saved["hits"])
                        acc.cursor = int(saved["cursor"])
                except (OSError, KeyError, ValueError):
                    pass   # corrupt state: start the sampling over
            self._live[key] = acc
            while len(self._live) > self._limit:
                self._live.popitem(last=False)
            return acc

    def persist(self, cache: BinaryCache, req, acc) -> None:
        import io

        rel = f"dotplot_acc/{_dotplot_key(req)}.npz"
        bio = io.BytesIO()
        np.savez(bio, matrix=acc.matrix, resolved=acc.resolved,
                 hits=acc.hits, cursor=acc.cursor, n1=acc.n1, n2=acc.n2,
                 version=DOTPLOT_ACC_VERSION)
        cache.write_bytes(rel, bio.getvalue())


def _dotplot_key(req) -> str:
    # cursor and max_samples are per-request pacing, not identity
    params = {k: v for k, v in req.params.items()
              if k not in ("cursor", "max_samples", "accumulator")}
    ident = (req.start, req.end, req.width, req.height, req.dtype,
             tuple(sorted(params.items())))
    return hashlib.blake2b(repr(ident).encode(), digest_size=12).hexdigest()


# ------------------------------------------------------------------- app

def create_app(cache_root: str | os.PathLike | None = None) -> FastAPI:
    app = FastAPI(title="binviz", version=TOOL_VERSION)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"], expose_headers=["X-Meta"])

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        # Analysis state changes under the same URL, and several error
        # statuses (404, 410) are heuristically cacheable — a browser that
        # caches a mid-analysis error would never recover. Nothing here
        # may be cached by HTTP semantics.
        resp = await call_next(request)
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp
    app.state.cache_root = cache_root
    app.state.jobs = _Jobs()
    app.state.dotplots = _DotPlots()

    def root():
        return app.state.cache_root or cache_mod.default_root()

    def get_cache(id: str) -> BinaryCache:
        if len(id) != 64 or any(c not in "0123456789abcdef" for c in id):
            raise HTTPException(400, "id must be a sha256 hex digest")
        cache = BinaryCache(id, root())
        if cache.meta() is None:
            raise HTTPException(404, f"unknown binary {id[:12]}…; POST "
                                     "/api/open first")
        return cache

    def require(cache: BinaryCache, artifact: str) -> None:
        meta = cache.meta() or {}
        state = meta.get("artifacts", {}).get(artifact)
        if state != "ready":
            raise HTTPException(
                409, f"artifact {artifact!r} not ready (state: {state}); "
                     f"poll /api/{cache.sha256}/status")

    def source_path(cache: BinaryCache) -> str:
        # meta.json is rewritten throughout analysis; on Windows a read can
        # catch it mid-replace and come back None. Retry before giving up.
        meta = {}
        for _ in range(3):
            meta = cache.meta() or {}
            if meta.get("source"):
                break
            time.sleep(0.03)
        src = meta.get("source", {})
        path = (str(cache.path("file.bin")) if src.get("stored")
                else src.get("path"))
        if not path or not os.path.exists(path):
            raise HTTPException(410, "source file no longer available")
        return path

    # ------------------------------------------------------------ open

    @app.post("/api/open")
    async def open_binary(request: Request):
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/octet-stream"):
            # Streamed to disk while hashing: buffering the body would hold
            # the whole upload in RAM, and files can be larger than RAM (P12)
            root_dir = Path(root())
            root_dir.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            n = 0
            fd, tmp_path = tempfile.mkstemp(dir=root_dir, suffix=".upload")
            try:
                with os.fdopen(fd, "wb") as f:
                    async for chunk in request.stream():
                        h.update(chunk)
                        n += len(chunk)
                        f.write(chunk)
                if n == 0:
                    raise HTTPException(400, "empty upload")
                sha = h.hexdigest()
                cache = BinaryCache(sha, root())
                dst = cache.path("file.bin")
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(tmp_path, dst)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            state = app.state.jobs.ensure(
                sha, str(cache.path("file.bin")), root(), stored=True)
            return {"id": sha, "state": state}

        try:
            doc = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "send JSON {\"path\": ...} or an "
                                     "application/octet-stream body")
        path = doc.get("path") if isinstance(doc, dict) else None
        if not path:
            raise HTTPException(400, "missing \"path\"")
        if not os.path.isfile(path):
            raise HTTPException(404, f"no such file: {path}")
        sha = sha256_file(path)
        state = app.state.jobs.ensure(sha, os.path.abspath(path), root(),
                                      stored=False)
        return {"id": sha, "state": state}

    @app.get("/api/files")
    def list_files(dir: str):
        if not os.path.isdir(dir):
            raise HTTPException(404, f"no such directory: {dir}")
        entries = []
        with os.scandir(dir) as it:
            for e in sorted(it, key=lambda e: e.name.lower()):
                if e.is_file():
                    entries.append({"name": e.name,
                                    "path": os.path.abspath(e.path),
                                    "size": e.stat().st_size})
        return {"dir": os.path.abspath(dir), "files": entries}

    # ---------------------------------------------------------- status

    @app.get("/api/{id}/status")
    def status(id: str):
        meta = get_cache(id).meta() or {}
        return {k: meta.get(k) for k in
                ("sha256", "size", "state", "error", "artifacts",
                 "tool_version", "source", "progress")}

    @app.get("/api/{id}/model")
    def model(id: str):
        cache = get_cache(id)
        require(cache, "model")
        return Response(cache.read_bytes("model.json"),
                        media_type="application/json")

    # --------------------------------------------------------- signals

    @app.get("/api/{id}/signals")
    def signals(id: str):
        from .signals import SIGNALS

        cache = get_cache(id)
        out = []
        for name, (window, stride, _key, unit, lo, hi) in SIGNALS.items():
            rel = f"signals/{name}.f32"
            ready = cache.exists(rel)
            out.append({
                "name": name, "unit": unit, "window": window,
                "stride": stride, "lo": lo, "hi": hi, "ready": ready,
                "windows": cache.path(rel).stat().st_size // 4
                if ready else None,
            })
        return {"signals": out}

    @app.get("/api/{id}/signal/{name}")
    def signal(id: str, name: str, n: int = DEFAULT_SIGNAL_BINS,
               start: int = 0, end: int = -1):
        from .signals import SIGNALS
        from .stats import reduce_minmeanmax

        if name not in SIGNALS:
            raise HTTPException(404, f"unknown signal {name!r}; "
                                     f"known: {sorted(SIGNALS)}")
        cache = get_cache(id)
        require(cache, "signals")
        n = max(1, min(n, 100_000))
        size = (cache.meta() or {}).get("size", 0)
        end = size if end < 0 else min(end, size)
        start = max(0, min(start, end))

        if cache.path(f"signals/{name}.f32").stat().st_size == 0:
            sel = np.zeros(0, dtype=np.float32)   # file shorter than window
            i0 = i1 = 0
        else:
            values = cache.memmap(f"signals/{name}.f32", "<f4")
            offsets = cache.memmap(f"signals/{name}.i64", "<i8")
            i0 = int(np.searchsorted(offsets, start, "left"))
            i1 = int(np.searchsorted(offsets, end, "left"))
            sel = np.asarray(values[i0:i1], dtype=np.float32)
        if sel.size:
            mins, means, maxs = reduce_minmeanmax(sel, n)
        else:
            mins = means = maxs = np.zeros(n, dtype=np.float32)
        payload = np.concatenate([mins, means, maxs]).astype("<f4").tobytes()
        window, stride, _k, unit, lo, hi = SIGNALS[name]
        return Response(payload, media_type="application/octet-stream",
                        headers=_meta_headers({
                            "name": name, "unit": unit, "n": n,
                            "window": window, "stride": stride,
                            "lo": lo, "hi": hi, "start": start, "end": end,
                            "windows": i1 - i0, "layout": "min|mean|max f32",
                        }))

    # ------------------------------------------------------ histograms

    @app.get("/api/{id}/hist")
    def hist(id: str, n: int = 1, dtype: str = "u8",
             start: int = 0, end: int = -1):
        if n not in (1, 2):
            raise HTTPException(400, "n must be 1 or 2 (trigram: /hist3)")
        cache = get_cache(id)
        size = (cache.meta() or {}).get("size", 0)
        whole = start == 0 and (end < 0 or end >= size)
        if whole and dtype == "u8":
            require(cache, "hist")
            return Response(cache.read_bytes(f"hist/{n}_u8.bin"),
                            media_type="application/octet-stream",
                            headers=_meta_headers({
                                "n": n, "dtype": "u8", "start": 0,
                                "end": size,
                                "quantise": {"method": "identity"},
                            }))
        counts, qmeta, start, end = _compute_hist(
            source_path(cache), n, dtype, start, end)
        return Response(np.ascontiguousarray(counts).astype("<u4").tobytes(),
                        media_type="application/octet-stream",
                        headers=_meta_headers({
                            "n": n, "dtype": dtype, "start": start,
                            "end": end, "quantise": qmeta,
                        }))

    @app.post("/api/{id}/hist/locate")
    async def hist_locate(id: str, request: Request):
        """Brush-to-locate: where in the file do these byte pairs occur?

        Body: {first0, first1, second0, second1, dtype?, start?, end?, n?}
        — an inclusive rect of quantised bigram cells (first = element i,
        second = element i+1, matching /hist n=2 axes). Returns n uint32
        bins of match counts over [start, end), so the overall/plot views
        can highlight the offsets behind a bigram structure.
        """
        cache = get_cache(id)
        try:
            doc = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(400, "JSON body required")
        if not isinstance(doc, dict):
            raise HTTPException(400, "JSON object body required")

        def cell(key: str) -> int:
            try:
                return max(0, min(255, int(doc.get(key, 0))))
            except (TypeError, ValueError):
                raise HTTPException(400, f"bad cell value for {key!r}")

        f0, f1 = sorted((cell("first0"), cell("first1")))
        s0, s1 = sorted((cell("second0"), cell("second1")))
        dtype = doc.get("dtype", "u8")
        n = max(1, min(int(doc.get("n", 2048)), 100_000))
        payload, meta = _compute_locate(
            source_path(cache), dtype, int(doc.get("start", 0)),
            int(doc.get("end", -1)), (f0, f1, s0, s1), n)
        return Response(payload, media_type="application/octet-stream",
                        headers=_meta_headers(meta))

    @app.get("/api/{id}/hist3")
    def hist3(id: str, threshold: int = 1, dtype: str = "u8",
              start: int = 0, end: int = -1, limit: int = 0):
        cache = get_cache(id)
        size = (cache.meta() or {}).get("size", 0)
        threshold = max(1, threshold)
        limit = max(0, limit)
        whole = start == 0 and (end < 0 or end >= size)
        if whole and dtype == "u8":
            require(cache, "trigram")
            if cache.path("trigram.sparse").stat().st_size == 0:
                return Response(b"", media_type="application/octet-stream",
                                headers=_meta_headers({
                                    "points": 0, "total_points": 0,
                                    "threshold": threshold, "dtype": "u8",
                                    "capped": False,
                                    "layout": "[x,y,z,count] i32",
                                }))
            pts = cache.memmap("trigram.sparse", TRIGRAM_DTYPE)
            pts = pts.reshape(-1, 4)
            # the stored artifact itself may be a densest-first prefix of
            # the true point set (P12 size cap); the sidecar has the truth
            side = {}
            if cache.exists("trigram.meta.json"):
                try:
                    side = cache.read_json("trigram.meta.json")
                except (OSError, json.JSONDecodeError):
                    side = {}
            total_points = int(side.get("total_points", len(pts)))
            # count-descending on disk: threshold and limit are prefix slices
            cut = int(np.searchsorted(-pts[:, 3], -threshold, "right"))
            capped = (bool(limit) and cut > limit) \
                or bool(side.get("capped", False))
            if limit and cut > limit:
                cut = limit
            return Response(pts[:cut].tobytes(),
                            media_type="application/octet-stream",
                            headers=_meta_headers({
                                "points": cut, "total_points": total_points,
                                "threshold": threshold, "dtype": "u8",
                                "capped": capped,
                                "layout": "[x,y,z,count] i32",
                            }))
        payload, meta = _compute_hist3(
            source_path(cache), dtype, start, end, threshold, size, limit)
        return Response(payload, media_type="application/octet-stream",
                        headers=_meta_headers(meta))

    # -------------------------------------------------------- surfaces

    @app.get("/api/{id}/surface/{name}")
    def surface(id: str, name: str, request: Request,
                start: int = 0, end: int = -1, w: int = 512, h: int = 512,
                dtype: str = "u8"):
        from .surfaces import SurfaceRequest, get_surface

        cache = get_cache(id)
        require(cache, "model")   # analysis running is fine; bytes suffice
        try:
            surf = get_surface(name)
        except KeyError as e:
            raise HTTPException(404, str(e))
        reserved = {"start", "end", "w", "h", "dtype"}
        params = {k: _typed(v) for k, v in request.query_params.items()
                  if k not in reserved}
        path = source_path(cache)
        with MappedFile.open(path) as mf:
            req = SurfaceRequest(start, end, w, h, dtype, params) \
                .clamp(mf.size)
            if name == "dotplot":
                return _dotplot_response(app, cache, surf, mf, req)
            return _surface_response(cache, surf, mf, req, name)

    @app.get("/api/{id}/image/stride")
    def image_stride(id: str, start: int = 0, end: int = -1,
                     mode: str = "grey8", top: int = 3):
        """Autocorrelation stride suggester (§5.7): top candidate row
        strides for the range, in bytes and in pixels for `mode`."""
        from .surfaces.image import parse_mode, suggest_stride_pixels

        cache = get_cache(id)
        try:
            parse_mode(mode)
        except ValueError as e:
            raise HTTPException(400, str(e))
        top = max(1, min(top, 10))
        with MappedFile.open(source_path(cache)) as mf:
            end = mf.size if end < 0 else min(end, mf.size)
            start = max(0, min(start, end))
            cands = suggest_stride_pixels(
                mf.view[start:end], mode, top=top)
        return {"start": start, "end": end, "mode": mode,
                "candidates": cands}

    # ------------------------------------------------------------- cfg

    @app.get("/api/{id}/functions")
    def functions(id: str):
        cache = get_cache(id)
        require(cache, "functions")
        return Response(cache.read_bytes("functions.json"),
                        media_type="application/json")

    @app.get("/api/{id}/cfg/{va}")
    def cfg(id: str, va: str):
        cache = get_cache(id)
        require(cache, "functions")
        try:
            addr = int(va, 0)
        except ValueError:
            raise HTTPException(400, f"bad VA {va!r}")
        rel = f"cfg/{addr:x}.json"
        if not cache.exists(rel):
            raise HTTPException(404, f"no recovered function at {addr:#x}")
        return Response(cache.read_bytes(rel), media_type="application/json")

    # ----------------------------------------------------------- bytes

    @app.get("/api/{id}/bytes")
    def raw_bytes(id: str, off: int = 0,
                  length: int = Query(4096, alias="len")):
        cache = get_cache(id)
        length = max(0, min(length, MAX_BYTES_READ))
        off = max(0, off)
        with open(source_path(cache), "rb") as f:
            f.seek(off)
            data = f.read(length)
        return Response(data, media_type="application/octet-stream",
                        headers=_meta_headers({"off": off, "len": length,
                                               "returned": len(data)}))

    @app.get("/api/{id}/triage")
    def triage(id: str):
        cache = get_cache(id)
        require(cache, "triage")
        return Response(cache.read_bytes("triage.json"),
                        media_type="application/json")

    return app


# --------------------------------------------------- endpoint work bodies

def _compute_hist(path: str, n: int, dtype: str, start: int, end: int):
    from .elements import DTYPES, elements, quantise
    from .stats import ngram

    if dtype not in DTYPES:
        raise HTTPException(400, f"unknown dtype {dtype!r}; known: "
                                 f"{list(DTYPES)}")
    with MappedFile.open(path) as mf:
        end = mf.size if end < 0 else min(end, mf.size)
        start = max(0, min(start, end))
        vals = elements(mf.view[start:end], dtype)
        bins, qmeta = quantise(vals, dtype)
        del vals
        counts = ngram(bins, n).copy()
        del bins
    return counts, qmeta, start, end


_LOCATE_CHUNK = 1 << 22


def _locate_density(bins: np.ndarray, rect: tuple[int, int, int, int],
                    n: int, span: int, ebits: int):
    f0, f1, s0, s1 = rect
    density = np.zeros(n, dtype=np.int64)
    matches = 0
    pairs = max(0, len(bins) - 1)
    # Chunked so a rect matching the whole file never materialises a
    # full-size index array (the mask is the only per-chunk transient).
    for i0 in range(0, pairs, _LOCATE_CHUNK):
        i1 = min(i0 + _LOCATE_CHUNK, pairs)
        first = bins[i0:i1]
        second = bins[i0 + 1:i1 + 1]
        mask = (first >= f0) & (first <= f1) \
            & (second >= s0) & (second <= s1)
        idx = np.flatnonzero(mask).astype(np.int64)
        if idx.size == 0:
            continue
        idx += i0
        matches += idx.size
        byte_off = (idx * ebits) >> 3          # element index -> byte offset
        density += np.bincount(byte_off * n // span, minlength=n)
    return density, matches


def _compute_locate(path: str, dtype: str, start: int, end: int,
                    rect: tuple[int, int, int, int], n: int):
    from .elements import DTYPES, element_width_bits, elements, quantise

    if dtype not in DTYPES:
        raise HTTPException(400, f"unknown dtype {dtype!r}; known: "
                                 f"{list(DTYPES)}")
    f0, f1, s0, s1 = rect
    ebits = element_width_bits(dtype)
    with MappedFile.open(path) as mf:
        end = mf.size if end < 0 else min(end, mf.size)
        start = max(0, min(start, end))
        span = max(1, end - start)
        vals = elements(mf.view[start:end], dtype)
        bins, qmeta = quantise(vals, dtype)
        del vals
        pairs = max(0, len(bins) - 1)
        # in a helper so every mmap view dies before MappedFile.close()
        density, matches = _locate_density(bins, rect, n, span, ebits)
        del bins
    return density.astype("<u4").tobytes(), {
        "n": n, "dtype": dtype, "start": start, "end": end,
        "rect": {"first0": f0, "first1": f1, "second0": s0, "second1": s1},
        "matches": matches, "pairs": pairs, "quantise": qmeta,
        "layout": "density u32",
    }


def _compute_hist3(path: str, dtype: str, start: int, end: int,
                   threshold: int, size: int, limit: int = 0):
    from .cache import pack_trigram
    from .elements import DTYPES, elements, quantise
    from .stats import ngram

    if dtype not in DTYPES:
        raise HTTPException(400, f"unknown dtype {dtype!r}; known: "
                                 f"{list(DTYPES)}")
    with MappedFile.open(path) as mf:
        end = mf.size if end < 0 else min(end, mf.size)
        start = max(0, min(start, end))
        vals = elements(mf.view[start:end], dtype)
        bins, qmeta = quantise(vals, dtype)
        del vals
        coords, counts = ngram(bins, 3)
        coords, counts = coords.copy(), counts.copy()
        del bins
    total = int(len(counts))
    keep = counts >= threshold
    coords, counts = coords[keep], counts[keep]
    capped = bool(limit) and len(counts) > limit
    if capped:
        # densest first, matching the cached whole-file ordering
        top = np.argsort(-counts.astype(np.int64), kind="stable")[:limit]
        coords, counts = coords[top], counts[top]
    payload = pack_trigram(coords, counts)
    return payload, {
        "points": int(len(counts)), "total_points": total,
        "threshold": threshold, "dtype": dtype, "start": start, "end": end,
        "capped": capped, "quantise": qmeta, "layout": "[x,y,z,count] i32",
    }


def _surface_response(cache: BinaryCache, surf, mf, req, name: str):
    key = hashlib.blake2b(repr((name, req.cache_key())).encode(),
                          digest_size=12).hexdigest()
    meta_rel = f"rasters/{key}.json"
    if cache.exists(meta_rel):
        side = cache.read_json(meta_rel)
        rel = f"rasters/{key}." + ("png" if side["kind"] == "rgb" else "raw")
        if cache.exists(rel):
            media = ("image/png" if side["kind"] == "rgb"
                     else "application/octet-stream")
            return Response(cache.read_bytes(rel), media_type=media,
                            headers=_meta_headers(side))

    raster = surf.render(mf.view, req)
    raster.pixels = np.ascontiguousarray(raster.pixels)   # off the mmap
    side = {"kind": raster.kind, "shape": list(raster.pixels.shape),
            "surface": name, "meta": raster.meta}
    if raster.kind == "rgb":
        import io

        from PIL import Image

        bio = io.BytesIO()
        Image.fromarray(raster.pixels, "RGB").save(bio, "PNG")
        payload, media, ext = bio.getvalue(), "image/png", "png"
    else:
        payload = raster.pixels.tobytes()
        media, ext = "application/octet-stream", "raw"
    cache.write_bytes(f"rasters/{key}.{ext}", payload)
    cache.write_json(meta_rel, _jsonable(side))
    return Response(payload, media_type=media, headers=_meta_headers(side))


def _dotplot_response(app, cache: BinaryCache, surf, mf, req):
    from .surfaces.dotplot import DEFAULT_WINDOW

    p = req.params
    k = int(p.get("window", DEFAULT_WINDOW))
    off1 = int(p.get("off1", req.start))
    end1 = int(p.get("end1", req.end))
    off2 = int(p.get("off2", off1))
    end2 = int(p.get("end2", end1))
    n1 = max(0, (end1 - off1) - k + 1)
    n2 = max(0, (end2 - off2) - k + 1)

    acc = None
    if n1 and n2:
        acc = app.state.dotplots.get_or_create(cache, req, n1, n2)
        p["accumulator"] = acc
    raster = surf.render(mf.view, req)
    raster.pixels = np.ascontiguousarray(raster.pixels)
    if acc is not None and raster.meta.get("mode") == "sampled":
        app.state.dotplots.persist(cache, req, acc)
    side = {"kind": raster.kind, "shape": list(raster.pixels.shape),
            "surface": "dotplot", "meta": raster.meta}
    return Response(raster.pixels.tobytes(),
                    media_type="application/octet-stream",
                    headers=_meta_headers(side))


app = create_app()
