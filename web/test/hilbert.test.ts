import assert from "node:assert/strict";
import { test } from "node:test";

import { d2xy, offsetAtXY, xy2d } from "../src/hilbert.ts";

test("xy2d(d2xy(d)) == d for all d at order 8", () => {
  const order = 8;
  const n = (1 << order) * (1 << order);
  for (let d = 0; d < n; d++) {
    const [x, y] = d2xy(order, d);
    assert.equal(xy2d(order, x, y), d, `d=${d}`);
  }
});

test("d2xy stays in bounds and visits every cell (order 6)", () => {
  const order = 6;
  const side = 1 << order;
  const seen = new Set<number>();
  for (let d = 0; d < side * side; d++) {
    const [x, y] = d2xy(order, d);
    assert.ok(x >= 0 && x < side && y >= 0 && y < side);
    seen.add(y * side + x);
  }
  assert.equal(seen.size, side * side);
});

test("consecutive curve positions are 4-adjacent (locality)", () => {
  const order = 7;
  let [px, py] = d2xy(order, 0);
  for (let d = 1; d < (1 << order) * (1 << order); d++) {
    const [x, y] = d2xy(order, d);
    assert.equal(Math.abs(x - px) + Math.abs(y - py), 1, `jump at d=${d}`);
    [px, py] = [x, y];
  }
});

test("offsetAtXY matches the server's cell->offset formula", () => {
  const order = 5;
  const nCells = (1 << order) * (1 << order);
  const start = 4096, nbytes = 100_000;
  for (let d = 0; d < nCells; d++) {
    const [x, y] = d2xy(order, d);
    assert.equal(
      offsetAtXY(order, x, y, start, nbytes),
      start + Math.floor((d * nbytes) / nCells));
  }
});
