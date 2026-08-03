# Binary Visualiser & Triage Tool — Implementation Plan

**Status:** design locked, ready for Phase 0
**Target reader:** a Claude Code session executing one phase per working session
**Scope:** static analysis only. No emulation, no unpacking, no symbolic execution.
**Parity goal:** every visualisation in `kentavv/binary_viewer`, plus entropy triage, plus interactive CFG.

---

## 0. What we're building

A local tool that ingests an arbitrary executable (ELF / PE / Mach-O / headerless blob) and exposes it through a set of linked interactive views over a **single shared address-space model**:

**Triage/analysis views (new):**
- **Entropy strip** — packed / compressed / encrypted / padded regions, region-aware
- **Interactive control-flow graph** — recovered functions as navigable graphs
- **Triage verdict** — machine-readable summary derived from everything else

**Structure views (full binary_viewer parity):**
- **Overall view** — whole-file map, byte-class coloured or Hilbert-curve laid out, with drag-to-select range
- **Zoomed overall view** — the same surface bound to the current selection
- **Plot view** — multiple normalised float signals over file offset
- **2D histogram** — byte-bigram matrix, any element width
- **3D histogram** — byte-trigram point cloud, rotatable
- **Image view** — raw bytes as pixels: grey/RGB/BGR/RGBA/BGRA at 8/12/16 bit, plus 24 Bayer CFA modes
- **Dot plot** — self-similarity matrix, progressive stochastic sampling
- **Hex viewer** — scrollable hex/ASCII dump

Two design principles run through everything:

1. **The analysis layer never lies about what it doesn't know.** Unresolved indirect jumps, guessed function boundaries, sampled-not-exhaustive dot plots, and packed-and-therefore-meaningless disassembly are represented explicitly in the data model and rendered as such. Most binary visualisers fail by silently smoothing over uncertainty.
2. **Parity is bought with one abstraction, not eight features.** Six of the eight structure views are the same operation — *reinterpret a byte range as elements, project to a 2-D raster* — behind different parameters. Building the `Surface` abstraction once (Phase 3) makes the whole reference feature set cheap. Building them as eight separate widgets, as the reference does, is what makes them expensive.

---

## 1. Architecture Overview

### 1.1 The shared spine: address space, then element stream, then surface

Three abstractions, in dependency order. Everything else is a consumer.

**(a) `BinaryModel` — the address space.** Entropy is computed over file offsets; disassembly happens at virtual addresses; the user clicks an entropy spike and expects to land in a function. Without one authoritative offset↔VA mapping, the views drift apart and the tool becomes eight separate toys. Phase 1.

**(b) `ElementStream` — the reinterpretation layer.** The reference project's `histo_dtype_t` (u8/u12/u16/u32/u64/f32/f64) and `ImageView::dtype_t` (24 pixel formats) are the same idea applied twice: *raw bytes are not always bytes*. A firmware blob may be 16-bit sensor samples; a scientific dump may be f32 arrays; a camera partition may be 12-bit Bayer. One reinterpretation function feeds the histograms, the image view, and the plot view. Phase 2.

**(c) `Surface` — bytes-to-raster.** Overall/byte-class, Hilbert, image view, bigram, dot plot are all `(byte range, dtype, params, w, h) → 2-D raster`. One protocol, one cache, one frontend canvas component, N implementations. Phase 3.

```
   file bytes (mmap)
        │
        ▼
┌───────────────────────────────────────────────┐
│ P1  BinaryModel                               │
│     regions · symbols · off↔va · arch · entry │
└───────────────┬───────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────┐
│ P2  ElementStream + statistics core           │
│     dtype reinterpret · quantise · byte-class │
│     entropy · n-gram (n=1,2,3) · reducers     │
└───────────────┬───────────────────────────────┘
                │
      ┌─────────┴──────────┬────────────────────┐
      ▼                    ▼                    ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
│ P3 Surfaces  │   │ P2 Signals   │   │ P4 Decoder       │
│ byteclass    │   │ entropy(w)   │   │ (Capstone)       │
│ hilbert      │   │ printable%   │   │      ↓           │
│ image+bayer  │   │ chi²         │   │ P5 CFG recovery  │
│ ngram2       │   │ ...          │   │ functions/blocks │
│ dotplot      │   └──────┬───────┘   └────────┬─────────┘
│ ngram3(pts)  │          │                    │
└──────┬───────┘          │                    │
       └──────────────────┼────────────────────┘
                          ▼
        ┌────────────────────────────────────────┐
        │ P6  Content-addressed cache + service  │
        │     ~/.cache/binviz/<sha256>/          │
        │     JSON meta · raw rasters · signals  │
        └────────────────────┬───────────────────┘
                             ▼
        ┌────────────────────────────────────────┐
        │ P7  SelectionStore  (the linkage core) │
        │     offsetRange · vaRange · function   │
        └────────────────────┬───────────────────┘
       ┌──────────┬──────────┼──────────┬────────────┐
       ▼          ▼          ▼          ▼            ▼
   P7 Overall  P7 Plot   P8 2D/3D   P9 Image/   P10 CFG
   +Zoomed     (signals) histogram  Dot/Hex     (elkjs)
       └──────────┴──────────┴──────────┴────────────┘
                             ▼
                  P11 Triage report + file nav
```

### 1.2 What is shared vs. what is separate

| Concern | Shared? | Notes |
|---|---|---|
| File access (mmap, hashing, chunked reads) | **Shared** | `loader.py`, one implementation |
| Container parsing, region map, off↔va | **Shared** | `BinaryModel`, the spine |
| Element reinterpretation (dtype) | **Shared** | histograms, image view, plot view all consume it |
| Quantisation to 256 bins | **Shared** | required by every histogram at width > 8 bits |
| Statistical kernels (histogram, entropy, n-gram) | **Shared** | `stats.py` |
| Downsampling / binning reducer | **Shared** | entropy strip, overall view, CFG minimap |
| Raster production | **Shared** | `Surface` protocol + one raster cache |
| Colour mapping | **Shared (frontend)** | one `colormap.ts`, LUTs as `Uint8Array` |
| Selection state | **Shared (frontend)** | one `SelectionStore` — this is what makes views *linked* rather than adjacent |
| Disassembly | **Separate** | only P4/P5 touch Capstone |
| Graph layout | **Separate** | only the CFG view runs elkjs |
| 3-D point cloud rendering | **Separate** | only the trigram view runs WebGL |

### 1.3 Language and process boundaries

**Backend: Python 3.11+.** LIEF, Capstone, and numpy are Python-first; the numeric work is vectorised numpy so the interpreter is not the bottleneck. Analysis runs once per file and is cached by content hash.

**Frontend: TypeScript, no framework.** The views are canvas surfaces plus a control panel. React/Vue buys nothing and costs a build chain; a small hand-rolled store plus `<canvas>` is genuinely simpler. Vite for bundling.

**Boundary: HTTP.** Metadata is JSON. Bulk numeric data — rasters, signals, histograms — is **never** JSON (see §6, Phase 6).

### 1.4 Repository layout

```
binviz/
  pyproject.toml
  src/binviz/
    loader.py         # mmap, sha256, chunked reads
    model.py          # Region, Symbol, BinaryModel, off_to_va/va_to_off
    parse.py          # LIEF -> BinaryModel; raw fallback
    elements.py       # Dtype, elements(), quantise(), byte_class()
    stats.py          # entropy, ngram(n), reducers, chi-square
    signals.py        # Signal type + registry (entropy, printable%, ...)
    surfaces/
      base.py         # Surface protocol, SurfaceRequest, Raster
      linear.py       # byte-class / entropy linear map
      hilbert.py      # d2xy + Hilbert-laid-out surface
      image.py        # pixel-format + Bayer demosaic surface
      ngram.py        # 2-D bigram raster; 3-D trigram point extraction
      dotplot.py      # progressive stochastic self-similarity
    disasm/
      backend.py      # DisassemblyBackend protocol
      capstone_be.py  # required backend
      r2_be.py        # optional oracle backend
      blocks.py       # basic-block splitting
      recover.py      # function discovery + CFG assembly
    cache.py          # content-addressed on-disk cache + raster accumulators
    service.py        # FastAPI app
    triage.py         # verdict synthesis
    cli.py            # binviz probe|model|signal|surface|cfg|triage|serve
    render/           # offline PNG/DOT export (verification artifacts)
  web/
    index.html
    src/
      store.ts          # SelectionStore, BinaryHandle
      api.ts            # typed fetch layer
      colormap.ts
      canvas/raster.ts  # shared raster canvas component
      views/overall.ts  views/plot.ts  views/hist2d.ts  views/hist3d.ts
      views/image.ts    views/dotplot.ts  views/hex.ts  views/cfg.ts
      workers/layout.worker.ts
  corpus/
    Makefile          # builds ground-truth samples
    manifest.json     # expected properties per sample
  tests/
```

---

## 2. Feature Parity Map

Every binary_viewer capability, and where it lands. **Nothing is dropped.**

| binary_viewer feature | Source file | Our phase | Notes / deliberate changes |
|---|---|---|---|
| Overall primary view | `overall_view.cpp` | **P3** (raster) + **P7** (UI) | byte-class colouring mode |
| — Hilbert curve mode | `hilbert.cpp` | **P3** + **P7** | first-class toggle, not deferred |
| — drag range selection (m1/m2) | `overall_view.cpp` | **P7** | generalised into `SelectionStore`, drives *all* views incl. CFG |
| Overall zoomed view | `overall_view.cpp` | **P7** | same component, bound to selection |
| Plot view (2 float series, normalize) | `plot_view.cpp` | **P2** (signals) + **P7** (UI) | generalised to N named signals, not fixed 2 |
| 2D histogram (bigram) | `histogram_2d_view.cpp` | **P2** + **P8** | + log/rank/sqrt display transforms |
| 3D histogram (trigram, OpenGL) | `histogram_3d_view.cpp` | **P2** + **P8** | WebGL point cloud; threshold/scale/overlap controls preserved |
| — histo dtype u8/u12/u16/u32/u64/f32/f64 | `histogram_calc.h` | **P2** | one `elements()` + `quantise()` serving all consumers |
| Image view — grey 8/12/16 | `image_view.cpp` | **P3** + **P9** | |
| Image view — RGB/BGR/RGBA/BGRA @ 8/12/16 | `image_view.cpp` | **P3** + **P9** | |
| Image view — 24 Bayer CFA modes | `bayer.cpp` | **P3** + **P9** | 4 CFA phases × 6 channel permutations |
| Image view — invert toggle | `image_view.cpp` | **P9** | |
| Image view — offset / width spinboxes | `image_view.cpp` | **P9** | width also settable from selection |
| Dot plot (offset1/offset2/width/max_samples) | `dot_plot.cpp` | **P3** + **P9** | progressive sampling preserved; **+ exact mode** for small ranges |
| Hex/binary viewer with scrollbar | `binary_viewer.cpp` | **P9** | + region/symbol annotation gutter |
| Entropy (block_size=256) | `histogram_calc.cpp` | **P2** | **dual window** — see §5.3 |
| File prev/next navigation | `main_app.cpp` | **P11** | + drag-drop, + recent list |
| View switcher dropdown | `main_app.cpp` | **P7** | tabbed/grid layout instead |
| Dark theme (QDarkStyleSheet) | `qdarkstyle/` | **P7** | CSS custom properties, light + dark |
| — | — | **P4/P5/P10** | **new:** disassembly + interactive CFG |
| — | — | **P11** | **new:** triage verdict |
| — | — | **P1** | **new:** container parsing / region awareness |

---

## 3. Phase-by-Phase Breakdown

Each phase is sized for one focused session and ends with a runnable artifact you can look at. **Every phase's success criteria are executable** — a test or a CLI command with checkable output, not "it looks right."

---

### Phase 0 — Skeleton + ground-truth corpus

**Why first:** every later phase's success criteria reference this corpus. Building it later means retrofitting all validation.

**Inputs:** a C compiler, `upx`, `python3`.

**Builds:**
- `pyproject.toml` with pinned deps (`lief`, `capstone`, `numpy`, `fastapi`, `uvicorn`, `pillow`, `pytest`).
- `corpus/Makefile` producing, in `corpus/out/`:

| Sample | How | Purpose |
|---|---|---|
| `zeros.bin` | 1 MiB of `0x00` | entropy floor = 0.0 |
| `urandom.bin` | 1 MiB CSPRNG | entropy ceiling calibration; bigram uniformity |
| `ascii.txt` | ~1 MiB English text | entropy ≈ 4.3–4.8; ASCII-box bigram mass |
| `pattern.bin` | repeating 16-byte pattern | bigram ground truth (exactly 16 cells); dot-plot ground truth |
| `repeats.bin` | 3 copies of a 64 KiB block, separated by random data | **dot plot** — must show 3 off-diagonal bands |
| `ramp16.bin` | u16le values 0,1,2,… | **dtype** ground truth: as u8 looks like noise, as u16 a clean ramp |
| `floats.bin` | 256 Ki f32 samples of a sine | **f32 dtype** + plot-view ground truth |
| `bayer_raw.bin` | 640×480 12-bit RGGB gradient, synthetic | **image view / Bayer** ground truth |
| `rgb_raw.bin` | 320×240 RGB8 colour bars | **image view** ground truth |
| `hello_O0`, `hello_O2` | same C source, two opt levels | code-entropy calibration |
| `hello_static` | `-static` | large `.text`, many functions |
| `hello_stripped` | `strip` of `hello_O2` | function-discovery recall test |
| `hello_upx` | `upx -9` of `hello_static` | packed detection ground truth |
| `switchy` | C source with a 20-case `switch` | jump-table / indirect-jump test |
| `sample.png`, `sample.zip` | any | non-executable controls; packed false-positive test |
| *(optional)* ARM64 + ARM/Thumb binaries | `aarch64-linux-gnu-gcc` | arch-dispatch test |

- `corpus/manifest.json`: per sample, expected `format`, `arch`, `bits`, plus an `expect` block later phases fill in (entropy bounds, bigram cell counts, function counts, dot-plot band positions).
- `binviz probe <file>` → `{path, size, sha256, magic, guessed_format}`. Header-magic sniffing only; no LIEF yet.

**Success criteria:**
- `make -C corpus` from clean produces every sample; `pytest tests/test_corpus.py` asserts each exists and matches manifest properties.
- `binviz probe corpus/out/hello_O2` → `elf`; `binviz probe corpus/out/sample.png` → `png`.

**Scope note:** if a cross-compiler isn't available, mark ARM samples optional and let tests skip. Do **not** block on toolchain yak-shaving — record the gap in the manifest.

---

### Phase 1 — Address-space model

**Why this is the true blocker:** file offset ≠ virtual address. In PE, RVAs and raw offsets diverge because `SectionAlignment` (4096) ≠ `FileAlignment` (512). In ELF, `.bss` occupies virtual space with zero file bytes. Get this wrong and every cross-view link is silently off by a few KB — which looks like "the tool is subtly broken" and is miserable to debug later.

**Produces:**

```python
@dataclass(frozen=True)
class Region:
    name: str          # ".text", "PT_LOAD[1]", "<overlay>", "<gap>"
    kind: str          # section | segment | header | overlay | gap
    file_off: int      # -1 if not file-backed (e.g. .bss)
    file_size: int
    vaddr: int         # -1 if not mapped
    vsize: int
    perms: str         # subset of "rwx"
    entropy: float | None      # filled by P2

@dataclass(frozen=True)
class Symbol:
    name: str; va: int; size: int
    kind: str          # func | object | import | export | unknown
    source: str        # symtab | dynsym | export_table | none

@dataclass
class BinaryModel:
    path: str; sha256: str; size: int
    format: str        # elf | pe | macho | raw
    arch: str          # x86 | x86_64 | arm | arm64 | mips | unknown
    bits: int; endian: str
    entry_va: int | None
    regions: list[Region]          # sorted by file_off, gaps materialised
    symbols: list[Symbol]
    imports: list[str]             # "libc.so.6!memcpy" / "KERNEL32.dll!VirtualAlloc"
    exports: list[str]
    arch_ranges: list[tuple[int,int,str]]   # (va0, va1, "arm"|"thumb"|"data")
    warnings: list[str]

    def off_to_va(self, off: int) -> int | None: ...
    def va_to_off(self, va: int) -> int | None: ...
    def region_at_off(self, off: int) -> Region | None: ...
    def to_json(self) -> dict: ...
```

**Library decision — LIEF, not `pefile`/`pyelftools`/`macholib`.** One API across all three formats is worth a lot: the alternative is three parsers plus three normalisation layers, and normalisation is where the bugs live. LIEF's weaknesses (strict about malformed headers; Python bindings shift between minor versions) are handled by pinning the version, wrapping every LIEF call inside `parse.py` only, and a **raw fallback** — if LIEF throws or returns `None`, produce a `format="raw"` model with one whole-file region and `arch` from a `--arch` override. Malformed and packed binaries are the interesting ones; the tool must never hard-fail on them.

**Implementation notes:**
- Build the offset→VA map as a sorted interval list + `bisect`. O(log n), called constantly by P5 and by every cross-view link.
- **Materialise gaps and the overlay.** File bytes not covered by any section/segment become explicit `kind="gap"` / `kind="overlay"` regions. Appended data past the last section is one of the highest-signal triage findings there is (installers, self-extractors, appended payloads) and most tools quietly ignore it.
- For ARM, populate `arch_ranges` from ELF mapping symbols (`$a`, `$t`, `$d`). Without this, P4 decodes Thumb as ARM and produces garbage — a classic silent failure. `$d` ranges are marked data and never swept.
- Record suspicious findings in `warnings` rather than raising: entry point outside any executable region, W+X regions, virtual size ≫ raw size, zero imports.

**Success criteria:**
- `binviz model <file> --json` for every corpus sample; property test: for 10,000 random offsets in file-backed regions, `va_to_off(off_to_va(o)) == o`.
- `hello_O2`: `.text` present with `file_off`/`vaddr` cross-checked against parsed `readelf -S` output.
- `hello_upx`: parses without exception, ≥1 warning, near-empty imports.
- `sample.png`: falls back to `format="raw"`, one region, no exception.
- `hello_O2` truncated to 60% → still returns a model, with a warning.

---

### Phase 2 — Element stream, statistics core, and signals

This is the phase that makes parity affordable. It replaces what the reference splits across `histogram_calc.cpp`, the dtype enums, and per-view statistics.

**(a) Element reinterpretation** — `elements.py`:

```python
Dtype = Literal["u8","u12","u16le","u16be","u32le","u32be",
                "u64le","u64be","f32le","f32be","f64le","f64be"]

def elements(buf: memoryview, dtype: Dtype) -> np.ndarray:
    """Reinterpret raw bytes as a 1-D array of elements."""

def quantise(vals: np.ndarray, dtype: Dtype,
             lo=None, hi=None) -> tuple[np.ndarray, dict]:
    """Map elements to uint8 bin indices 0..255 for histogramming.
       Returns (bins, {lo, hi, method})."""
```

**The non-obvious parts:**
- **u12 is packed**, not padded: two 12-bit elements per three bytes (`[a_hi][a_lo|b_hi][b_lo]`). This is sensor/camera-raw layout and is why the reference has it. Implement with `np.unpackbits`-free bit arithmetic on a `(n,3)` uint8 view; test against a hand-built vector.
- **You cannot histogram u16 directly.** A 65536×65536 bigram matrix is 34 GB. Every width > 8 bits *must* be quantised to 256 bins first, and the quantisation choice changes what the plot means — so it is returned in metadata and shown in the UI. Default: linear over `[min, max]` of the range for integers; for floats, linear over the 0.5th–99.5th percentile after dropping NaN/±inf (raw min/max on float data is almost always destroyed by a single outlier).
- Misaligned ranges: element width `w` on a range whose length isn't a multiple of `w` truncates the tail. Report it rather than silently dropping.

**(b) Statistics** — `stats.py`:

```python
def histogram(bins: np.ndarray) -> np.ndarray            # (256,) uint32
def ngram(bins: np.ndarray, n: int) -> np.ndarray        # n=1,2,3 -> (256,)/(256,256)/(256,256,256 sparse)
def entropy_profile(buf, window, stride) -> EntropyProfile
def byte_class(buf: memoryview) -> np.ndarray            # (n,) uint8 class id
def chi2_uniform(hist) -> float
```

`EntropyProfile`:
```python
@dataclass
class EntropyProfile:
    window: int; stride: int
    values: np.ndarray     # float32, bits/byte in [0,8]
    offsets: np.ndarray    # int64, window start offsets
    def bin(self, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Reduce to n bins; returns (min, mean, max) per bin."""
```

**Byte classes** (the reference's `use_byte_classes_`) — a 6-class palette, since this is what makes the overall view readable:
`0` null (0x00) · `1` printable ASCII (0x20–0x7E) · `2` whitespace/control (0x09,0x0A,0x0D + other <0x20) · `3` low non-printable · `4` high (0x80–0xFE) · `5` 0xFF.
Null and 0xFF get their own classes deliberately — padding and erased flash are the two most common bulk fills and you want them instantly distinguishable.

**Trigram is sparse.** A dense 256³ uint32 array is 67 MB and mostly zero. Compute it as a `bincount` over `(b0<<16)|(b1<<8)|b2` with `minlength=2**24` (a 67 MB uint32 transient is acceptable once), then immediately threshold and emit **sparse points** `(x, y, z, count)` — typically 10k–500k non-zero cells. Only the sparse form is cached or shipped.

**(c) Signals** — `signals.py`. Generalises the reference's fixed 2-series plot view:

```python
@dataclass
class Signal:
    name: str; unit: str
    values: np.ndarray     # float32
    offsets: np.ndarray    # int64
    lo: float; hi: float    # natural range for normalisation

SIGNALS = {"entropy_256", "entropy_4096", "printable_ratio",
           "null_ratio", "chi2_uniform", "distinct_bytes"}
```

**(d) Classification:**
```python
def classify(window_bytes) -> str
# "zero" | "ascii" | "code" | "data" | "compressed" | "encrypted_or_random"
```
Entropy + printable ratio + chi-square. Compressed and encrypted data have near-identical entropy; the weak distinguisher is that compressed streams retain small structural biases and container magic. Label that distinction low-confidence — don't pretend.

**The entropy window decision — why the reference's fixed 256 isn't enough.** Plug-in Shannon entropy over a 256-symbol alphabet is **biased low when the window is small relative to the alphabet**. For a 256-byte window of uniform random bytes, per-symbol counts are ≈ Poisson(1) and expected measured entropy is ≈ **7.1–7.2 bits/byte, not 8.0**. So at 256-byte windows, truly random data sits barely above the folkloric "7.0 = packed" line, while optimised x86 code crosses it routinely. Every false "packed!" verdict you have ever seen comes from this.

The answer: **two windows, two roles.** 256 B / stride 256 for *visual texture* (matches the reference, keeps the plot lively); 4096 B / stride 4096 for any *decision*. Both are cheap. Thresholds are not hardcoded — Phase 2 emits `corpus/calibration.json` by running over `zeros`, `urandom`, `ascii`, `hello_O2:.text`, and `hello_upx`'s packed region at both windows, and `classify()` reads from it. Change the window later and the thresholds move with it instead of quietly becoming meaningless. Optionally apply Miller–Madow correction (`+(K̂−1)/(2N ln2)`) to small-window values *for display only*, stated in the legend; never to decision values.

**Vectorised implementation:** reshape to `(n_windows, window)`, `bincount` over `row*256 + byte` → `(n_windows, 256)`, then `-(p*log2 p).sum(axis=1)` with zeros masked. 100 MB at window 4096 is one ~25M-element bincount, well under a second. Overlapping strides use incremental count updates (add entering byte, drop leaving byte) — **but only build that if the simple version misses the target.** Measure first.

**Verification artifact:** `binviz signal <file> --name entropy_4096 --png out.png` and `binviz hist <file> --n 2 --dtype u16le --png out.png`.

**Success criteria:**
- `zeros.bin` → entropy 0.0 everywhere. `pattern.bin` → constant, matching hand-computed entropy of the 16-byte pattern.
- `urandom.bin`: window 4096 mean ∈ [7.98, 8.0]; window 256 mean ∈ [7.0, 7.3]. Record exact values in `calibration.json` and assert stability.
- `hello_upx` packed region: 4096-window mean ≥ 7.7; `classify` → compressed/random for ≥90% of windows.
- `hello_O2` `.text`: `code` for ≥80% of windows; `.rodata` **not** `compressed`.
- **dtype:** `ramp16.bin` as `u8` → near-uniform histogram; as `u16le` → monotone ramp, distinct-bin count ≈ 256. `floats.bin` as `f32le` → arcsine-shaped histogram (sine amplitude density), no NaN leakage.
- **u12:** hand-built 3-byte vector round-trips to the two expected 12-bit values.
- **bigram:** `pattern.bin` → exactly 16 non-zero cells (assert the exact set); `urandom.bin` → non-zero fraction > 0.99; `ascii.txt` → ≥95% mass in the ASCII box.
- **trigram:** `pattern.bin` → exactly 16 sparse points; `urandom.bin` sparse point count within 2% of the coupon-collector expectation.
- `profile.bin(2000)` returns exactly 2000 bins for any input; **regression test:** inject one random 4 KiB block into `zeros.bin`, assert it survives binning to 2000 bins in the `max` channel.
- Perf: 100 MB → both entropy profiles < 2.0 s; bigram < 1.5 s; trigram < 4 s; peak RSS increase < 200 MB.

That last binning criterion matters more than it looks — see §5.2.

---

### Phase 3 — Surface engine (the parity phase)

**Inputs:** `BinaryModel`, mmapped bytes, P2 primitives.

**Produces** the abstraction that six views share:

```python
@dataclass(frozen=True)
class SurfaceRequest:
    start: int; end: int
    width: int; height: int
    dtype: Dtype = "u8"
    params: dict = field(default_factory=dict)

@dataclass
class Raster:
    pixels: np.ndarray     # uint8 (h,w) scalar OR uint8 (h,w,3) rgb
    kind: str              # "scalar" | "rgb"
    meta: dict             # value range, sample count, progress, warnings

class Surface(Protocol):
    name: str
    def render(self, buf: memoryview, req: SurfaceRequest) -> Raster: ...
```

Scalar rasters ship raw and are coloured client-side, so changing colormap never refetches. RGB rasters (image view) ship as PNG.

**The six implementations:**

**1. `linear`** (`surfaces/linear.py`) — the reference's overall view. Row-major byte-class map, or a scalar signal map. Params: `mode: "byteclass" | "signal"`, `signal: str`. Aggregation to `w×h` uses the P2 reducer; for byte-class mode the reducer is **mode (most common class)**, not mean — averaging class IDs is meaningless and produces phantom classes. That mistake is easy to make and looks plausible.

**2. `hilbert`** (`surfaces/hilbert.py`) — the reference's `use_hilbert_curve_`. A linear strip breaks locality at every row wrap; a Hilbert curve keeps nearby offsets nearby in 2-D, so a contiguous 4 KiB blob shows as a compact patch instead of a smear across rows. Implement `d2xy`/`xy2d` for order `k` where `2^k = min(w,h)` rounded to a power of two (Hilbert requires square, power-of-two — pad and mask the remainder, don't silently rescale). **Ship `xy2d` too**: the inverse is what lets the user click a Hilbert pixel and get a file offset, which is the whole point of having it linked.

**3. `image`** (`surfaces/image.py` + Bayer) — the reference's `ImageView`, all 24+ modes:
- **Packed pixel formats:** `grey8/12/16`, `rgb8/12/16`, `bgr8/12/16`, `rgba8/12/16`, `bgra8/12/16`. Built on `elements()`; 12-bit uses the packed u12 path; 16-bit is scaled to 8 for display with the scaling recorded in `meta`.
- **Bayer CFA:** 4 phases (`RGGB`, `BGGR`, `GRBG`, `GBRG`) × 6 output channel permutations = **24 modes**, matching `bayer8_0..23`. Demosaic with simple bilinear interpolation — this is a *format identification* tool, not an image pipeline, so bilinear is correct and anything fancier is over-engineering. The real use case is spotting raw sensor dumps inside firmware images, where "does it suddenly look like a photograph" is the entire signal.
- Params: `width` (image row stride in pixels), `invert: bool`, `offset`.
- **Non-obvious:** the row stride is the single control that matters. Wrong stride turns a photograph into diagonal noise. Provide an **autocorrelation-based stride suggester**: FFT-autocorrelate the byte stream over the selected range and report the top 3 peak lags in the 64–8192 range as candidate strides. The reference makes the user guess with a spinbox; this is a genuine improvement and it is ~15 lines of numpy.

**4. `ngram2`** (`surfaces/ngram.py`) — the 2-D histogram raster. 256×256 from P2, then `to_display(counts, mode)` → uint8, modes `log1p` (default), `rank` (percentile-flattened, best for faint structure), `sqrt`, `linear`. **The display transform is part of the analysis, not the UI**: raw bigram counts span 6+ orders of magnitude, so linear mapping shows one bright pixel at (0,0) and black everywhere else. The transform determines what the plot *means*, so it's recorded in `meta`.

**5. `dotplot`** (`surfaces/dotplot.py`) — the reference's `DotPlot`, and the most algorithmically interesting one. Cell (i,j) is lit when the `k`-byte window at `i` matches the window at `j`.
- **Exhaustive is impossible.** A 1 MiB range is 10¹² comparisons. The reference's answer — and the right one — is **progressive stochastic sampling**: draw `max_samples` random `(i,j)` pairs, accumulate hits into the matrix, and refine over repeated calls (`advance_mat` + `pts_i_` cursor). Preserve exactly this. Params: `off1`, `off2`, `window` (k), `max_samples`, `seed`, `cursor`. Server keeps an accumulator in the raster cache keyed by `(range, params, seed)`; each call advances it and returns `meta.progress`.
- **Plus an exact mode the reference lacks.** For ranges under ~256 KiB, hash every k-mer into buckets (`np.unique` on a rolling 64-bit hash), then only compare within buckets → O(n + matches) instead of O(n²). This gives an exact, non-noisy plot instantly for the common "inspect this one section" case. Auto-select exact vs. sampled by range size; report which was used in `meta` so a sampled plot is never mistaken for a complete one.
- **Always report sampling density.** A sparse dot plot from 10⁶ samples over a 10¹² space looks like "no self-similarity" when it means "we barely looked."

**6. `ngram3` point extraction** — not a raster; returns the sparse `(x,y,z,count)` array for the WebGL view, with `threshold` and `scale` params matching the reference's spinboxes.

**Success criteria:**
- `rgb_raw.bin` at `rgb8`, stride 320 → recognisable colour bars (assert mean RGB of each bar column against expected).
- `bayer_raw.bin` at `bayer8` RGGB phase → smooth gradient; the other 3 phases → visible checkerboard artifacts (assert high-frequency energy is ≥3× lower for the correct phase — this is a real, checkable test for "did we get the CFA phase right").
- Stride suggester returns 320 as a top-3 candidate for `rgb_raw.bin` and 640 for `bayer_raw.bin`.
- `repeats.bin` dot plot (exact mode) → 3 clear off-diagonal bands at the expected offsets; sampled mode at 10⁶ samples → same bands visible, `meta.mode == "sampled"`.
- Hilbert: `xy2d(d2xy(d)) == d` for all `d` at order 8; a 4 KiB contiguous marker renders as a connected patch (assert its bounding box area < 4× its pixel count).
- `linear` byteclass on `ascii.txt` → ≥95% class 1; on `zeros.bin` → 100% class 0.
- `binviz surface <file> --name <n> --png out.png` for all six. Capture one plate per surface per corpus class into `docs/plates/` — these are the visual regression baseline and the documentation.

**Scope calibration:** this phase is large. If it overruns, the correct split is `linear` + `hilbert` + `ngram2` in session A (they share the reducer), `image` + `bayer` in session B, `dotplot` in session C. Do **not** split by "start all six, finish none."

---

### Phase 4 — Instruction decoding core

**Produces:**

```python
@dataclass(frozen=True)
class Insn:
    va: int; size: int; bytes_: bytes
    mnemonic: str; op_str: str
    groups: frozenset[str]     # jump|call|ret|branch_relative|privileged|invalid
    targets: tuple[int, ...]   # resolved direct branch/call targets
    is_indirect: bool

class DisassemblyBackend(Protocol):
    def decode(self, va: int, data: memoryview, mode: str) -> Iterator[Insn]: ...
```

plus `linear_sweep(range)` and `recursive_descent(seeds)` → `dict[int, Insn]`.

**Why Capstone over radare2 as the required backend:**

1. **It is a decoder, not an analyser — and that's the point.** Capstone gives `(addr, size, mnemonic, op_str, groups, detail)` and has no opinion about functions. We need to own analysis semantics because the visualisation's value proposition is *showing where analysis is uncertain*; r2's `aaa` returns a cleaned-up answer with the uncertainty already discarded.
2. **In-process.** No subprocess, no r2pipe JSON per query, no marshalling 200k instructions through a pipe. The interactive CFG needs sub-100 ms responses.
3. **Deterministic and version-stable.** `capstone` wheels install with pip on all three OSes with zero system deps. r2's JSON command output changes shape across releases; pinning it is a maintenance tax.
4. **Licensing.** Capstone is BSD; radare2/rizin are LGPLv3.

**Where radare2 *does* earn its place:** an **optional second backend behind the same protocol** for (a) function discovery on stripped binaries, where r2's accumulated heuristics beat anything written in one session, and (b) **differential testing** — `pytest -m oracle` compares our function set against `aflj` and reports precision/recall rather than asserting equality. Ship `r2_be.py`; never let the core path require it.

**Explicitly rejected:** **angr** (full VEX/claripy symbolic stack, minutes per binary, wrong tool for an interactive viewer). **objdump** as a runtime dependency (output format is a parsing minefield) — but it *is* the test oracle here, which is a different thing.

**Implementation notes:**
- Two Capstone handles: `detail=False` for bulk linear sweep (roughly 2× faster), `detail=True` only for branch-target resolution.
- **x86 self-synchronisation:** a linear sweep started at a wrong offset emits phantom instructions that typically resync within 2–10 bytes — so wrong output *looks* plausible. Consequence: recursive descent is primary; linear sweep only fills gaps, and every block it produces carries `confidence="low"`.
- ARM/Thumb mode from `BinaryModel.arch_ranges`; if a decode at a Thumb address yields `invalid`, retry once in the other mode and log a warning.
- Refuse to follow branches outside executable regions; hard-cap instructions per function (200k) so pathological input terminates.

**Success criteria:**
- **Differential vs objdump:** for `hello_O2` and `hello_static`, parse `objdump -d --no-show-raw-insn` address columns and assert our `.text` linear sweep yields an *identical set of instruction start addresses*. Compare addresses and sizes, not mnemonic text (syntax flavours differ; that's not a real disagreement). Target 100%; any mismatch is a real bug.
- Recursive descent from `entry_va` on `hello_O0` reaches `main` (verified via symtab).
- `urandom.bin` decoded as x86_64 → `groups={"invalid"}` entries, never raises, never loops.
- Perf: 10 MB `.text` linear sweep < 20 s with detail off.

---

### Phase 5 — Function discovery + CFG assembly

**Produces** the wire format the frontend consumes verbatim:

```jsonc
{
  "function": {
    "va": 4198400, "name": "main", "size": 342,
    "discovery": "symbol|entry|call_target|prologue|gap_sweep",
    "confidence": 0.9, "mode": "x86_64",
    "complete": true            // false if any block hit a decode failure or the cap
  },
  "blocks": [
    { "id": 0, "va": 4198400, "end_va": 4198432, "file_off": 8192,
      "confidence": "high",
      "insns": [{"va":4198400,"size":1,"bytes":"55","mnemonic":"push","op":"rbp"}],
      "terminator": "jcc|jmp|ret|call_noreturn|fallthrough|indirect|invalid" }
  ],
  "edges": [ {"src":0,"dst":1,"kind":"true|false|uncond|fallthrough|indirect_unresolved"} ],
  "unresolved": [ {"va":4198512,"reason":"indirect_jump","hint":"jump_table?"} ],
  "calls_out": [ {"from_va":4198450,"target_va":4200000,"name":"printf","kind":"direct|plt|indirect"} ]
}
```

Plus a program-level `functions.json` index and a call graph.

**Discovery cascade, strict priority order** (provenance is recorded and displayed):

1. `symtab`/`dynsym` `STT_FUNC`, PE exports, Mach-O `LC_FUNCTION_STARTS` — confidence 1.0
2. `entry_va`, TLS/init/fini array entries — 1.0
3. **Call-target harvesting:** recursive descent from tiers 1–2; every direct `call` target is a function — 0.9
4. **Prologue scan** over uncovered executable bytes: x86-64 `55 48 89 e5`, `f3 0f 1e fa` (endbr64), `48 83 ec`; ARM64 `stp x29,x30,[sp,#-N]!`; Thumb `push {…,lr}` — 0.5. Unreliable at `-O2` (no frame pointer); that's expected, hence the low confidence.
5. **Gap sweep:** remaining executable bytes → low-confidence blocks not attached to any function — 0.2. Rendered as an "unclaimed code" band, not hidden.

**Basic-block splitting** is mechanical: collect leaders (function start, every branch target, every instruction after a branch or noreturn call), sort, cut. The subtlety is **noreturn calls** — `exit`, `abort`, `__stack_chk_fail`, `_Unwind_Resume`, `longjmp`. Treating a call to `abort` as falling through creates a spurious edge into alignment padding and grows a fake tail. Keep a noreturn name list plus the ELF/DWARF attribute where available.

**Indirect jumps — the honest approach.** Unresolved indirect jumps are the largest source of missing CFG edges (every non-trivial `switch` makes one). Never drop them: emit `terminator: "indirect"` plus an `unresolved` record, and let the UI draw a dangling edge to a "?" sentinel. A CFG that *shows* its holes is far more useful for triage than one pretending to be complete.

**One** bounded pattern matcher as a stretch goal: the standard x86-64 PIC jump table — `lea <reg>,[rip+disp]` → `movsxd <r2>,[<reg>+<idx>*4]` → `add <r2>,<reg>` → `jmp <r2>`. Read the table via `va_to_off`, bound entry count by the preceding `cmp`/`ja` if present else cap at 256, and validate every target lands in the same executable region. Any validation failure abandons the whole table and falls back to unresolved. Do this **last**, only if time remains.

**Success criteria:**
- `hello_O2` unstripped: every `STT_FUNC` appears in `functions.json` with `discovery="symbol"`; `main`'s block/edge counts cross-checked against `r2 agfj` under `pytest -m oracle`, gated at ≥0.9 recall.
- `hello_stripped`: recall vs. the unstripped twin (same binary before stripping ⇒ identical addresses ⇒ exact comparison). **Target ≥0.75 via tiers 2–4, and the phase must print the number.** A plan that pretends stripped recovery is solved is lying; the deliverable is a measured number, not a pass/fail.
- `switchy`: the dispatch yields either a resolved table with all 20 targets or exactly one `unresolved` record — never a silently truncated CFG.
- `hello_upx`: function count near zero and a `packed` warning; must not emit thousands of garbage functions.
- `binviz cfg <file> --func main --dot out.dot` + `dot -Tpng` renders readably. Eyeball artifact and documentation in one.

---

### Phase 6 — Cache + HTTP service

**Why a phase:** analysis is deterministic and pure given file bytes, so caching is trivially correct — and it is what lets the frontend be dumb. Skip it and every view re-triggers analysis, making the UI feel broken.

**Builds:**
- `cache.py`: content-addressed store at `~/.cache/binviz/<sha256>/` — `model.json`, `signals/<name>.f32` + `.i64` offsets, `hist/<n>_<dtype>.bin`, `trigram.sparse`, `rasters/<hash>.raw|png`, `dotplot_acc/<hash>.npy` (progressive accumulators), `functions.json`, `cfg/<va>.json`, `meta.json` (tool version + params). The cache key includes analysis parameters, so changing a window size invalidates cleanly.
- `service.py` (FastAPI):

| Endpoint | Returns |
|---|---|
| `POST /api/open` (path or upload) | `{id: sha256}`; analysis starts in a background task |
| `GET /api/{id}/status` | per-artifact readiness — UI progressively enables views |
| `GET /api/{id}/model` | JSON (incl. compact interval table for client-side off↔va) |
| `GET /api/{id}/signals` | JSON list of available signals |
| `GET /api/{id}/signal/{name}?n=2000&start=&end=` | 3 × Float32Array (min/mean/max), concatenated |
| `GET /api/{id}/hist?n=2&dtype=u8&start=&end=` | raw Uint32Array (256 or 65536 entries) |
| `GET /api/{id}/hist3?threshold=&dtype=` | sparse points: Int32Array `[x,y,z,count]×N` |
| `GET /api/{id}/surface/{name}?start=&end=&w=&h=&dtype=&…` | raw uint8 raster (scalar) or PNG (rgb); `X-Meta` header carries `Raster.meta` as JSON |
| `GET /api/{id}/surface/dotplot?…&cursor=` | raster + `meta.progress`, advances the accumulator |
| `GET /api/{id}/functions` | JSON index |
| `GET /api/{id}/cfg/{va}` | P5 CFG JSON |
| `GET /api/{id}/bytes?off=&len=` | raw bytes, capped 1 MiB — hex viewer |
| `GET /api/{id}/triage` | P11 verdict |
| `GET /api/files?dir=` | sibling file list for prev/next navigation |

**Binary payloads, not JSON — non-negotiable.** A 100 MB file at window 256 is ~390k float32s: ~4 MB of JSON text plus parse time, versus 1.5 MB of octet-stream and a free `new Float32Array(await r.arrayBuffer())`. Same for histograms and rasters. Metadata stays JSON.

**Success criteria:**
- Cold `POST /api/open` on `hello_static` → all artifacts on disk; second call `ready` in < 50 ms.
- `curl .../signal/entropy_4096?n=2000 | wc -c` == `3*4*2000`; a Python client reconstructs values identical to calling the engine directly.
- Concurrent `open` of the same file does not analyse twice (lock file or in-process futures keyed by sha256).
- Killing the server mid-analysis leaves no half-written artifact readable as complete (write `.tmp`, then `os.replace`).
- Two dot-plot requests with the same `(range, params, seed)` and increasing `cursor` monotonically increase `meta.progress` and hit count.

---

### Phase 7 — Web shell, Overall + Zoomed + Plot views, SelectionStore

This is the reference's `main_app` + `overall_view` + `plot_view` trio, and it establishes the linkage every later view plugs into.

**Builds:**
- Vite app, dark/light theming via CSS custom properties (the reference ships QDarkStyleSheet; ours is ~30 lines of CSS and supports both schemes).
- `api.ts`, `colormap.ts` (viridis/magma/inferno + the 6-entry byte-class categorical palette as `Uint8Array` LUTs).
- `canvas/raster.ts` — **one** component: fetch a scalar raster, apply a colormap LUT into `ImageData`, `putImageData`, handle pan/zoom/hover. Every raster view reuses it. This is the frontend counterpart to the `Surface` protocol and it is why P8/P9 are cheap.
- **`SelectionStore`** — `{offsetRange, vaRange, selectedFunction, hoveredOffset, dtype}`, with bidirectional propagation and conversion through the model's interval table. The reference's `rangeSelected(float,float)` signal, generalised: our selection is in absolute offsets (not normalised floats) and also carries VA and function, so the CFG can participate.
- **Overall view** — full-file `linear` or `hilbert` surface with the drag-select m1/m2 marker state machine (`none | m1_moving | m2_moving | m12_moving`) ported from `overall_view.cpp`'s interaction model, which is well-judged: dragging near a marker moves it, dragging inside the band moves both.
- **Zoomed overall view** — the same component bound to `SelectionStore.offsetRange`.
- **Plot view** — N signals stacked or overlaid, per-signal normalise toggle, region ribbon above (coloured blocks per `Region`, labelled `.text` / `.rodata` / `<overlay>`), shared x-axis with the overall view.
- Minimal hex peek panel fed by `/bytes` (full hex viewer lands in P9).

**Rendering decision — Canvas2D `ImageData`, not D3.** These are pixel surfaces. D3's value is data-bound DOM; binding 390k nodes is catastrophic and a single 390k-point SVG `<path>` is barely better. Use `d3-scale` for axes if convenient — the submodule, not the bundle.

**The critical detail — aggregation happens server-side, in the analysis layer.** The canvas is ~2000 px wide. Do **not** fetch 390k values and let the browser downscale: nearest-neighbour sampling drops single-window spikes entirely, and that spike is the whole reason someone opened the tool. Fetch `/signal/{name}?n=<canvas_width>` and render **min/mean/max as a band** — filled envelope, mean as a line — so a spike is visually impossible to miss. On zoom, refetch the range at the same `n`. This is the single most important rendering decision in the project.

> Invoke the `dataviz` skill before finalising the colour scales and legends. Baseline: perceptually uniform sequential (viridis/magma) for scalar values; **never** rainbow/jet, whose yellow/cyan transitions manufacture false boundaries you will then "see" in the data. The byte-class palette is categorical and must be from a distinct hue family so the two encodings can't be confused.

**Success criteria:**
- `hello_upx` overall view makes the packed region obvious; `zeros.bin` renders flat.
- Spike regression, headless: inject one random 4 KiB block into a 100 MB zero file; assert the fetched `max` band at that bin exceeds 7.5.
- Hilbert toggle re-lays the same data; clicking a Hilbert pixel yields the correct offset (verified against `xy2d`).
- Drag-select in the overall view updates the zoomed view and the plot view x-range within one frame.
- Pan/zoom at 60 fps on a 100 MB file.
- Clicking any point sets `SelectionStore.offsetRange`; hex peek matches `xxd`.

---

### Phase 8 — 2D and 3D histogram views

**2D histogram (bigram)** — 256×256 canvas scaled with `image-rendering: pixelated`; display-transform selector (`log1p`/`rank`/`sqrt`/`linear`); **dtype selector** (u8/u12/u16/u32/u64/f32/f64, with endianness) wired to the shared `SelectionStore.dtype`; axis ticks at meaningful values (0x00, 0x20, 0x41, 0x7F, 0xFF); recomputed for the current selection. 65,536 cells is nothing for Canvas2D.

**Brush-to-locate** — the feature that turns the bigram from a picture into an instrument: brushing a rectangle of cells highlights, back in the overall/plot views, the offsets where those byte pairs occur. Needs `POST /api/{id}/hist/locate` (cell rect → binned occurrence density). This answers "*where in the file* is this weird structure?", which the reference cannot.

**3D histogram (trigram) — this is where WebGL is genuinely required, not speculative.** 256³ cells thresholded to 10k–500k visible points, rotating at 60 fps with depth ordering. That is a point-cloud workload, and Canvas2D cannot do it. Use raw WebGL2 or `regl` — not a general 3-D framework:
- Instanced or `gl.POINTS` rendering of the sparse `(x,y,z,count)` array, one `Float32Array` upload.
- Colour and point size from `count` via the `scale` control; `threshold` control filters server-side (fewer points shipped) — matching the reference's spinboxes.
- `overlap` toggle → additive blending with depth-write off (reveals interior density) vs. depth-tested opaque points. This is what the reference's `overlap_` checkbox does and it matters: opaque points hide the structure inside the cube.
- Trackball rotation, plus the reference's idle **auto-spin** — genuinely useful here, because a static projection of a 3-D point cloud is ambiguous and motion parallax resolves it.
- Draw the unit cube wireframe with axis labels; without it the cloud has no frame of reference.

**Success criteria:**
- The four corpus classes (text, code, packed, random) are visually distinct in 2D; save the plates to `docs/plates/`.
- Selecting `.text` in the overall view updates the bigram within 300 ms.
- Brush-to-locate on `pattern.bin` highlights exactly the pattern's offsets.
- dtype switch to `u16le` on `ramp16.bin` collapses the bigram to a diagonal line (the ramp's defining structure) — a crisp, checkable proof the dtype plumbing reaches the view.
- 3D: 200k points sustain 60 fps while spinning; threshold slider changes point count and re-uploads without a frame hitch; `urandom.bin` fills the cube uniformly while `ascii.txt` occupies one corner cluster.

---

### Phase 9 — Image view, dot plot, hex viewer

All three reuse `canvas/raster.ts` and the P3 surfaces, so this phase is mostly controls and interaction.

**Image view** — dtype dropdown covering all packed pixel formats and the 24 Bayer modes; row-stride control with the **autocorrelation stride suggester** offering its top-3 candidates as one-click buttons; offset control; invert toggle; "fit width to selection" helper. Renders as PNG from the server (RGB path).

**Dot plot** — `off1`/`off2` range pickers (defaulting both to the current selection, giving the self-similarity diagonal), window size `k`, `max_samples`, seed. **Progressive rendering**: request with an increasing `cursor` on an animation-frame budget so the plot visibly refines, and show `meta.progress` plus the exact/sampled mode as a persistent label. A sampled dot plot that looks empty must never be mistaken for "no self-similarity."

**Hex viewer** — virtualised scroll (render only visible rows) over `/bytes`, with an **annotation gutter** showing the containing region and any symbol at that address, plus highlighting for the current selection. The reference's `BinaryView` is a plain hex dump; region/symbol annotation is a cheap and large improvement now that P1 exists.

**Success criteria:**
- `rgb_raw.bin` at stride 320 renders recognisable colour bars; stride 321 visibly shears (proving the control works, and why the suggester matters).
- `bayer_raw.bin` in the correct CFA phase is smooth; the other three show checkerboarding.
- Suggester's top candidate is 320 for `rgb_raw.bin`, 640 for `bayer_raw.bin`.
- `repeats.bin` dot plot shows 3 off-diagonal bands within 2 s of opening, refining visibly.
- Hex viewer scrolls a 1 GiB file without loading it (assert bytes fetched < 10 MB after scrolling to the end).
- Selecting a range anywhere scrolls the hex viewer to it.

---

### Phase 10 — CFG view

**Layout — elkjs (`layered`), in a Web Worker.**

*Why ELK over dagre:* CFGs are layered DAGs plus back-edges (every loop) and self-loops. dagre handles the plain DAG case but is unmaintained and routes back-edges and self-loops poorly — which in a CFG is not an edge case but the main event. ELK's `layered` implements the full Sugiyama pipeline with proper cycle-breaking and orthogonal routing, and exposes the knobs that matter (`nodePlacement.strategy=BRANDES_KOEPF`, `layering.strategy=NETWORK_SIMPLEX`, fixed port sides so true/false branches leave a block consistently).

*Why a Worker:* layout of a 500-node graph takes hundreds of milliseconds; on the main thread the UI freezes on every function click. Post `{nodes, edges, sizes}` in, get `{positions, bendpoints}` out. **Cache by `(function_va, collapse_state)`** — re-laying-out on every pan is the most common performance bug in graph UIs.

*Node sizing precedes layout:* block height = instruction count × line height; width = longest rendered line. Measure text once with an offscreen 2-D context, cache per `(font, string)`.

**Rendering — Canvas2D with a quadtree for hit-testing.** The decision rule, stated so it can be reapplied:

| Visual elements | Technology | Why |
|---|---|---|
| < ~2,000 | SVG/DOM | CSS styling, native events, accessibility, trivial text |
| ~2k – 50k | **Canvas2D** | DOM cost dominates above ~2k; Canvas2D text is still cheap and correct |
| > 50k, or 3-D, or animated 60 fps transitions | WebGL | worth the cost of an SDF text atlas — or no text at all |

A per-function CFG is 10–200 blocks × ~10 instructions = 100–2,000 text lines: squarely Canvas2D, which gives free, correct, hinted text. WebGL text needs a signed-distance-field glyph atlas — a multi-session project buying nothing at this scale. (The trigram cloud in P8 is the case that genuinely needs WebGL, because it's 3-D and text-free. The whole-program call graph at >50k nodes is the other, and it's P12.)

LOD: below ~0.4 zoom, draw blocks as solid rectangles without instruction text — measure first; text rendering is the cost, not the rectangles.

**Visual encoding of uncertainty** (the differentiator): low-confidence blocks get dashed borders; `indirect_unresolved` edges terminate at a distinct "?" sentinel; functions from prologue scan or gap sweep are badged in the function list; a packed binary shows a persistent banner — *"This binary appears packed (entropy 7.9 in executable regions, 2 imports). Static CFG is not meaningful. Supply an unpacked dump via `binviz open --raw`."*

**Success criteria:**
- `main` from `hello_O0` lays out in < 200 ms and matches the `dot` render from P5.
- A 500-block function pans/zooms at 60 fps; layout computed once (assert via call counter).
- Clicking a block sets `SelectionStore.offsetRange` to its file range — the overall view, hex viewer, and bigram all follow. This closes the loop and is the moment the tool stops being nine panels.
- Selecting a range in the overall view filters the function list to functions overlapping it.
- `hello_upx` shows the packed banner and an empty-but-explained function list — not an error, not garbage.

---

### Phase 11 — Triage report, cross-view polish, file navigation

**Builds:**
- `triage.py` → the verdict, consuming every prior artifact:

```jsonc
{
  "verdict": "likely_packed | likely_benign_binary | non_executable | corrupt | inconclusive",
  "confidence": 0.0,
  "findings": [
    {"severity":"high","code":"HIGH_ENTROPY_EXEC",
     "detail":"Region .text entropy 7.91 (calibrated packed threshold 7.70)",
     "offsets":[4096,102400]},
    {"severity":"high","code":"OVERLAY_PRESENT","detail":"3.2 MiB appended past last section"},
    {"severity":"medium","code":"IMPORT_STARVED","detail":"2 imports for a 4 MiB binary"},
    {"severity":"medium","code":"WX_REGION"},
    {"severity":"low","code":"LOW_FUNCTION_DENSITY",
     "detail":"14 functions over 1.8 MiB of executable bytes"},
    {"severity":"low","code":"EMBEDDED_IMAGE_LIKELY",
     "detail":"autocorrelation stride 640 + low entropy over 1.2 MiB", "offsets":[…]}
  ]
}
```

Every finding carries offsets, so clicking it drives `SelectionStore` — the report is a navigation surface, not a text dump. `binviz triage <file> --json` gives the same headlessly.

- **File navigation** (the reference's `prevFile`/`nextFile`): sibling-directory list, prev/next buttons with keyboard shortcuts, drag-and-drop open, recent-files list. Selection state resets; view configuration (dtype, surface mode, colormap) persists across files — that persistence is what makes "flip through 200 firmware blobs looking for the odd one" actually work, and it's the reference's real workflow.
- Layout: tabbed or grid view switcher; save/restore panel arrangement in `localStorage`.

**Success criteria:**
- Corpus expectations encoded in `manifest.json` and asserted: `hello_upx` → `likely_packed` with `HIGH_ENTROPY_EXEC` + `IMPORT_STARVED`; `hello_O2` → `likely_benign_binary`, no high-severity findings; `sample.png` → `non_executable`; truncated ELF → `corrupt`.
- **False-positive check:** `sample.zip` and a binary with a large compressed resource must **not** be `likely_packed`. High entropy in a non-executable region is a different finding. Failure here means the classifier isn't region-aware.
- `bayer_raw.bin` triggers `EMBEDDED_IMAGE_LIKELY`.
- Clicking any finding navigates every view.
- Next/prev through a 50-file directory keeps dtype and surface mode fixed; each file's first paint is < 500 ms warm.

---

### Phase 12 — Scale hardening (trigger-driven, not scheduled)

Each item is independent and should be entered **with a measured number in hand**. Implementing any speculatively is exactly the over-engineering this plan avoids.

- **Files > 1 GiB:** chunked streaming analysis, progress reporting, cached-artifact size cap. Test with 2 GiB.
- **Whole-program call graph > 50k nodes:** WebGL — instanced quads for nodes, one line buffer for edges, text dropped below a zoom threshold (so no SDF atlas needed). `regl` or raw WebGL2; no general graph library.
- **Signal pyramid:** if refetch-on-zoom shows latency, precompute binned levels (n = 2k, 8k, 32k) at analysis time and serve the nearest.
- **Sliding-window entropy:** incremental count updates if the 2 s target is missed.
- **Dot plot on huge ranges:** tile the accumulator and stream tiles.

---

## 4. Phase Dependencies

```
P0 ─▶ P1 ─▶ P2 ─┬──────────▶ P3 ─────────────┐
                │                             │
                └─▶ P4 ─▶ P5 ────────────┐    │
                                         │    │
                          P6 ◀───────────┴────┘
                           │
                           ▼
                          P7 ─┬─▶ P8 ─┐
                              ├─▶ P9 ─┤
                              └─▶ P10─┴─▶ P11 ─▶ P12
```

**True blockers (cannot start without):**
- **P1 → P4/P5** absolutely. You cannot disassemble without arch, bits, endianness, entry point, and executable-region bounds.
- **P2 → P3** absolutely. Every surface consumes `elements()`/`quantise()`/`byte_class()`. Building surfaces first means writing reinterpretation inline six times.
- **P4 → P5** absolutely.
- **P6 → all frontend phases.** The wire format must exist first.
- **P7 → P8/P9/P10** — P7 builds the shell, `SelectionStore`, `colormap.ts`, and `canvas/raster.ts`, which all three import. Once P7's *shell* exists, P8, P9, and P10 are fully parallel.
- **P11** needs every view.

**Soft dependencies (worth being precise about):**
- **P2 needs P1 only for region labelling**, not for the maths. A session could technically do P2 first. **Don't** — region-aware `classify()` is what prevents the "compressed resource ⇒ packed!" false positive in P11, and retrofitting region-awareness means rewriting the classifier and recalibrating.
- **P3 does not block P4/P5** and vice versa. After P2, the analysis track (P4→P5) and the surface track (P3) are genuinely independent — this is the best parallelisation opportunity in the plan if two sessions can run.
- **P8's 3-D view needs P2's sparse trigram**, nothing else.
- **P9 needs P3's `image` and `dotplot` surfaces**; the hex viewer needs only P6.
- **P12** is optional and trigger-driven.

**Genuinely parallelisable:** P3 ∥ (P4→P5) after P2. P8 ∥ P9 ∥ P10 after P7.

**Critical path:** P0 → P1 → P2 → P4 → P5 → P6 → P7 → P10 → P11. Nine sessions minimum; **13–15 realistically**, with P3 the most likely to need splitting.

---

## 5. Known Challenges in This Domain

### 5.1 Offset vs. address confusion
The most common structural bug, and it hides. PE `SectionAlignment` ≠ `FileAlignment`; ELF `.bss` has virtual size and no file bytes; Mach-O `__PAGEZERO` maps nothing. Symptoms surface late: cross-view links land a few KB off, disassembly of the right function starts mid-instruction. **Addressed by:** P1 before everything, exactly two conversion functions, non-file-backed regions materialised explicitly, round-trip property tests over random offsets.

### 5.2 Downsampling destroys the signal you're looking for
A 100 MB file on a 2000 px canvas is 50 KB per pixel. Naive mean or nearest-neighbour aggregation makes a single 4 KiB encrypted blob vanish — and that blob is the reason the tool was opened. **Addressed by:** aggregation in the analysis layer, min/mean/max envelope rendering, and an explicit regression test that injects one high-entropy block into a zero file and asserts survival through binning. Related and equally easy to get wrong: **byte-class rasters must aggregate by mode, not mean** — averaging class IDs invents classes that don't exist.

### 5.3 The entropy threshold folklore
"Entropy > 7.0 means packed" is repeated everywhere and is window-size-dependent nonsense. At 256-byte windows uniform random data measures ≈7.1–7.2 (plug-in bias; counts are Poisson(1) over 256 symbols), so the threshold is nearly meaningless there — while compressed PNG resources, encrypted config blobs, and UTF-16-heavy `.rodata` all trip it while being benign. **Addressed by:** dual windows with separate roles, thresholds derived from a checked-in `calibration.json`, region-aware classification, and an explicit false-positive test in P11.

### 5.4 Element width changes what the data *is*
A 16-bit sensor dump histogrammed as u8 looks like noise. An f32 array looks like structured garbage. This is why the reference has a dtype enum, and it's under-appreciated. The trap on the way in: **you cannot histogram u16 directly** (a 65536² bigram is 34 GB), so quantisation to 256 bins is mandatory — and the quantisation choice silently determines what the plot shows. **Addressed by:** one `quantise()` returning its method in metadata, percentile-based bounds for floats (raw min/max is destroyed by one outlier), packed-12-bit handled correctly, and `ramp16.bin`/`floats.bin` as ground truth.

### 5.5 O(n²) visualisations
The dot plot is the honest example: a 1 MiB range is 10¹² comparisons. Implementations either cap the input to uselessly small ranges or hang. **Addressed by:** progressive stochastic sampling with a visible progress figure (the reference's approach, preserved), plus an exact hash-bucketed mode for ranges under ~256 KiB, plus always reporting which mode ran — because a sparse sampled plot reads as "no self-similarity" when it means "we barely looked."

### 5.6 3-D point clouds are ambiguous when static
A trigram cube rendered as opaque depth-tested points hides its interior; rendered as a still image it has no depth cue at all. **Addressed by:** the `overlap` additive-blending mode (interior density becomes visible), motion parallax via trackball plus idle auto-spin, a wireframe bounding cube for reference, and server-side thresholding so point counts stay in the 60 fps range.

### 5.7 Row stride is the hidden control in image views
A raw image at the wrong stride looks like diagonal noise, and users conclude "there's no image here." **Addressed by:** the FFT-autocorrelation stride suggester surfacing top-3 candidate lags as one-click buttons — ~15 lines of numpy that convert a guessing game into a click.

### 5.8 Stripped binaries
No symbols means function boundaries are guesses. Prologue signatures fail at `-O2`; tail calls blur function ends; hot/cold splitting scatters one function across the address space; PLT thunks pollute the list. **Addressed by:** a five-tier discovery cascade where every function records its tier and confidence, provenance shown in the UI, and an acceptance criterion that is a *measured recall number against the unstripped twin* rather than pass/fail. Honest coverage reporting over inflated counts.

### 5.9 Packed binaries make static analysis meaningless
A UPX'd `.text` is compressed; disassembling it yields thousands of plausible-looking garbage functions, which many tools render without comment. **Addressed by:** detect (executable-region entropy vs. calibration + import starvation + entrypoint outside named sections + unusual section names), then *refuse to pretend* — banner, empty-but-explained function list, and the entropy/histogram/dot-plot views (which remain fully valid on packed data) doing the work instead. Unpacking is out of scope; the escape hatch is `--raw` on a memory dump, which P1's raw fallback already supports.

### 5.10 Indirect control flow
Every `switch` becomes an indirect jump; every vtable and function-pointer call is an unresolved edge. Full resolution needs value-set analysis — a research project. **Addressed by:** never dropping an indirect edge silently (typed `indirect_unresolved` edge to a visible sentinel plus an `unresolved` record), and one bounded, validated jump-table matcher where any validation failure abandons the table rather than emitting wrong targets.

### 5.11 Graph layout is the frontend performance cliff
Naive implementations re-run layout on every pan, run it on the main thread, or reach for SVG at 5,000 nodes. Each independently kills interactivity. **Addressed by:** layout in a Worker, cached by `(function_va, collapse_state)`, Canvas2D + quadtree hit-testing, LOD dropping text below a zoom threshold, and an explicit element-count rule so the WebGL decision is measurement-driven.

### 5.12 Data-in-code and ARM mode switching
ARM literal pools live inside `.text`; Thumb and ARM interleave. Decoding a literal pool as instructions, or Thumb as ARM, produces garbage that *looks* like real disassembly. **Addressed by:** ELF mapping symbols (`$a`/`$t`/`$d`) into `arch_ranges` at P1, mode-aware decoding with a single alternate-mode retry, `$d` ranges never swept.

### 5.13 Memory
A 1 GiB binary must not become 4 GiB of Python objects, and a 256³ trigram must not be materialised densely per request. **Addressed by:** mmap everywhere, numpy views not copies, chunked bigram accumulation (the naive one-liner allocates 4× the file size), sparse trigram storage, compact record arrays for large functions, and asserted peak-RSS bounds in the P2 tests.

---

## 6. Reference Project Insights — kentavv/binary_viewer

C++/Qt5, CMake + vcpkg, GPL-3.0, ~92 commits. Eight views driven from `main_app`, a shared `histogram_calc` core, `QGLWidget` for the 3-D histogram, QDarkStyleSheet for theming. Lineage it cites: Cantor.Dust, Greg Conti's binvis, binglide, Veles, binwalk.

### Patterns worth taking

1. **A single narrow analysis header serving every view.** `histogram_calc.h` is ~5 functions and every visualisation consumes it. Same instinct as our `stats.py` + `elements.py` core, and it's why adding a ninth view later is cheap.
2. **The dtype enum applied twice.** `histo_dtype_t` for histograms and `ImageView::dtype_t` for pixels are the same idea — raw bytes are not always bytes. We unify them into one `elements()`, which the reference does not: it reimplements width handling per view.
3. **Range selection as a broadcast signal.** `rangeSelected(float,float)` from the overall and plot views into `main_app` and out to everything else is exactly the right architecture, and it's the single most valuable thing to copy. We generalise it (absolute offsets, plus VA and function, so the CFG can join) but the shape is theirs.
4. **Overall + zoomed as the same widget, twice.** One component, two bindings. Clean, and we do the same.
5. **Progressive stochastic dot plot.** `advance_mat(bs, rand)` with a `pts_i_` cursor and a `max_samples` cap is the correct answer to an O(n²) visualisation, and it's non-obvious enough that arriving at it independently is unlikely. Copy the *approach* (not the code — see licensing below).
6. **`overlap_` on the 3-D histogram.** A one-checkbox feature that decides whether you can see inside the point cloud at all. Easy to omit; we don't.
7. **Prev/next file navigation with persistent view settings.** Reveals the actual workflow — flipping through many files with a fixed lens, looking for the anomalous one. Our file navigation is designed around that, not treated as a convenience.
8. **Hilbert curve as an alternate whole-file layout.** Cheap (`d2xy`), and it preserves locality that a linear strip destroys at every row wrap. First-class in our plan, with `xy2d` shipped so it's clickable.

### What we do differently, and why

| binary_viewer | This project | Reasoning |
|---|---|---|
| C++/Qt5 desktop, CMake + vcpkg | Python backend + browser frontend | LIEF/Capstone/numpy are Python-first; the CFG wants a browser layout engine (elkjs) and a DOM control surface. Qt would mean writing Sugiyama layout from scratch. Also: zero install friction, and the analysis layer becomes scriptable (`binviz triage --json` in a pipeline) instead of GUI-locked. |
| **No container parsing** — flat byte stream only | `BinaryModel` with regions, symbols, off↔va | The biggest architectural divergence. A flat stream can't say "high entropy *in an executable section*", can't link a raster pixel to a function, and can't distinguish a compressed resource from a packed `.text`. Our entire triage value depends on region awareness. |
| **No disassembly at all** | Capstone-based CFG recovery | The CFG view simply doesn't exist there. It's also where the most novel design work is, which is why P4/P5 get two sessions. |
| Entropy at a fixed 256-byte block | Dual window (256 display / 4096 decision) + corpus calibration | 256-byte windows measure ≈7.1–7.2 on random data. Fine for texture; using it for decisions is where false "packed" verdicts come from. |
| Eight views, eight bespoke widgets, width handling reimplemented per view | One `Surface` protocol + one raster canvas component | Parity is bought once. It's also what makes a ninth view (say, a bit-plane view or a string-density map) an afternoon rather than a session. |
| Dense 3-D histogram in GL immediate mode | Sparse `(x,y,z,count)` extraction + WebGL2 point cloud | A dense 256³ uint32 is 67 MB, mostly zero. Sparse form is 10k–500k points, ships over HTTP, and uploads as one buffer. |
| Dot plot: sampled only | Sampled **+ exact hash-bucketed mode** under ~256 KiB, mode reported | The common case is inspecting one section, where exact is both feasible and strictly better — and a sampled plot must never be mistaken for a complete one. |
| Image view: user guesses the stride | Same controls **+ autocorrelation stride suggester** | Converts the tool's biggest usability cliff into a click. |
| Hex view: plain dump | + region/symbol annotation gutter | Free once P1 exists. |
| Views are independent windows behind a dropdown | Single `SelectionStore`, bidirectional linking across all nine | Nine unlinked views are nine tools. The linkage — click an entropy spike, land in the CFG block; brush a bigram cell, see where those pairs live — is the actual product. |
| GPL-3.0 | Permissive (MIT/Apache-2.0) | Follows from choosing Capstone over radare2; keeps embedding options open. **Consequence: read binary_viewer for architecture, never copy its source.** GPL-3.0 would infect the project. Reimplement concepts from the descriptions in this plan. |

---

## 7. Getting started

The next session opens this file and executes **Phase 0 only**. "Done" means: `make -C corpus` builds every sample from clean, `corpus/manifest.json` describes them, `binviz probe` runs on each without error, `pytest` green. Do not start Phase 1 in the same session — the corpus is what every later acceptance criterion is written against, and it deserves to be correct before anything depends on it.

Open questions, to resolve when they become blocking and not before:
- Whether to ship the r2 oracle backend at all — decide at P5, based on measured stripped-binary recall.
- Whether Phase 3 splits into two or three sessions — decide when `image.py` + Bayer is written and you can see how much is left.
- Whether the 3-D trigram earns its screen space versus three 2-D projections — decide at P8, after looking at the plates.
- PDB symbol loading for PE — a whole subsystem, currently out of scope; revisit only if the corpus grows a Windows arm.
