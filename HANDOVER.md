# binviz — session handover

> Keep this file current: whoever finishes a phase updates the status table,
> the "how to run" section if it changed, and the gotchas list. `PLAN.md` is
> the full design doc; this file is the fast on-ramp for a new session.

## Status (updated 2026-08-06, end of Phase 12)

| Phase | What | State |
|---|---|---|
| P0 | Skeleton + ground-truth corpus (`corpus/build.py` / Makefile, `binviz probe`) | ✅ done |
| P1 | `BinaryModel` address space (LIEF + raw fallback, off↔va, gaps/overlay) | ✅ done |
| P2 | ElementStream + stats + signals (dtype incl. packed u12, dual-window entropy, calibration) | ✅ done |
| P3 | Surface engine (linear, hilbert, image+bayer, ngram2/3, dotplot) — plates in `docs/plates/` | ✅ done |
| P4 | Capstone decode core (linear sweep + recursive descent, objdump differential) | ✅ done |
| P5 | Function discovery + CFG JSON (5-tier cascade, jump tables, measured stripped recall) | ✅ done |
| P6 | Content-addressed cache + FastAPI service (binary wire formats, X-Meta header) | ✅ done |
| P7 | Web shell: Overall + Zoomed + Plot + SelectionStore + hex peek | ✅ done |
| P8 | 2D/3D histogram views (bigram canvas, WebGL trigram, brush-to-locate) | ✅ done |
| P9 | Image view (stride suggester, 87 modes), dot plot (progressive), virtualised hex viewer | ✅ done |
| P10 | CFG view (elk layout in workers, Canvas2D, uncertainty encoding, function list) | ✅ done |
| P11 | Triage verdict + clickable findings panel + file navigation | ✅ done |
| **P12** | **Scale hardening (measured on 2 GiB; triggered items only)** | ✅ **done (this session)** |

## How to run

Two processes: the analysis server and the Vite dev server (no static mount yet —
the FastAPI app does **not** serve `web/dist`; dev mode is the way to run the UI).

**The API now requires a token** (S1a) and confines file access to `--root`
(S1d) — see `SECURITY.md`. Pin the token so both processes agree:

```bash
export BINVIZ_TOKEN=dev-token-not-a-secret     # any string; dev only

# 1. backend (Python 3.11+, deps already in .venv)
.venv/Scripts/python -m binviz.cli serve \
    --token "$BINVIZ_TOKEN" --root .            # 127.0.0.1:8000

# 2. frontend (Node 24; `npm install` already run in web/)
cd web && npm run dev                            # 127.0.0.1:5173, proxies /api -> :8000
# non-default backend port: BINVIZ_API=http://127.0.0.1:8377 npm run dev
```

The Vite proxy attaches `BINVIZ_TOKEN` to every proxied request, so the
browser never sees it. Without that env var, run plain `binviz serve` and open
the `?token=…` URL it prints — `src/auth.ts` stores the token and strips it
from the address bar.

`--root .` matters: paths outside it now 403. Drop it and the CLI defaults to
the working directory anyway.

Then open http://localhost:5173 and paste an absolute path (e.g.
`corpus/out/hello_upx`), or drop a file onto the window, or use
`?path=C:\...\file` in the URL. Corpus samples: `make -C corpus` or
`python corpus/build.py` (outputs in `corpus/out/`, gitignored).

Tests:
```bash
.venv/Scripts/python -m pytest -q         # backend, 334 passing (perf marked-out)
cd web && npm test                        # 45 node --test units (hilbert/off↔va/colormap/transforms/hexutil/cfgutil/escape)
cd web && npm run build                   # tsc --noEmit (strict) + vite build
```

Service tests go through `conftest.make_app` / `authed_client`, which build
an authenticated app and send the real token. Don't reach for `auth=False`
to make a new test pass — see the note in `conftest.py`.

## What Phase 7 built (`web/`)

TypeScript, no framework, Vite. Everything hangs off one `SelectionStore`
(`src/store.ts`) holding `{offsetRange, vaRange, hoveredOffset, caret}` in
absolute file offsets — every view reads and writes it, which is what makes
the views linked rather than adjacent.

- `src/api.ts` — typed fetch layer matching the P6 wire formats (X-Meta JSON
  header, raw `<f4`/`u8` payloads). **All GETs use `cache: "no-store"`** — see gotchas.
- `src/store.ts` — SelectionStore + client-side `offToVa`/`vaToOff` over the
  model's `mappings` interval table (unit-tested against the server's semantics).
- `src/colormap.ts` — viridis/magma/inferno LUTs (polynomial fits), the
  byte-class categorical palette, plot series colors. Palette was validated
  with the dataviz skill's checker in **both** themes (all-pairs): classes
  1–4 are blue `#2a78d6`/orange `#eb6834`/aqua `#1baf7a`/fuchsia `#a848b8`
  (dark steps `#3987e5/#d95926/#199e70/#c559c5`); class 0 (null) recedes
  toward the surface, class 5 (0xFF) pops as ink — lightness anchors outside
  the categorical set. Legend chips + hover tooltips are the required
  secondary encoding.
- `src/hilbert.ts` — verbatim port of `surfaces/hilbert.py` `d2xy`/`xy2d`
  (cross-checked against Python on 265 vectors; note `xy2d` rotates with full
  `n`, `d2xy` with sub-square `s` — do not "fix" this asymmetry).
- `src/canvas/raster.ts` — the one raster canvas component (raster + overlay
  canvas pair, LUT applied client-side, pointer events in cell coords,
  ResizeObserver + `sync()`).
- `src/views/overall.ts` — Overall & Zoomed are this one class, two bindings
  (`"file"` = drag-select source with the m1/m2/band marker state machine;
  `"selection"` = bound to the store). Layouts linear/hilbert; modes
  byteclass / entropy signal (`reduce=max`, never mean) / raw value.
- `src/views/plot.ts` — region ribbon + stacked signal lanes rendering the
  server's min/mean/max bands as envelope + 2px mean line; crosshair +
  tooltip; drag-select; "follow selection" x-range toggle. Aggregation is
  server-side at `n = canvas width` — never fetch-all-and-downsample.
- `src/views/hex.ts` — 512 B hex peek (deleted in P9; see `views/hexview.ts`).
- `src/views/info.ts` — model summary, warnings, clickable region list
  (region click drives the SelectionStore).
- `src/main.ts` — open (path / upload / `?path=`), status polling with
  progressive view enablement, theme toggle (stamps `data-theme`, views
  re-LUT without refetching).
- `web/test/*.test.ts` — run by `node --test` (Node 24 strips types natively;
  relative imports need explicit `.ts` extensions, tsconfig has
  `allowImportingTsExtensions`).
- `tests/test_phase7.py` — the PLAN's headless spike regression at the wire
  level: 4 KiB random block in 32 MiB of zeros survives binning in the `max`
  band (>7.5 bits/byte) while the mean band would hide it (<1.0).

## What Phase 8 built

Backend (`src/binviz/service.py`, tested in `tests/test_phase8.py`):
- `POST /api/{id}/hist/locate` — brush-to-locate. JSON body
  `{first0, first1, second0, second1, dtype?, start?, end?, n?}` (inclusive
  cell rect, first = element i / second = element i+1, matching /hist n=2
  axes) → n uint32 density bins over [start, end). Chunked so a
  whole-file-matching rect never materialises a full-size index array; all
  mmap views die inside a helper before `MappedFile.close()` (Windows
  BufferError otherwise — same trap as `_compute_hist`).
- `GET /hist3` grew `limit=` — a prefix cap. Whole-file u8 responses are
  count-descending on disk so it's a slice; capped computed subranges are
  sorted densest-first to match. Meta gains `capped`. The frontend always
  passes `limit=1e6`: a 100 MB random file at threshold 1 would otherwise
  ship ~268 MB of points.

Frontend:
- `src/transforms.ts` — client-side mirror of `ngram.py:to_display`
  (log1p/rank/sqrt/linear, peak-normalised, numpy-truncation semantics;
  unit-tested in `web/test/transforms.test.ts`). Transforms apply
  client-side over raw /hist counts so switching modes never refetches.
- `src/views/hist2d.ts` — 256×256 bigram canvas (pixelated), axis ticks at
  00/20/41/7f/ff, tooltip with counts + quantise bin ranges, brush →
  locate → `store.setLocate`, click clears. Recomputes on selection +
  dtype. Orientation: counts are `[first*256+second]`, drawn x = b[i],
  y = b[i+1] (transpose happens in `renderScratch`).
- `src/views/hist3d.ts` — raw WebGL2 point cloud. Viridis evaluated in the
  shader (same polynomial as colormap.ts), size/colour from log count,
  `overlap` = additive blending with depth-write off vs depth-tested
  opaque, trackball + wheel zoom + idle auto-spin (resumes 2.5 s after the
  last interaction), cube wireframe with labels projected onto a 2D
  overlay via the same MVP. Additive intensity is dimmed by point count
  (`u_dim`) so a 1M-point uniform cloud reads as a density gradient, not a
  white cube.
- `SelectionStore` gained `dtype` (shared element dtype — P9's image view
  should read it too) and `locate` (density highlight) + events. Overall
  and plot views tint locate density in the attention orange
  (`LOCATE_RGB`), alpha scaled by per-bin density.
- New Bigram/Trigram panes in a third grid row; dtype picker lives in the
  Bigram pane head but writes the shared store. Picker resets to u8 on
  open (store.setModel resets state).

Verified live in the browser: hello_static bigram/trigram + brush-to-locate
highlighting in overall+plot, selection-driven recompute, ramp16.bin as
u16le collapsing to the diagonal (and the trigram to a 3-D diagonal line),
urandom filling the cube vs ascii.txt corner cluster. Plates for the
dtype criterion: `docs/plates/ngram2_ramp16_{u8,u16le}.png`.

## What Phase 9 built

Backend (`src/binviz/service.py`, tested in `tests/test_phase9.py`):
- `GET /api/{id}/image/stride?start&end&mode&top` — exposes the P3
  autocorrelation stride suggester (`surfaces/image.py:suggest_stride_pixels`),
  candidates in bytes *and* pixels for the given mode. rgb_raw top candidate
  is 320 px; bayer's true 640 px stride only surfaces as a peak sub-multiple
  (the CFA repeats every two rows) — the engine handles that, don't "fix" it.
- test_phase9.py also pins the PNG path of /surface/image and the dotplot
  progressive contract (advancing cursor → monotone resolved/progress/hits).

Frontend:
- `src/api.ts` — `getSurfaceRgb` (PNG → ImageBitmap + X-Meta) and
  `getStrideSuggestions`.
- `src/views/image.ts` — all 15 packed + 72 Bayer modes (optgroups,
  generated); width/offset/invert controls; suggester top-3 as one-click
  buttons (pixel units per mode, inexact ones greyed); `sel→w` helper;
  follows the selection until the user types an offset (any new selection
  re-anchors); hover tooltip maps pixel → offset/VA/region, click sets the
  caret. PNG drawn pixelated at integer upscales.
- `src/views/dotplot.ts` — axis pickers (selection/file per axis), window k,
  samples-per-pass, refine toggle. Sampled mode drives the server's
  progressive accumulator with an advancing `cursor` until progress hits 1
  (REFINE_DELAY_MS between passes, MAX_PASSES cap); the exact/sampled mode +
  progress + hits label is persistent. RasterCanvas fit:"square", viridis.
- `src/views/hexview.ts` + `hexutil.ts` — virtualised hex viewer replacing
  the P7 peek (`views/hex.ts` deleted). Only visible rows render; bytes come
  through a 64×16 KiB LRU page cache (measured: end+middle jumps on
  hello_static fetched 48 KiB). Annotation gutter shows region + symbol at
  each row (labels printed on change), selection highlights and scrolls into
  view, click sets caret. hexutil.ts is DOM-free (scroll map, page span,
  symbol bisect) and node --test covered. The scroll map compresses the
  spacer under Chrome's ~33.5M px element-height cap (MAX_SPACER_PX) and
  scales scrollTop back to rows — 1 GiB files scroll fine.
- Layout is now a 4-row grid (hex spans rows 2–4); `#layout` scrolls
  vertically on smaller screens.

Verified live: rgb_raw colour bars at stride 320 (suggested) vs shear at a
wrong stride; bayer_raw smooth at RGGB_12 + 640 px vs channel-swapped at
GRBG; repeats.bin dot plot showing the diagonal + 3 off-diagonal band pairs
refining to 100%; drag-select in Overall scrolling/highlighting the hex view
and re-anchoring the image view; no console errors.

## What Phase 10 built

No backend changes (`/functions` and `/cfg/{va}` were P5/P6 work). All
frontend; `elkjs` is now a dependency.

- `src/views/cfgutil.ts` — DOM-free helpers, node --test covered
  (`test/cfgutil.test.ts`): monospace block sizing (one measured char
  width, no per-string measurement; >30-line blocks elide the middle),
  `prepareGraph` (CFG doc → ELK nodes/edges, plus a "?" sentinel node and
  dashed `indirect_unresolved` edge for each block containing an
  unresolved record), `HitGrid` (uniform-grid hit index — the PLAN's
  quadtree role, simpler), and viewport maths (`fitTransform`, `zoomAt`
  anchored zoom, scale clamps, `TEXT_MIN_SCALE = 0.4` LOD threshold).
- `src/workers/layout.worker.ts` — my protocol worker ({seq, nodes,
  edges} in, positions + orthogonal bendpoints out) wrapping **elk-api
  with a workerFactory that spawns `elk-worker.min.js?worker` as a nested
  worker** (see gotcha 13 — elk.bundled.js does NOT survive Vite). ELK
  options: layered, DOWN, ORTHOGONAL routing, NETWORK_SIMPLEX layering,
  BRANDES_KOEPF placement.
- `src/views/cfg.ts` — the view. Function list (search filter +
  "selection only" checkbox filtering by VA-overlap with the store
  selection; discovery-tier badges, quiet for symbol/entry, orange for
  prologue/gap_sweep/partial; unclaimed-block count in the footer),
  packed banner (rendered instead of garbage functions when
  `functions.packed`), Canvas2D graph render (block header = va +
  terminator, mnemonic-column instruction text, dashed borders for
  low-confidence blocks, entry block in accent, blocks overlapping the
  selection tinted, edge ink true/false = aqua/orange, dashed fuchsia to
  the "?" sentinel), pan/drag (3 px threshold vs click), wheel zoom at
  cursor, hover tooltip (block va range/file off/region/terminator/
  confidence; sentinel explains "hole in the graph"). Layout results
  cached per function VA — reopening a function never re-lays-out; stale
  worker replies dropped by seq.
- Linkage: clicking a block `setSelection`s its file range + caret —
  verified live: zoomed/hex/bigram/trigram/dotplot all follow; hovering
  a block sets `hoveredOffset`. Selection made elsewhere filters the
  function list ("selection only") and tints overlapping blocks.
- Auto-opens `main`, else the entry function, else the first.
- `index.html`/`theme.css`: fifth grid row `"cfg cfg cfg"`; `#cfg-body`
  = list (240 px) + canvas; `.fn-badge`/`.cfg-banner`/`.cfg-note` styles.

Verified live: hello_O0 main (18 blocks, layout 76–194 ms — criterion is
<200 ms), block click driving every view, selection filtering 885→1
functions, switchy `dispatch` fully resolved (24 blocks/43 edges — the P5
jump-table matcher got all 20 targets, so no sentinel there), switchy PLT
thunks showing the dashed "?" sentinel for `jmp [rip+…]`, hello_upx
showing the packed banner with 7 explained stub functions, theme toggle
clean, no console errors.

## What Phase 11 built

Backend (`src/binviz/triage.py`, tested in `tests/test_phase11.py`):
- `triage(buf, model, functions, cal)` → the PLAN §P11 verdict document:
  `{verdict, confidence, findings[], format, size}`, each finding
  `{severity, code, detail, offsets}` with half-open file offsets where
  navigable. Codes: HIGH_ENTROPY_EXEC / TRUNCATED (high, verdict-driving),
  OVERLAY_PRESENT (high only when ≥20% of file AND ≥64 KiB — a 1 KiB tail
  on a 4 KiB hello is alignment slack, not a payload), IMPORT_STARVED,
  WX_REGION, ENTRY_OUTSIDE_EXEC, VSIZE_EXCEEDS_RAW (medium), SECTIONLESS,
  LOW_FUNCTION_DENSITY, HIGH_ENTROPY_NONEXEC, EMBEDDED_IMAGE_LIKELY (low).
- Region awareness is the FP guard: high entropy in *executable* regions
  (≥50% of decision windows over `packed_h_min` from calibration.json) →
  likely_packed; the same entropy in data regions → low-severity
  HIGH_ENTROPY_NONEXEC that never tips the verdict (sample.zip test).
- **TRUNCATED is read from the ELF header directly** (e_shoff +
  shentsize·shnum > EOF): LIEF reparses a truncated ELF into a smaller
  *self-consistent* model with no clamp warnings, so parse warnings alone
  miss it.
- EMBEDDED_IMAGE_LIKELY gates on the *max* suggest_stride peak score
  ≥ 0.75 (rgb/bayer measure 0.84–1.0; ELF .rodata/.debug_* noise peaks at
  0.6–0.7) + non-random entropy; reported stride is `cands[0]` which for
  Bayer is the 2× row-pair lag (see gotcha in P9 notes).
- `triage` is a cached artifact (ARTIFACTS grew; TOOL_VERSION bumped to
  0.0.2 so every pre-P11 cache dir invalidates on next open). Endpoint
  `GET /api/{id}/triage` serves triage.json (409 until ready). CLI:
  `binviz triage <file> [--json]`.

Frontend:
- `src/views/triage.ts` — verdict banner (colour-coded) + findings list in
  the side pane above model info. Clicking a finding with offsets does
  `setSelection` + `setCaret` — every view follows; active finding
  highlights when the selection matches it exactly.
- File navigation (`main.ts`): prev/next buttons + `[` / `]` keys walk the
  open file's directory via `GET /api/files` (position shown as `n/m`;
  disabled for uploads), recent-files list (localStorage, max 10) feeds a
  datalist on the path input, drag-drop unchanged.
- View config persists across files AND reloads: `store.setModel` now
  keeps `dtype` (selection state still resets — main.ts broadcasts
  `setSelection/setCaret/setLocate(null)` after reset so no view keeps the
  previous file's state), and overall layout/mode, bigram display, dtype
  are saved to localStorage (`binviz-viewcfg`) and restored at boot by
  dispatching change events after listeners are wired.

Verified live: hello_upx LIKELY PACKED 0.81 with clickable
HIGH_ENTROPY_EXEC driving zoomed/hex/bigram/trigram/dotplot; hello_O2
LIKELY BENIGN 0.80; ramp16.bin nav'd to with `]` keeping u16le (bigram
collapses to the diagonal on arrival); warm next/prev paint 126 ms
(criterion < 500 ms); no console errors.

## Gotchas discovered this session (read before touching P9–P10)

1. **Never let the browser HTTP-cache `/api`.** 404 and 410 are
   *heuristically cacheable* per RFC 7231 — a transient mid-analysis 410 got
   cached by Chrome and the surface view stayed "gone" forever, with requests
   never reaching the server (network log showed the request, server log
   didn't). Fixed on both sides: `Cache-Control: no-store` middleware in
   `service.py`, and `fetch(..., {cache: "no-store"})` in `api.ts`. Keep both
   for any new endpoint/view.
2. **meta.json read race on Windows.** meta is rewritten throughout analysis
   via `os.replace`; a concurrent read can transiently see nothing →
   `source_path()` used to 410. It now retries ×3 (`service.py`). Frontend
   surface fetches additionally retry on 409/410 every 700 ms.
   **Update (S6 work):** `require()` had the same bug and nobody had noticed
   — a transient empty read made it report a *ready* artifact as not ready,
   i.e. an intermittent spurious 409. Both now go through
   `_meta_or_retry(cache, key)`. If you add another meta.json reader on a
   request path, use that helper; a bare `cache.meta()` will bite you only
   under concurrency, which means only in production.
3. **Don't trust ResizeObserver ordering after unhiding a container.** The
   initial "layout unhidden → RO fires → cached size becomes valid" chain is
   not reliable; `RasterCanvas.sync()` pulls `clientWidth` from the DOM at
   fetch time instead. New raster views should do the same.
4. **`hilbert` surface has no `signal` mode** (backend `hilbert.py` supports
   byteclass/value only) — the UI disables the Entropy option under Hilbert.
   Also hilbert ignores requested w/h and returns `side×side` (side =
   2^⌊log2 min(w,h)⌋) — size canvases from `X-Meta.shape`, not the request.
5. **Signal-mode pixel scaling**: scalar signal rasters are
   `(v-lo)·255/(hi-lo)` (`meta.value_range` = [lo,hi]); byteclass rasters are
   class ids 0–5 aggregated by *mode* (ties → lowest id).
6. Vite's file watcher occasionally missed edits mid-session (Windows);
   if the browser seems to run stale code, restart `npm run dev` (add
   `--force` to also drop the prebundle cache).
7. **numpy views of the mmap must all die before `MappedFile.close()`.**
   For u8, `quantise()` returns a *view* of the file; any slice of it
   (loop variables included) keeps the mmap pinned and close() raises
   BufferError — on Windows this surfaces as a 500. Do per-chunk work in
   a helper function so its locals are freed on return (see
   `_locate_density`), and `del` the top-level arrays before leaving the
   `with` block.
8. **Trigram wire size needs the `limit` param.** `/hist3?threshold=1` on
   high-entropy input is ~16.7M cells × 16 B. Always pass a limit from the
   UI; the response is densest-first either way, and `meta.capped` says
   when you're not seeing everything (hist3d shows "densest shown").
9. Corpus `ramp16.bin` is `<u2` arange over 128 Ki elements, i.e. it
   **wraps mod 65536** — as u16le its bigram is the diagonal *plus* a few
   (255, 0) wrap cells. Tests that assert pure diagonality build their own
   non-wrapping ramp (see `tests/test_phase8.py`).
10. **Hex bytes render as `··` placeholders until their page fetch lands**,
    then a queued rAF repaint fills them in — a screenshot taken in that
    window looks broken but isn't. If placeholders *persist*, then look at
    the page cache (`hexview.ts:fetchPage`), not the renderer.
11. A dot plot that is one solid colour is not automatically a bug:
    rgb_raw.bin repeats every row 240×, so at k=8 essentially every cell
    has a match and log1p-normalised display saturates. Check
    `meta.lit_cells` against the raster size before diagnosing.
12. `ImageBitmap`s must be `.close()`d when replaced (image view does);
    leaking them holds GPU memory across refetches.
13. **`elkjs/lib/elk.bundled.js` breaks under Vite's CJS prebundling** —
    its internal fake-Worker shim comes out as "_Worker is not a
    constructor" the moment `new ELK()` runs (and a worker that dies this
    way is *silent*: no console error, layout just never returns — attach
    `worker.onerror` when debugging). The working recipe is
    `import ELK from "elkjs/lib/elk-api.js"` plus
    `import ElkEngineWorker from "elkjs/lib/elk-worker.min.js?worker"`
    and `new ELK({ workerFactory: () => new ElkEngineWorker() })`.
    Nested workers (ours spawns elk's) are fine in Chrome, dev and build.
14. P9's image view intentionally does NOT follow the shared
    `SelectionStore.dtype` — pixel formats (rgb8, bayer…) are a different
    axis than element dtypes; the mode picker is its own control.

## What Phase 12 built (scale hardening — measured triggers only)

Method: PLAN §P12 forbids speculative optimisation, so the session started
by *measuring* a 2 GiB mixed file (valid ELF head + 1.2 GiB tiled ELF +
512 MiB urandom + 256 MiB zeros + 64 MiB ascii; generator logic preserved
in `tests/test_phase12.py`'s docstring) on a 16 GB Windows machine. What
tripped got fixed; what didn't got its number recorded and no code.

**Triggered and fixed** (backend `stats.py`/`cache.py`/`service.py`/
`surfaces/dotplot.py`, tests in `tests/test_phase12.py`):
- `stats.histogram()` ran `np.bincount` over the whole array; bincount
  casts u8 → int64 internally, i.e. 8× the file in RAM. Measured: the
  `hist` step committed **16.75 GiB** on the 2 GiB file and paged the
  machine to a crawl. Now chunked (`_TARGET_CHUNK_ELEMS`), saturating at
  u32 max to keep the `<u4` wire format honest for >4 GiB inputs.
- Trigram picked sparse-vs-dense from the **first chunk only**; a file
  that opens binary-like and turns random keeps sparse while the random
  section explodes the merge. Measured: **85 s, +10.3 GiB commit**.
  `_trigram` now switches to the blocked-dense table mid-stream when any
  chunk exceeds `_TRIGRAM_SPARSE_MAX_UNIQUE` or the running total exceeds
  `_TRIGRAM_SPARSE_TOTAL_MAX` (2^25 keys ≈ 0.8 GB merge peak, the new
  worst case). Re-measured after the fix: see the numbers below.
- Whole-file dot-plot axes: the range-2 k-mer index and the range-1
  permutation are each O(n) uint64 — **~17 GB apiece** at 2 GiB, a
  MemoryError in practice. `DotPlotAccumulator` now bounds both: above
  `ROW_SAMPLE_MAX` (2^22) axis 1 is a fixed random row subset
  (`meta.rows_sampled`); above `INDEX_MAX_POSITIONS` (2^24) there is no
  persistent index at all — each advance() streams one tile of range 2
  past the row hashes and `progress` counts tiles (`meta.tiled/tiles/
  tiles_done`). At 100% progress the tiled matrix is *bit-identical* to
  the untiled one (tested) — tiling changes memory, not meaning.
- Trigram artifact cap: 2 GiB mixed wrote a **256 MiB** trigram.sparse.
  Now capped at `TRIGRAM_STORE_MAX_POINTS` (2^22 points = 64 MiB,
  count-descending so the cap keeps the densest); `trigram.meta.json`
  records `{total_points, stored_points, capped}` and `/hist3` reports
  `capped: true` + the true total. TOOL_VERSION bumped to 0.0.3 (all
  pre-P12 caches invalidate).
- Per-step progress: signals/hist/trigram/triage report a fraction via a
  throttled writer (`cache.StepProgress`, ≥0.5 s between meta.json
  writes) → `meta.progress` → `/status` → the status chip shows
  "analyzing 2/6 · trigram 43%…". Triage progress is byte-weighted
  *within* regions (a 2 GiB overlay is one region — per-region ticks
  would jump 0→95%).
- `/api/open` uploads streamed to a temp file while hashing instead of
  `await request.body()` (whole upload in RAM); uploads can now exceed
  RAM.

**Measured and NOT triggered** (numbers recorded, no code — the PLAN's
own rule):
- Signal binning (`/signal` refetch-on-zoom): 57–75 ms for the full 2 GiB
  entropy_256 series (8.4M windows) at n=2000, ~1 ms for a 1% zoom. No
  signal pyramid.
- Sliding-window entropy: signals on 2 GiB took 17.7 s ≈ 0.86 s/100 MB —
  the P2 "2 s per 100 MB" target holds linearly. No incremental counts.
- WebGL graph: no whole-program call-graph view exists (P10 renders one
  function's CFG at a time; corpus max is tens of blocks). No trigger.
- LIEF parse transiently commits ~2× file size (+4 GiB at 2 GiB, 7.6 s) —
  LIEF-internal, survivable at the 2 GiB target, left alone. Known cost:
  the model step is the remaining per-open transient on huge files.

2 GiB step times after the fixes (same machine; commit-charge deltas —
working set additionally shows ~2 GiB of reclaimable file-backed mmap
pages during the streaming steps):
sha256 1.9 s · model 7.6 s (+4 GiB transient, LIEF) · signals 17.7 s ·
hist 22.4 s (+0.03 GiB, was +16.75 GiB) · trigram 33.3 s (+0.83 GiB, was
85 s / +10.3 GiB) · functions 0.9 s · triage 14.3 s. Full `analyze()`
end-to-end: 88.7 s warm, state=complete, progress ticking live.
trigram.sparse: 64 MiB capped (was 256 MiB). Whole-file dot plot (both
axes = the 2 GiB file, previously MemoryError): first pass 8.6 s, then
~3.8 s per tile, 128 tiles, per-pass tile progress in the meta.

Remaining PLAN open questions (unchanged): r2 oracle backend shipping
(decide from stripped recall), 3-D trigram vs three 2-D projections
(decide from the plates).
