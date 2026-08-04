# Striate

Binary visualiser & triage tool: linked interactive views (entropy, histograms,
image/dot-plot surfaces, control-flow graphs) over a single shared
address-space model. Static analysis only. See `PLAN.md` for the full design.

## Status

Phase 2 complete.

- **P0** project skeleton, ground-truth corpus, `binviz probe`
- **P1** address-space model (`binviz model`) — regions, symbols, off↔va
  mapping, gap/overlay materialisation, raw fallback for malformed input
- **P2** element stream + statistics core (`binviz signal`, `binviz hist`) —
  dtype reinterpretation incl. packed 12-bit, quantisation with recorded
  method, windowed entropy, n-grams (sparse trigram), named signals, and
  corpus-calibrated window classification

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
```

Sample plates live in `docs/plates/` — regenerate them with the `--png`
commands above.

The corpus cross-compiles ELF samples with `zig cc`, so no Linux toolchain is
needed on Windows/macOS — samples are parsed, never executed.
