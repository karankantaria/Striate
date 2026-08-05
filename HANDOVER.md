# binviz — session handover

> Keep this file current: whoever finishes a phase updates the status table,
> the "how to run" section if it changed, and the gotchas list. `PLAN.md` is
> the full design doc; this file is the fast on-ramp for a new session.

## Status (updated 2026-08-05, end of Phase 8)

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
| **P8** | **2D/3D histogram views (bigram canvas, WebGL trigram, brush-to-locate)** | ✅ **done (this session)** |
| P9 | Image view, dot plot, full hex viewer | ⬜ next (parallel with P10) |
| P10 | CFG view (elkjs in a worker, Canvas2D) | ⬜ (parallel with P9) |
| P11 | Triage verdict + file navigation | ⬜ needs all views |

## How to run

Two processes: the analysis server and the Vite dev server (no static mount yet —
the FastAPI app does **not** serve `web/dist`; dev mode is the way to run the UI).

```bash
# 1. backend (Python 3.11+, deps already in .venv)
.venv/Scripts/python -m binviz.cli serve            # 127.0.0.1:8000

# 2. frontend (Node 24; `npm install` already run in web/)
cd web && npm run dev                                # 127.0.0.1:5173, proxies /api -> :8000
# non-default backend port: BINVIZ_API=http://127.0.0.1:8377 npm run dev
```

Open http://localhost:5173 and paste an absolute path (e.g.
`corpus/out/hello_upx`), or drop a file onto the window, or use
`?path=C:\...\file` in the URL. Corpus samples: `make -C corpus` or
`python corpus/build.py` (outputs in `corpus/out/`, gitignored).

Tests:
```bash
.venv/Scripts/python -m pytest -q         # backend, 279 passing (perf marked-out)
cd web && npm test                        # 19 node --test units (hilbert/off↔va/colormap/transforms)
cd web && npm run build                   # tsc --noEmit (strict) + vite build
```

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
- `src/views/hex.ts` — 512 B hex peek at caret/selection (full viewer is P9).
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

## Where P9/P10 plug in

P7/P8 built the pieces they import: `SelectionStore` (incl. the shared
`dtype` the image view should follow), `api.ts`, `colormap.ts`,
`canvas/raster.ts`, `transforms.ts`, the tooltip singleton, and the pane
grid in `index.html`/`theme.css`. Backend endpoints they need already
exist and are tested: `/surface/image|dotplot` (dotplot keeps a
progressive accumulator server-side, `meta.progress`), `/functions`,
`/cfg/{va}`, `/bytes`.
