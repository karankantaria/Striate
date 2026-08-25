import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BYTE_CLASS_COLORS, BYTE_CLASS_NAMES, byteClassLut, GRAY, INFERNO,
  LOCATE_RGB, MAGMA, SERIES, VIRIDIS,
} from "../src/colormap.ts";

test("LUTs are 256 RGB entries", () => {
  for (const lut of [VIRIDIS, MAGMA, INFERNO, GRAY, byteClassLut()]) {
    assert.equal(lut.length, 768);
  }
});

test("viridis luminance is monotonically increasing (perceptually uniform)", () => {
  let prev = -1;
  for (let i = 0; i < 256; i++) {
    const lum = 0.2126 * VIRIDIS[i * 3] + 0.7152 * VIRIDIS[i * 3 + 1] +
      0.0722 * VIRIDIS[i * 3 + 2];
    assert.ok(lum >= prev - 0.75, `luminance dip at ${i}`);
    prev = Math.max(prev, lum);
  }
});

test("viridis endpoints are dark-purple and yellow", () => {
  const [r0, g0, b0] = [VIRIDIS[0], VIRIDIS[1], VIRIDIS[2]];
  const [r1, g1, b1] = [VIRIDIS[765], VIRIDIS[766], VIRIDIS[767]];
  assert.ok(b0 > r0 && b0 > g0, "start leans blue/purple");
  assert.ok(r1 > 200 && g1 > 200 && b1 < 100, "end is yellow");
});

test("byte-class LUT maps ids to the validated palette, saturating past 5", () => {
  const lut = byteClassLut();
  for (let id = 0; id < 6; id++) {
    const want = BYTE_CLASS_COLORS[id];
    const got = "#" + [lut[id * 3], lut[id * 3 + 1], lut[id * 3 + 2]]
      .map((v) => v.toString(16).padStart(2, "0")).join("");
    assert.equal(got, want, `class ${id}`);
  }
  // out-of-range values (never sent by the server) clamp to the last class
  assert.equal(lut[255 * 3], lut[5 * 3]);
});

/* ARCHITECTURE.md §3 fixes Striate as dark-only. A second palette creeping back
   is how the app ends up with colours nobody validated against --ink. */
test("there is one palette, not one per theme", () => {
  assert.ok(Array.isArray(BYTE_CLASS_COLORS), "BYTE_CLASS_COLORS is keyed by theme again");
  assert.ok(Array.isArray(SERIES), "SERIES is keyed by theme again");
  assert.equal(typeof LOCATE_RGB, "string");
  assert.equal(BYTE_CLASS_COLORS.length, BYTE_CLASS_NAMES.length);
});
