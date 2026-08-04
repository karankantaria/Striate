#!/usr/bin/env python3
"""Generate corpus/calibration.json from measured corpus samples.

Raw per-sample measurements are recorded alongside the derived thresholds,
so a threshold can always be traced back to the data that produced it.
Re-run after changing entropy windows or rebuilding the corpus:

    python corpus/calibrate.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from binviz.parse import parse                      # noqa: E402
from binviz.signals import DECISION_WINDOW, DISPLAY_WINDOW  # noqa: E402
from binviz.stats import window_stats               # noqa: E402


def read(name: str) -> bytes:
    with open(os.path.join(OUT, name), "rb") as f:
        return f.read()


def region_bytes(name: str, region_name: str) -> bytes:
    m = parse(os.path.join(OUT, name))
    r = next(r for r in m.regions if r.name == region_name)
    return read(name)[r.file_off : r.file_off + r.file_size]


def measure(data: bytes, window: int) -> dict:
    s = window_stats(data, window, which=("entropy", "chi2", "printable_ratio"))
    h, chi2 = s["entropy"], s["chi2"]
    if len(h) == 0:
        return {"windows": 0}
    return {
        "windows": int(len(h)),
        "h_mean": round(float(h.mean()), 4),
        "h_std": round(float(h.std()), 4),
        "h_p10": round(float(np.percentile(h, 10)), 4),
        "h_p90": round(float(np.percentile(h, 90)), 4),
        "chi2_mean": round(float(chi2.mean()), 2),
        "chi2_p99": round(float(np.percentile(chi2, 99)), 2),
        "printable_mean": round(float(s["printable_ratio"].mean()), 4),
    }


def main() -> int:
    w, wd = DECISION_WINDOW, DISPLAY_WINDOW
    samples = {
        "zeros": read("zeros.bin"),
        "urandom": read("urandom.bin"),
        "ascii": read("ascii.txt"),
        "hello_static_text": region_bytes("hello_static", ".text"),
        "hello_O2_text": region_bytes("hello_O2", ".text"),
        # the UPX payload is the overlay: packed bytes outside every PT_LOAD
        "upx_payload": region_bytes("hello_upx", "<overlay>"),
    }
    raw = {
        name: {f"w{w}": measure(data, w), f"w{wd}": measure(data, wd)}
        for name, data in samples.items()
    }

    rand = raw["urandom"][f"w{w}"]
    code = raw["hello_static_text"][f"w{w}"]
    upx = raw["upx_payload"][f"w{w}"]

    derived = {
        # random: within noise of the measured urandom band, chi2 consistent
        "random_h_min": round(rand["h_mean"] - max(0.02, 4 * rand["h_std"]), 4),
        "random_chi2_max": round(rand["chi2_p99"] * 1.5, 2),
        # packed: midpoint between measured code and measured packed payload
        "packed_h_min": round((code["h_p90"] + upx["h_p10"]) / 2, 4),
        "code_h_lo": round(max(code["h_p10"] - 0.75, 3.0), 4),
        "code_h_hi": round(code["h_p90"] + 0.3, 4),
        "ascii_printable_min": 0.95,
        "zero_null_min": 0.995,
    }
    # code band must stay strictly below the packed threshold
    derived["code_h_hi"] = min(derived["code_h_hi"], derived["packed_h_min"] - 0.01)

    cal = {
        "decision_window": w,
        "display_window": wd,
        "source": "corpus/calibrate.py over corpus/out",
        "raw": raw,
        "derived": derived,
    }
    out_path = os.path.join(HERE, "calibration.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=2)
        f.write("\n")
    print(f"wrote {out_path}")
    for k, v in derived.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
