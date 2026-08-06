import assert from "node:assert/strict";
import { test } from "node:test";

import type { CfgBlock, CfgDoc } from "../src/api.ts";
import {
  HEADER_H, HitGrid, LINE_H, MAX_LINES, PAD_Y, SENTINEL_R,
  blockLines, blockSize, fitTransform, fnOverlaps, insnLine, prepareGraph,
  sentinelId, toGraph, zoomAt,
} from "../src/views/cfgutil.ts";

function mkBlock(over: Partial<CfgBlock> = {}): CfgBlock {
  return {
    id: 0, va: 0x1000, end_va: 0x1010, file_off: 0x200, confidence: "high",
    insns: [
      { va: 0x1000, size: 1, bytes: "55", mnemonic: "push", op: "rbp" },
      { va: 0x1001, size: 3, bytes: "4889e5", mnemonic: "mov", op: "rbp, rsp" },
    ],
    terminator: "ret",
    ...over,
  };
}

/* ------------------------------------------------------- block sizing */

test("insnLine pads the mnemonic into a fixed column", () => {
  assert.equal(insnLine("push", "rbp"), "push    rbp");
  assert.equal(insnLine("ret", ""), "ret");
});

test("blockSize scales with the longest line and instruction count", () => {
  const b = mkBlock();
  const { w, h } = blockSize(b, 7);
  assert.equal(h, HEADER_H + 2 * LINE_H + 2 * PAD_Y);
  // longest line is "mov     rbp, rsp" (16 chars) at 7px/char + padding
  assert.ok(w >= 16 * 7);
});

test("blockLines elides the middle of very long blocks", () => {
  const insns = Array.from({ length: 100 }, (_, i) => ({
    va: 0x1000 + i, size: 1, bytes: "90", mnemonic: "nop", op: "",
  }));
  const lines = blockLines(mkBlock({ insns }));
  assert.equal(lines.length, MAX_LINES);
  const marker = lines.find((l) => l.includes("more"));
  assert.ok(marker, "elision marker present");
  assert.match(marker!, /71 more/);   // 100 - 24 head - 5 tail
});

/* ------------------------------------------------------- graph prep */

function mkDoc(): CfgDoc {
  return {
    function: {
      va: 0x1000, name: "f", size: 64, discovery: "symbol",
      confidence: 1, mode: "x86_64", complete: true,
    },
    blocks: [
      mkBlock({ id: 0, va: 0x1000, end_va: 0x1010, terminator: "jcc" }),
      mkBlock({ id: 1, va: 0x1010, end_va: 0x1020, terminator: "indirect" }),
      mkBlock({ id: 2, va: 0x1020, end_va: 0x1030, terminator: "ret" }),
    ],
    edges: [
      { src: 0, dst: 1, kind: "true" },
      { src: 0, dst: 2, kind: "false" },
    ],
    unresolved: [{ va: 0x101c, reason: "indirect_jump", hint: "jump_table?" }],
    calls_out: [],
  };
}

test("prepareGraph adds a ? sentinel per unresolved block", () => {
  const g = prepareGraph(mkDoc(), 7);
  // 3 blocks + 1 sentinel
  assert.equal(g.nodes.length, 4);
  assert.equal(g.edges.length, 3);
  const sentinel = g.nodes.find((n) => n.id === sentinelId(1));
  assert.ok(sentinel, "sentinel node for block 1 (contains va 0x101c)");
  assert.equal(sentinel!.w, SENTINEL_R * 2);
  const dangling = g.edges.find((e) => e.dst === sentinelId(1));
  assert.equal(dangling?.kind, "indirect_unresolved");
  assert.equal(dangling?.src, "1");
  assert.equal(g.sentinels.get(1)?.reason, "indirect_jump");
});

test("prepareGraph ignores unresolved records outside any block", () => {
  const doc = mkDoc();
  doc.unresolved = [{ va: 0x9999, reason: "indirect_jump", hint: null }];
  const g = prepareGraph(doc, 7);
  assert.equal(g.nodes.length, 3);
  assert.equal(g.sentinels.size, 0);
});

/* -------------------------------------------------------- hit-testing */

test("HitGrid finds the containing node and misses empty space", () => {
  const nodes = [
    { id: "0", x: 0, y: 0, w: 100, h: 50 },
    { id: "1", x: 200, y: 300, w: 80, h: 40 },
  ];
  const grid = new HitGrid(nodes, 400);
  assert.equal(grid.hit(50, 25)?.id, "0");
  assert.equal(grid.hit(99.9, 49.9)?.id, "0");
  assert.equal(grid.hit(240, 320)?.id, "1");
  assert.equal(grid.hit(150, 150), null);
  assert.equal(grid.hit(-5, 10), null);
  // half-open: the far edge is outside
  assert.equal(grid.hit(100, 25), null);
});

test("HitGrid prefers the later (topmost) node on overlap", () => {
  const nodes = [
    { id: "under", x: 0, y: 0, w: 50, h: 50 },
    { id: "over", x: 10, y: 10, w: 50, h: 50 },
  ];
  const grid = new HitGrid(nodes, 100);
  assert.equal(grid.hit(20, 20)?.id, "over");
  assert.equal(grid.hit(5, 5)?.id, "under");
});

/* ---------------------------------------------------------- viewport */

test("fitTransform centres and never zooms past 1", () => {
  // small graph in a big viewport: scale caps at 1, centred
  const t = fitTransform(100, 100, 1000, 500);
  assert.equal(t.scale, 1);
  assert.equal(t.tx, 450);
  assert.equal(t.ty, 200);
  // big graph: scaled to fit the tighter axis
  const t2 = fitTransform(2000, 1000, 500, 500, 25);
  assert.ok(t2.scale <= (500 - 50) / 2000 + 1e-9);
  assert.ok(t2.scale > 0);
});

test("zoomAt keeps the anchor point fixed", () => {
  const t = { scale: 1, tx: 0, ty: 0 };
  const anchor = { x: 100, y: 80 };
  const g0 = toGraph(t, anchor.x, anchor.y);
  const t2 = zoomAt(t, 2, anchor.x, anchor.y);
  const g1 = toGraph(t2, anchor.x, anchor.y);
  assert.ok(Math.abs(g0.x - g1.x) < 1e-9);
  assert.ok(Math.abs(g0.y - g1.y) < 1e-9);
  assert.equal(t2.scale, 2);
});

test("zoomAt clamps to the scale bounds", () => {
  let t = { scale: 1, tx: 0, ty: 0 };
  for (let i = 0; i < 50; i++) t = zoomAt(t, 2, 0, 0);
  assert.equal(t.scale, 4);
  for (let i = 0; i < 80; i++) t = zoomAt(t, 0.5, 0, 0);
  assert.equal(t.scale, 0.02);
});

/* ---------------------------------------------------- list filtering */

test("fnOverlaps is half-open and treats size 0 as 1 byte", () => {
  assert.ok(fnOverlaps({ va: 100, size: 50 }, 120, 130));
  assert.ok(fnOverlaps({ va: 100, size: 50 }, 149, 500));
  assert.ok(!fnOverlaps({ va: 100, size: 50 }, 150, 500));  // touching, not overlapping
  assert.ok(!fnOverlaps({ va: 100, size: 50 }, 0, 100));
  assert.ok(fnOverlaps({ va: 100, size: 0 }, 100, 101));
});
