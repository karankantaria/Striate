# Final steps

The short list between here and a published release. `RELEASE.md` §5 is the
work register and §7 is the publishing procedure; this file is the running
checklist, ticked as things land.

Started **2026-08-06**. Everything under "Done" and "Decided" is
committed, through `63fff79`. Updated **2026-08-25**.

---

## Done

- [x] **PyInstaller spec** — `packaging/binviz.spec` + `packaging/launcher.py`,
      onedir, `upx=False`, `console=True`, refuses to build without a staged
      frontend. Built and run on Windows (99 MB): `probe`, `triage`, `serve`
      (UI mounted, deep links survive a refresh, `/api` still 401 unauthed)
      and the desktop window all exercised from the frozen bundle.
      README instructions added; 10 tests in `tests/test_packaging.py`.
- [x] **macOS `.icns`** — `tools/make_icns.py` writes the container directly,
      so it did not need a Mac. Ten entries matching `iconutil`'s output,
      downsampled from the 1024px master rather than redrawn. Container
      walked, every payload checked, 1024 entry pixel-identical to the
      master (5 more tests).
- [x] **Release workflow rehearsed locally** — it would have failed on its
      first run. Three fixes in `publish.yml`: build the corpus in both jobs
      (a fresh checkout has no samples and `conftest` *fails* rather than
      skips — measured 168 failures + 40 errors), run the 87 frontend tests
      in the release gate, and Node 22 → 24.
- [x] **Calibration ships in the wheel** — `corpus/` is not packaged, so an
      installed binviz was analysing on `_FALLBACK_CAL` (`code_h_lo` 4.5
      against the measured 5.31). Staged by `tools/build_ui.py`, asserted by
      the release gate, reported by `/api/config`, and folded into
      `params_fingerprint()` so moved thresholds invalidate the analyses
      computed under the old ones. Verified from a clean venv outside the
      repo (16 more tests).

---

## Decided 2026-08-07 (`RELEASE.md` §6, now "Settled decisions")

- [x] **`README.md`'s status section** — the phase list is gone rather than
      renumbered. Phase numbers are internal vocabulary and go stale by
      construction; the first screen now says what the tool does, and
      `HANDOVER.md` keeps the history. Quickstart extended with `disasm`,
      `functions`, `cfg` and `triage`, all run against corpus samples
      before being written down.
- [x] **`lief>=1.0,<1.1`** — keep. The failure mode is a plausible but
      wrong parse, and the parser tests check the pin rather than replace
      it: they run against whatever is installed, so they cannot vouch for
      a version nobody has run them on. Price accepted — a binviz release
      for lief 1.1 even if it is compatible.
- [x] **`--auth local`'s claim window** — keep. Refusing to start without a
      credential locks out the double-click desktop user, which is the
      majority case for `binviz app`. Scope recorded in §6: `local` is
      opt-in, so the trigger for revisiting is the shared-machine case
      becoming common, not general unease.
- [x] **Two version numbers stay separate** — confirmed. They read the same
      today, which is what makes collapsing them look free; the next
      frontend-only patch is where you would find out it is not.

---

## The release (owner-only, `RELEASE.md` §7)

- [x] **PyPI pending publisher** — registered **2026-08-25**. Project
      `binviz`, owner `karankantaria`, repository `Striate`, workflow
      `publish.yml`, environment `pypi`. Matches the `environment:` block in
      the workflow, which is half the identity PyPI checks. No required
      reviewer attached; add one on the `pypi` environment if a publish
      should need a human.
- [~] **`workflow_dispatch` rehearsal** — first run **2026-08-25**
      ([32893805688]). Both jobs reached `pytest` and both failed there on
      the *same single test*, `test_differential_objdump[hello_static]`:
      **1 failed, 453 passed, 2 skipped.** `publish` correctly did not
      appear — a dispatch cannot publish. Fixed in `tests/test_disasm.py`
      (see below); needs a second dispatch to confirm green.
      **Push first:** the workflow builds what is on `origin/main`, so a
      dispatch with the working tree uncommitted rehearses the previous
      commit and proves nothing about the code being tagged.

      [32893805688]: https://github.com/karankantaria/Striate/actions/runs/32893805688

- [x] **objdump folds FWAIT; capstone does not.** The only rehearsal
      failure, and not a binviz bug. `9b` is an instruction (`fwait`);
      binutils folds it into the following x87 op, so `9b d9 ee` prints as
      one three-byte `fldz` where we decode `wait` + `fldz`. Three sites in
      `hello_static`, all ours-only, **0 objdump-only** — we mark an extra
      valid start, never miss one. Invisible on Windows because the test
      skips without binutils. `_folded_fwait()` now drops exactly these
      sites: the previous address must be a one-byte `wait` both decoders
      start at, and the address itself one only we start. Deliberately
      narrow — 25 of the 28 `wait`s in that binary are *not* folded by
      objdump and already agree — and checked against three negative
      controls (a non-FWAIT ours-only start, an objdump-only start, and the
      `wait`'s own start) that all still fail as they should.
- [ ] **Tag and publish.**
- [ ] **Release notes mention the one-time re-analysis.** The cache
      fingerprint moves (`d4480e63…` → `62b4fd7b…`), so the first open after
      upgrading re-analyses every cached binary. Correct — those verdicts
      used different thresholds — but it should not be a surprise.

---

## Watch on the first CI run

Verified locally, but not on a Linux runner:

- [x] **The `floors` job's suite.** Ran. Install at the declared minimums
      succeeded, the corpus built, and the suite was **453 passed / 1
      failed** — the failure being the objdump test above, which is
      version-independent. The floor pins are green.
- [x] **upx 4.x vs the 5.2.0 that packed `hello_upx`.** Held. The
      predicted most-likely failure did not happen: `corpus/build.py`
      completed in *both* jobs, so upx 4.2.2 packs the zig-built sample
      within every range and verdict asserted about it.
- [x] **Node 24 and the frontend suite on Linux.** Both jobs cleared
      "build and stage the frontend", so `npm ci`, the 87 tests and the
      Vite build all pass on the runner.
- [ ] **The `publish` job.** OIDC against PyPI cannot be exercised anywhere
      but CI. Still the one thing only a real tag proves.
- [ ] **Deprecation notice, not a failure.** `actions/checkout@v4`,
      `setup-node@v4` and `setup-python@v5` target Node 20 and are being
      forced onto Node 24 by the runner. Harmless now; bump the action
      majors before it stops being a warning.

---

## Cannot be verified from here at all

Not blockers, and already in `RELEASE.md` §8 — listed so they are not
mistaken for tested:

- [ ] The `.icns` in a Dock, and the spec's darwin `BUNDLE` branch.
- [ ] The Linux/macOS pywebview backends, and `0o600` on a real POSIX host.
- [ ] The desktop file dialog's last inch — needs a human in front of it.

---

## Housekeeping

- [ ] **Working tree: 7 modified, nothing new.** Two changes, both
      documented and tested, neither yet on `origin/main`:
      the **palette re-layering** (a new `--void` token; surfaces run down
      from `--ink`, `--plum` demoted to small raised elements,
      `colormap.ts` validated against `--ink`) and the **double-click
      default**, now `app --auth local` rather than bare `app`. These are
      what the rehearsal above needs pushed.
- [ ] **`.venv` has three extras** installed for this work and absent from
      `pyproject.toml`: `pyinstaller`, `build`, `pyyaml`. Uninstall if you
      want the venv back to the declared set.

Green as of **2026-08-25**: **451 passed / 5 skipped** (Python; the 6
deselected are the `perf` marker, excluded by `pyproject.toml`'s
`addopts`), **87 passed / 0 failed** (frontend).
