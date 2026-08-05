import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_SPACER_PX, PAGE_BYTES, ROW_BYTES,
  firstRowAt, pageSpan, scrollMapFor, scrollTopForRow,
  sortSymbols, symbolAt,
} from "../src/views/hexutil.ts";
import type { Symbol_ } from "../src/api.ts";

const ROW_H = 18;

test("small file maps scroll 1:1", () => {
  const map = scrollMapFor(1000, ROW_H);
  assert.equal(map.spacerH, 18000);
  assert.equal(map.scale, 1);
  assert.equal(firstRowAt(map, 0, ROW_H), 0);
  assert.equal(firstRowAt(map, 18, ROW_H), 1);
  assert.equal(firstRowAt(map, 17, ROW_H), 0);
  assert.equal(scrollTopForRow(map, 42, ROW_H), 42 * 18);
});

test("1 GiB file compresses the spacer under the browser cap", () => {
  const totalRows = Math.ceil((1 << 30) / ROW_BYTES);   // 67,108,864 rows
  const map = scrollMapFor(totalRows, ROW_H);
  assert.equal(map.spacerH, MAX_SPACER_PX);
  assert.ok(map.scale > 1);
  // scroll extremes reach row extremes
  assert.equal(firstRowAt(map, 0, ROW_H), 0);
  const lastRow = firstRowAt(map, map.spacerH, ROW_H);
  assert.ok(Math.abs(lastRow - totalRows) <= map.scale / ROW_H + 1,
    `${lastRow} vs ${totalRows}`);
});

test("scroll map round-trips rows within one row of error", () => {
  const totalRows = Math.ceil((1 << 30) / ROW_BYTES);
  const map = scrollMapFor(totalRows, ROW_H);
  for (const row of [0, 1, 12345, 1_000_000, totalRows - 1]) {
    const back = firstRowAt(map, scrollTopForRow(map, row, ROW_H), ROW_H);
    assert.ok(Math.abs(back - row) <= 1, `row ${row} -> ${back}`);
  }
});

test("pageSpan covers the byte range inclusively", () => {
  assert.deepEqual(pageSpan(0, 1), [0, 0]);
  assert.deepEqual(pageSpan(0, PAGE_BYTES), [0, 0]);
  assert.deepEqual(pageSpan(0, PAGE_BYTES + 1), [0, 1]);
  assert.deepEqual(pageSpan(PAGE_BYTES - 1, PAGE_BYTES + 1), [0, 1]);
  assert.deepEqual(pageSpan(3 * PAGE_BYTES, 3 * PAGE_BYTES + 5), [3, 3]);
  // empty range never spans backwards
  assert.deepEqual(pageSpan(PAGE_BYTES, PAGE_BYTES), [1, 1]);
});

/* -------------------------------------------------------------- symbols */

function sym(name: string, va: number, size: number): Symbol_ {
  return { name, va, size, kind: "func", source: "symtab" };
}

test("symbolAt finds the containing symbol via bisect", () => {
  const sorted = sortSymbols([
    sym("c", 0x300, 0x40), sym("a", 0x100, 0x20), sym("b", 0x200, 0x10),
  ]);
  assert.equal(symbolAt(sorted, 0x100)?.name, "a");
  assert.equal(symbolAt(sorted, 0x11f)?.name, "a");
  assert.equal(symbolAt(sorted, 0x120), null);       // one past the end
  assert.equal(symbolAt(sorted, 0x205)?.name, "b");
  assert.equal(symbolAt(sorted, 0x2ff), null);       // gap between b and c
  assert.equal(symbolAt(sorted, 0x33f)?.name, "c");
  assert.equal(symbolAt(sorted, 0x50), null);        // before everything
});

test("zero-size symbols are addressable at exactly their VA", () => {
  const sorted = sortSymbols([sym("marker", 0x400, 0)]);
  assert.equal(symbolAt(sorted, 0x400)?.name, "marker");
  assert.equal(symbolAt(sorted, 0x401), null);
});

test("unmapped and unnamed symbols are dropped", () => {
  const sorted = sortSymbols([
    { name: "", va: 0x10, size: 4, kind: "func", source: "symtab" },
    { name: "x", va: -1, size: 4, kind: "func", source: "symtab" },
  ]);
  assert.equal(sorted.length, 0);
});
