# Striate

Binary visualiser & triage tool: linked interactive views (entropy, histograms,
image/dot-plot surfaces, control-flow graphs) over a single shared
address-space model. Static analysis only. See `PLAN.md` for the full design.

## Status

Phase 0 complete: project skeleton, ground-truth corpus, `binviz probe`.

## Quickstart

```sh
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # POSIX: .venv/bin/pip

# build the ground-truth corpus (uses zig cc from the ziglang pip package;
# needs UPX on PATH, in $UPX, or unzipped into corpus/tools/upx-*/)
make -C corpus                            # or: python corpus/build.py

pytest
binviz probe corpus/out/hello_O2
```

The corpus cross-compiles ELF samples with `zig cc`, so no Linux toolchain is
needed on Windows/macOS — samples are parsed, never executed.
