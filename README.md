# Striate

Binary visualiser & triage tool: linked interactive views (entropy, histograms,
image/dot-plot surfaces, control-flow graphs) over a single shared
address-space model. Static analysis only. See `PLAN.md` for the full design.

## Status

Phase 3 complete.

- **P0** project skeleton, ground-truth corpus, `binviz probe`
- **P1** address-space model (`binviz model`) — regions, symbols, off↔va
  mapping, gap/overlay materialisation, raw fallback for malformed input
- **P2** element stream + statistics core (`binviz signal`, `binviz hist`) —
  dtype reinterpretation incl. packed 12-bit, quantisation with recorded
  method, windowed entropy, n-grams (sparse trigram), named signals, and
  corpus-calibrated window classification
- **P3** surface engine (`binviz surface`, `binviz stride`) — one
  `(range, dtype, params, w, h) -> raster` protocol behind six views:
  linear/byte-class, Hilbert, image (packed formats + 24 Bayer modes),
  bigram, sparse trigram, and dot plot (exact + progressive sampled)

## Quickstart

```sh
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # POSIX: .venv/bin/pip

# build the ground-truth corpus (uses zig cc from the ziglang pip package;
# needs UPX on PATH, in $UPX, or unzipped into corpus/tools/upx-*/)
make -C corpus                            # or: python corpus/build.py

# thresholds are measured, never hardcoded (see plan §5.3)
python corpus/calibrate.py                # writes corpus/calibration.json

pytest                                    # functional suite
pytest -m perf -s                         # 100 MB performance targets

binviz probe  corpus/out/hello_O2
binviz model  corpus/out/hello_upx
binviz signal corpus/out/hello_upx --name entropy_4096 --png out.png
binviz hist   corpus/out/ramp16.bin --n 2 --dtype u16le --png bigram.png

# surfaces: -p passes surface parameters
binviz surface corpus/out/hello_static --name hilbert -p mode=byteclass --png h.png
binviz surface corpus/out/rgb_raw.bin  --name image -p mode=rgb8 -p width=320 --png i.png
binviz surface corpus/out/repeats.bin  --name dotplot -p mode=exact --png d.png
binviz stride  corpus/out/bayer_raw.bin --mode bayer_RGGB_RGB_12
```

## Running the server

```sh
binviz serve                              # 127.0.0.1:8000
```

It prints a URL containing a session token — open that. **Every `/api` route
requires the token**, because "it only listens on localhost" is not a defence
against a web page in another tab, which reaches `127.0.0.1` just like any
other origin. `SECURITY.md` has the reasoning.

File access is confined to `--root` (default: the working directory), so
paths outside it are refused.

### Limits

All four have a flag and an environment variable, and all four exist to stop
a local caller consuming more than you intended. Defaults are chosen for a
laptop; raise them if your machine is bigger.

| Flag | Env | Default | What it bounds |
|---|---|---|---|
| `--max-cache BYTES` | `BINVIZ_MAX_CACHE` | 5 GiB | Total size of cached analyses. Past this, least-recently-used entries are evicted — never one being analysed or viewed. |
| `--max-upload BYTES` | `BINVIZ_MAX_UPLOAD` | 8 GiB | Largest accepted upload. |
| `--max-analyses N` | — | 4 | Simultaneous analyses; beyond it `/api/open` returns 503. |
| `--root DIR` | — | cwd | Directory the server may read files from. |

Analyses are cached under `~/.cache/binviz` (or `$BINVIZ_CACHE`), keyed by
content hash, so reopening a binary is instant. Raise `--max-cache` if you
would rather keep more of them; the cache is safe to delete by hand at any
time — the worst case is that the next open re-analyses.

Other flags: `--token` to pin a token across restarts (useful with the Vite
dev proxy, which reads `BINVIZ_TOKEN`), `--port`, `--cache`, and `--no-auth`
for CI. `--no-auth` prints a banner telling you what it turned off; do not
use it on a machine you share.

Plates live in `docs/plates/` — regenerate with `python docs/make_plates.py`.
Start with `image_rgb_bars.png` next to `image_rgb_bars_wrong_stride.png`
(why the stride suggester exists), and `hilbert_byteclass_static.png` next to
`hilbert_byteclass_upx.png` (structure versus packed noise).

The corpus cross-compiles ELF samples with `zig cc`, so no Linux toolchain is
needed on Windows/macOS — samples are parsed, never executed.
