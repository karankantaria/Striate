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

Plates live in `docs/plates/` — regenerate with `python docs/make_plates.py`.
Start with `image_rgb_bars.png` next to `image_rgb_bars_wrong_stride.png`
(why the stride suggester exists), and `hilbert_byteclass_static.png` next to
`hilbert_byteclass_upx.png` (structure versus packed noise).

The corpus cross-compiles ELF samples with `zig cc`, so no Linux toolchain is
needed on Windows/macOS — samples are parsed, never executed.
