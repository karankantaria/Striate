import assert from "node:assert/strict";
import { test } from "node:test";

import { offToVa, vaToOff } from "../src/store.ts";

// mirrors the model's compact interval table: [file_off, size, vaddr]
const MAPPINGS: [number, number, number][] = [
  [0, 0x1000, 0x400000],
  [0x1000, 0x800, 0x402000],   // alignment divergence, like PE
  [0x2000, 0x100, 0x410000],
];

test("offToVa inside intervals", () => {
  assert.equal(offToVa(MAPPINGS, 0), 0x400000);
  assert.equal(offToVa(MAPPINGS, 0xfff), 0x400fff);
  assert.equal(offToVa(MAPPINGS, 0x1000), 0x402000);
  assert.equal(offToVa(MAPPINGS, 0x17ff), 0x4027ff);
  assert.equal(offToVa(MAPPINGS, 0x2050), 0x410050);
});

test("offToVa outside any interval is null", () => {
  assert.equal(offToVa(MAPPINGS, 0x1800), null);  // gap between intervals
  assert.equal(offToVa(MAPPINGS, 0x2100), null);  // past the last
  assert.equal(offToVa([], 0), null);
});

test("va<->off round-trips over every mapped offset", () => {
  for (const [fo, size] of MAPPINGS) {
    for (let off = fo; off < fo + size; off += 7) {
      const va = offToVa(MAPPINGS, off);
      assert.notEqual(va, null);
      assert.equal(vaToOff(MAPPINGS, va!), off);
    }
  }
});

test("vaToOff unmapped is null", () => {
  assert.equal(vaToOff(MAPPINGS, 0x300000), null);
  assert.equal(vaToOff(MAPPINGS, 0x402800), null);
});

test("raw-fallback identity mapping", () => {
  const raw: [number, number, number][] = [[0, 1234, 0]];
  assert.equal(offToVa(raw, 500), 500);
  assert.equal(vaToOff(raw, 500), 500);
});
