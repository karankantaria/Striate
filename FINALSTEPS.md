# Final steps

The short list between here and a published release. `RELEASE.md` §5 is the
work register and §7 is the publishing procedure; this file is the running
checklist, ticked as things land.

Started **2026-08-06**. Nothing in this file is committed yet.

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

- [ ] **PyPI pending publisher**, before anything else can run:
      owner `karankantaria`, repository `Striate`, workflow `publish.yml`,
      environment `pypi`. Attach a required reviewer there if you want a
      human in the loop.
- [ ] **`workflow_dispatch` rehearsal** — runs `build` + `floors` without
      publishing. This is the real test of the three fixes above.
- [ ] **Tag and publish.**
- [ ] **Release notes mention the one-time re-analysis.** The cache
      fingerprint moves (`d4480e63…` → `62b4fd7b…`), so the first open after
      upgrading re-analyses every cached binary. Correct — those verdicts
      used different thresholds — but it should not be a surprise.

---

## Watch on the first CI run

Verified locally, but not on a Linux runner:

- [ ] **The `floors` job's suite.** Every floor pin has a cp311 linux-x86_64
      wheel (asked of the index directly), but nobody has run the tests at
      those versions.
- [ ] **upx 4.x vs the 5.2.0 that packed `hello_upx`.** The runner image
      ships 4.2.2. Every assertion about that sample is a range or a
      verdict, never a hash — so it should hold. Most likely thing to bite.
- [ ] **The `publish` job.** OIDC against PyPI cannot be exercised anywhere
      but CI.

---

## Cannot be verified from here at all

Not blockers, and already in `RELEASE.md` §8 — listed so they are not
mistaken for tested:

- [ ] The `.icns` in a Dock, and the spec's darwin `BUNDLE` branch.
- [ ] The Linux/macOS pywebview backends, and `0o600` on a real POSIX host.
- [ ] The desktop file dialog's last inch — needs a human in front of it.

---

## Housekeeping

- [ ] **Nothing is committed.** 13 modified, 4 new (`FINALSTEPS.md`,
      `packaging/icons/icon.icns`, `tools/make_icns.py`,
      `tests/test_calibration_packaging.py`).
- [ ] **`.venv` has three extras** installed for this work and absent from
      `pyproject.toml`: `pyinstaller`, `build`, `pyyaml`. Uninstall if you
      want the venv back to the declared set.

Green as of the last run: **450 passed / 5 skipped** (Python),
**87 passed / 0 failed** (frontend).
