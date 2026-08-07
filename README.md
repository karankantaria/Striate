# Striate

Binary visualiser & triage tool: linked interactive views (entropy, histograms,
image/dot-plot surfaces, control-flow graphs) over a single shared
address-space model.

## What it does

Open a file and every view is looking at the same address space. Select a range
in one and the rest follow — the point is to answer "what is this region" by
looking at it several ways at once.

- **Map it.** `binviz model` parses ELF/PE/Mach-O through LIEF into regions,
  symbols and an offset↔virtual-address mapping, materialising gaps and
  overlays. Malformed input falls back to a raw model rather than failing.
- **Find the parts worth looking at.** Windowed entropy and other named
  signals, byte-class and Hilbert surfaces, and window classification against
  thresholds *measured* from a ground-truth corpus rather than picked
  (`PLAN.md` §5.3). Packed, encrypted, code and padding do not look alike.
- **Identify an encoding.** Bigram and sparse-trigram histograms, a dot plot
  for repeats and self-similarity, and an image view over 15 packed pixel
  formats and 24 Bayer modes — with a stride suggester, because the wrong row
  stride turns a photograph into diagonal noise and you conclude there is no
  photograph.
- **Read the code.** Capstone decode by linear sweep and recursive descent
  (differentially tested against objdump), a five-tier function-discovery
  cascade including jump tables, and control-flow graphs laid out in a worker
  — with the uncertainty of a recovered boundary drawn rather than hidden.
- **Get a verdict.** `binviz triage` says what the file looks like and why;
  in the UI each finding clicks through to the bytes it was derived from.

The UI is five workspaces — Overview, Bytes, Patterns, Code, and All — over the
same selection. Static analysis only: samples are parsed, never executed.

`PLAN.md` is the design; `HANDOVER.md` is the phase-by-phase history and the
gotchas list; `RELEASE.md` covers shipping, branding and known limitations.

## Quickstart

```sh
python -m venv .venv
# -c pins to the exact versions the suite is green against; pyproject.toml
# publishes ranges, so without it you get whatever resolves today
.venv/Scripts/pip install -e ".[dev]" -c constraints-dev.txt   # POSIX: .venv/bin/pip

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

# code
binviz disasm    corpus/out/hello_O2 --limit 20
binviz functions corpus/out/hello_static --sort size
binviz cfg       corpus/out/hello_O2 --func main --dot main.dot

# the verdict, and why
binviz triage corpus/out/hello_upx
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

### Desktop window

```sh
pip install "binviz[app]"
binviz app                    # native window; --browser for your browser
```

Same server, same token, same `--root` confinement as `binviz serve` — the
only difference is what displays it. Without pywebview installed, `binviz
app` opens your browser instead.

It prints the URL it is serving on, deliberately: wrapping the UI in a
window does not remove the network listener, it only makes it easier to
forget there is one. The listener is authenticated either way, and there is
no `--no-auth` on `binviz app`.

The window exposes exactly one function to the page — a native file picker —
and nothing else. See `src/binviz/app.py` for why that list is as short as
it is.

### Building a standalone app

Releases ship a wheel and nothing else. An unsigned frozen Python
executable that bundles capstone and lief and exists to dissect packed
binaries is exactly the profile SmartScreen and AV heuristics
false-positive on — so instead of shipping one, the repo carries what you
need to build it yourself, which sidesteps code signing entirely.

```sh
pip install pyinstaller                # 6.x
python tools/build_ui.py               # builds web/ and stages it into the package
pyinstaller packaging/binviz.spec      # -> dist/binviz/
```

Expect ~100 MB, dominated by numpy and lief. It is a onedir bundle, not a
single self-extracting file: launch `dist/binviz/binviz.exe` (or
double-click it) for the desktop window, or give it any subcommand —
`dist/binviz/binviz.exe triage sample.exe` — because the frozen build is
the whole CLI, not just the window.

The staging step is not optional. `web/dist` lives outside the Python
package, so skipping it produces an app whose window opens on a JSON 404;
the spec refuses to build rather than let that happen quietly.

On macOS the same command also produces `dist/Striate.app`, branded from
`packaging/icons/icon.icns`. Neither has been run on a Mac — see
RELEASE.md §8.

`--root` still defaults to the working directory, so a double-clicked
executable is confined to the folder it starts in — which is usually the
app's own folder. Set the shortcut's "Start in", or launch it with
`--root DIR`.

### Signing in

By default there is no login screen and nothing to copy: the server mints a
session token and injects it into the page it serves, so opening
`http://127.0.0.1:8000/` just works while every API call is still
authenticated.

On a machine you share, turn on the sign-in screen:

```sh
binviz passwd                 # prompts; scrypt digest, mode 0600
binviz serve --auth local
```

If you skip `binviz passwd`, the first sign-in claims the install — the
startup banner warns about that, because whoever reaches the port first
becomes the account.

The login screen is **not** the security boundary; the token check on every
`/api` route is. Anything on the machine can skip the form and call the API
directly, which is exactly why the token exists. See `SECURITY.md`.

## Licence

MIT — see [LICENSE](LICENSE).

Plates live in `docs/plates/` — regenerate with `python docs/make_plates.py`.
Start with `image_rgb_bars.png` next to `image_rgb_bars_wrong_stride.png`
(why the stride suggester exists), and `hilbert_byteclass_static.png` next to
`hilbert_byteclass_upx.png` (structure versus packed noise).

The corpus cross-compiles ELF samples with `zig cc`, so no Linux toolchain is
needed on Windows/macOS — samples are parsed, never executed.
