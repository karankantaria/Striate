"""The measured thresholds have to survive being installed.

Plan §5.3 refuses "entropy > 7.0 means packed" and replaces it with
thresholds derived from a checked-in `corpus/calibration.json`. But
`corpus/` is not in the wheel, and `signals._find_calibration()` only knew
about repo layouts — so a `pipx install binviz`, the artifact RELEASE.md §1
calls canonical, ran on `_FALLBACK_CAL` instead. Silently: nothing in the
API, the CLI or the logs said which numbers were in force, and the gaps are
not rounding (`code_h_lo` 5.31 measured against 4.5 hardcoded, 0.8 bits).

So the installed product classified windows differently from the checkout
its tests pass in. These pin the three pieces that stop that recurring:
the file is staged, it is found, and changing it invalidates the analyses
computed under the old numbers.
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from binviz import signals
from binviz.cache import params_fingerprint

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "corpus" / "calibration.json"
PACKAGED = Path(signals.__file__).resolve().parent / "calibration.json"


def _build_ui():
    """tools/build_ui.py, which is a script rather than an importable
    module — the staging logic is the thing under test, not the CLI."""
    spec = importlib.util.spec_from_file_location(
        "binviz_build_ui", ROOT / "tools" / "build_ui.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------- staging

def test_build_ui_stages_the_calibration(tmp_path, monkeypatch):
    mod = _build_ui()
    target = tmp_path / "calibration.json"
    monkeypatch.setattr(mod, "CAL_TARGET", target)
    assert mod._stage_calibration() is True
    assert json.loads(target.read_text(encoding="utf-8")) == \
        json.loads(CANONICAL.read_text(encoding="utf-8"))


def test_staging_fails_loudly_when_the_calibration_is_missing(tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """Unlike the icons, which degrade to a default window icon. A wheel
    with no branding looks plainer; a wheel with no calibration analyses
    differently and says nothing about it, so this one is fatal."""
    mod = _build_ui()
    monkeypatch.setattr(mod, "CAL_SRC", tmp_path / "nope.json")
    monkeypatch.setattr(mod, "CAL_TARGET", tmp_path / "calibration.json")
    assert mod._stage_calibration() is False
    assert "fall back to hardcoded thresholds" in capsys.readouterr().err


def test_the_package_declares_the_staged_file(caplog):
    """Staging it is useless if setuptools does not put it in the wheel —
    and that failure is invisible until someone installs the result."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"calibration.json"' in pyproject, \
        "package-data no longer ships the staged calibration"


def test_the_release_gate_checks_the_wheel_contains_it():
    """Same argument as §4.1's UI check: the staging step is a step someone
    can skip, and the resulting wheel installs and imports cleanly."""
    wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8")
    assert "binviz/calibration.json" in wf
    assert 'source != "fallback-defaults"' in wf


# -------------------------------------------------------------- lookup

def test_a_checkout_prefers_the_corpus_copy_over_the_staged_one():
    """The staged copy is a build-time snapshot. Someone who has just re-run
    corpus/calibrate.py must see the numbers they measured."""
    found = signals._find_calibration()
    assert found is not None
    assert os.path.samefile(found, CANONICAL)


def test_the_packaged_copy_is_found_when_there_is_no_checkout(monkeypatch):
    """The installed case, which is the one that was broken."""
    monkeypatch.delenv("BINVIZ_CALIBRATION", raising=False)
    monkeypatch.setattr(os.path, "exists",
                        lambda p: os.path.abspath(p) == str(PACKAGED))
    assert signals._find_calibration() == str(PACKAGED)


def test_the_environment_override_still_wins(tmp_path, monkeypatch):
    override = tmp_path / "mine.json"
    override.write_text('{"derived": {}, "source": "test"}', encoding="utf-8")
    monkeypatch.setenv("BINVIZ_CALIBRATION", str(override))
    assert signals._find_calibration() == str(override)


def test_the_fallback_is_not_a_shipping_mode():
    """`load_calibration()["source"]` is the observable that would have
    caught this, so it has to keep existing and keep being reported."""
    assert signals.load_calibration()["source"] != "fallback-defaults"
    assert "source" in signals._FALLBACK_CAL


# --------------------------------------------------------- invalidation

def test_the_cache_fingerprint_covers_the_thresholds(monkeypatch):
    """`params_fingerprint` is documented as hashing everything that changes
    analysis output besides the bytes. The triage verdict is a cached
    artifact derived from these numbers, so re-running calibrate.py — or
    moving between a checkout and an install, which used to shift
    `code_h_lo` by 0.8 bits — has to invalidate it."""
    before = params_fingerprint()

    moved = json.loads(json.dumps(signals.load_calibration()))
    moved["derived"]["packed_h_min"] += 0.1
    monkeypatch.setattr(signals, "_cal_cache", moved)

    assert params_fingerprint() != before, \
        "thresholds moved and every cached analysis stayed valid"


def test_the_fingerprint_is_stable_when_nothing_moves():
    assert params_fingerprint() == params_fingerprint()


# ------------------------------------------------------------ reporting

def test_the_server_reports_which_thresholds_are_in_force(tmp_path):
    from conftest import authed_client, make_app

    with authed_client(make_app(tmp_path)) as c:
        doc = c.get("/api/config").json()
    assert doc["calibration"] == signals.load_calibration()["source"]
    assert doc["calibration"] != "fallback-defaults"


@pytest.mark.parametrize("key", ["code_h_lo", "code_h_hi", "packed_h_min",
                                 "random_h_min", "random_chi2_max"])
def test_measured_and_hardcoded_thresholds_really_do_differ(key):
    """The premise of the whole file. If these ever converge, the fallback
    stops being dangerous — but they are measured values, so they will not,
    and a test that quietly passes for the wrong reason is worse than none."""
    measured = signals.load_calibration()["derived"][key]
    hardcoded = signals._FALLBACK_CAL["derived"][key]
    if measured == hardcoded:
        pytest.skip(f"{key} happens to match the fallback")
    assert measured != hardcoded
