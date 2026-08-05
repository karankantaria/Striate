import assert from "node:assert/strict";
import { test } from "node:test";

import { toDisplay } from "../src/transforms.ts";

// Semantics mirror surfaces/ngram.py:to_display — peak-normalised to 255
// with numpy-astype truncation, zeros always map to 0.

test("linear peak-normalises with truncation", () => {
  const out = toDisplay(new Uint32Array([0, 1, 2, 4]), "linear");
  // 255/4 per count, floor: 0, 63, 127, 255
  assert.deepEqual([...out], [0, 63, 127, 255]);
});

test("sqrt compresses magnitude", () => {
  const out = toDisplay(new Uint32Array([0, 1, 4, 16]), "sqrt");
  // sqrt: 0,1,2,4 -> *255/4 -> 0, 63, 127, 255
  assert.deepEqual([...out], [0, 63, 127, 255]);
});

test("log1p matches python semantics", () => {
  const counts = new Uint32Array([0, 1, 10, 1000]);
  const out = toDisplay(counts, "log1p");
  const peak = Math.log1p(1000);
  const expect = [...counts].map((c) => Math.trunc(Math.log1p(c) * 255 / peak));
  assert.deepEqual([...out], expect);
  assert.equal(out[3], 255);
});

test("rank flattens percentiles, ties broken by index", () => {
  const out = toDisplay(new Uint32Array([0, 5, 3, 10]), "rank");
  // ascending nonzero: 3 (rank 1), 5 (rank 2), 10 (rank 3); *255/3 floor
  assert.deepEqual([...out], [0, 170, 85, 255]);
  // stable tie-break: equal counts rank by index (numpy stable argsort)
  const tied = toDisplay(new Uint32Array([7, 7]), "rank");
  assert.deepEqual([...tied], [127, 255]);
});

test("all-zero input stays zero in every mode", () => {
  for (const mode of ["log1p", "rank", "sqrt", "linear"] as const) {
    const out = toDisplay(new Uint32Array(16), mode);
    assert.ok(out.every((v) => v === 0), mode);
  }
});

test("single nonzero cell hits 255", () => {
  for (const mode of ["log1p", "rank", "sqrt", "linear"] as const) {
    const out = toDisplay(new Uint32Array([0, 0, 42, 0]), mode);
    assert.equal(out[2], 255, mode);
    assert.equal(out[0], 0, mode);
  }
});
