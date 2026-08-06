/* CFG view helpers — DOM-free on purpose so node --test can exercise the
   graph preparation, hit-testing, and viewport maths without a browser.

   The layout itself runs in a Web Worker (workers/layout.worker.ts); this
   module builds the worker's input from the P5 wire format and indexes the
   worker's output for 60 fps hit-testing. */

import type { CfgBlock, CfgDoc, CfgEdge } from "../api.ts";

/* ------------------------------------------------- block text metrics
   Instruction text is monospace, so sizing needs exactly one measured
   character width — no per-string measurement, no canvas in this module. */

export const FONT_PX = 11;          // instruction font size
export const LINE_H = 15;           // per instruction line
export const HEADER_H = 18;         // block header (address + terminator)
export const PAD_X = 8;             // horizontal padding inside a block
export const PAD_Y = 4;
export const MNEMONIC_COL = 8;      // mnemonic column width in characters
export const MAX_LINES = 30;        // blocks longer than this elide the middle
export const SENTINEL_R = 13;       // "?" sentinel node radius

export interface NodeSize { id: string; w: number; h: number }

/** One rendered instruction line: mnemonic padded into a fixed column. */
export function insnLine(mnemonic: string, op: string): string {
  return op ? mnemonic.padEnd(MNEMONIC_COL) + op : mnemonic;
}

/** Lines a block renders, with long blocks elided in the middle (the
    elision marker counts the hidden instructions). */
export function blockLines(b: CfgBlock): string[] {
  const all = b.insns.map((i) => insnLine(i.mnemonic, i.op));
  if (all.length <= MAX_LINES) return all;
  const head = all.slice(0, MAX_LINES - 6);
  const tail = all.slice(all.length - 5);
  return [...head, `… ${all.length - head.length - tail.length} more …`, ...tail];
}

/** Pixel size of a block given the measured monospace char width. */
export function blockSize(b: CfgBlock, charW: number): { w: number; h: number } {
  const lines = blockLines(b);
  let maxLen = 12;                              // header "0x… · term" minimum
  for (const l of lines) maxLen = Math.max(maxLen, l.length);
  return {
    w: Math.ceil(maxLen * charW) + 2 * PAD_X,
    h: HEADER_H + lines.length * LINE_H + 2 * PAD_Y,
  };
}

/* ------------------------------------------------------ ELK graph prep */

export interface LayoutRequest {
  seq: number;
  nodes: NodeSize[];
  edges: { id: string; src: string; dst: string }[];
}

export interface LaidNode { id: string; x: number; y: number; w: number; h: number }
export interface LaidEdge { id: string; points: { x: number; y: number }[] }

export interface LayoutResult {
  seq: number;
  w: number;
  h: number;
  nodes: LaidNode[];
  edges: LaidEdge[];
  ms: number;
}

/** Sentinel node id for the unresolved-indirect target of block `id`. */
export function sentinelId(blockId: number): string {
  return `q${blockId}`;
}

export interface PreparedGraph {
  nodes: NodeSize[];
  edges: { id: string; src: string; dst: string; kind: CfgEdge["kind"] }[];
  /** block id -> unresolved record (va/reason/hint) for its sentinel */
  sentinels: Map<number, { va: number; reason: string; hint: string | null }>;
}

/** Build the worker input from a CFG document: every block becomes a node,
    every edge an ELK edge, and every block whose terminator is an
    unresolved indirect gets a dangling edge to a small "?" sentinel node —
    the hole in the graph is drawn, never hidden (§5.10). */
export function prepareGraph(doc: CfgDoc, charW: number): PreparedGraph {
  const nodes: NodeSize[] = doc.blocks.map((b) => {
    const { w, h } = blockSize(b, charW);
    return { id: String(b.id), w, h };
  });
  const edges = doc.edges.map((e, i) => ({
    id: `e${i}`, src: String(e.src), dst: String(e.dst), kind: e.kind,
  }));

  const sentinels = new Map<number, { va: number; reason: string; hint: string | null }>();
  const blockAt = (va: number): CfgBlock | null => {
    for (const b of doc.blocks) if (va >= b.va && va < b.end_va) return b;
    return null;
  };
  for (const u of doc.unresolved) {
    const b = blockAt(u.va);
    if (!b || sentinels.has(b.id)) continue;
    sentinels.set(b.id, u);
    const id = sentinelId(b.id);
    nodes.push({ id, w: SENTINEL_R * 2, h: SENTINEL_R * 2 });
    edges.push({
      id: `u${b.id}`, src: String(b.id), dst: id, kind: "indirect_unresolved",
    });
  }
  return { nodes, edges, sentinels };
}

/* --------------------------------------------------------- hit-testing
   A uniform grid over the laid-out graph. Cell size tracks the median
   block so a lookup touches O(1) cells; 500-block graphs hit-test in a
   few comparisons per pointermove (the PLAN's quadtree role, simpler). */

export class HitGrid {
  private cell: number;
  private cols: number;
  private buckets = new Map<number, LaidNode[]>();

  constructor(nodes: LaidNode[], graphW: number) {
    let sum = 0;
    for (const n of nodes) sum += Math.max(n.w, n.h);
    this.cell = Math.max(32, nodes.length ? sum / nodes.length : 64);
    this.cols = Math.max(1, Math.ceil(graphW / this.cell) + 1);
    for (const n of nodes) {
      const c0 = Math.floor(n.x / this.cell), c1 = Math.floor((n.x + n.w) / this.cell);
      const r0 = Math.floor(n.y / this.cell), r1 = Math.floor((n.y + n.h) / this.cell);
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          const key = r * this.cols + c;
          let list = this.buckets.get(key);
          if (!list) this.buckets.set(key, list = []);
          list.push(n);
        }
      }
    }
  }

  /** Topmost node containing graph-space point (x, y), or null. */
  hit(x: number, y: number): LaidNode | null {
    if (x < 0 || y < 0) return null;
    const key = Math.floor(y / this.cell) * this.cols + Math.floor(x / this.cell);
    const list = this.buckets.get(key);
    if (!list) return null;
    for (let i = list.length - 1; i >= 0; i--) {
      const n = list[i];
      if (x >= n.x && x < n.x + n.w && y >= n.y && y < n.y + n.h) return n;
    }
    return null;
  }
}

/* ------------------------------------------------------------ viewport */

export interface Transform { scale: number; tx: number; ty: number }

export const MIN_SCALE = 0.02;
export const MAX_SCALE = 4;
/** Below this zoom, blocks draw as solid rectangles without text (LOD). */
export const TEXT_MIN_SCALE = 0.4;

/** Fit a graph of (w, h) into a viewport of (vw, vh) with a margin,
    centred, never scaling above 1 (text is sized for scale 1). */
export function fitTransform(
  w: number, h: number, vw: number, vh: number, margin = 24,
): Transform {
  const scale = Math.max(
    MIN_SCALE,
    Math.min(1, (vw - 2 * margin) / Math.max(1, w), (vh - 2 * margin) / Math.max(1, h)),
  );
  return {
    scale,
    tx: (vw - w * scale) / 2,
    ty: Math.max(margin, (vh - h * scale) / 2),
  };
}

/** Zoom about a viewport-space anchor point, clamped. */
export function zoomAt(
  t: Transform, factor: number, ax: number, ay: number,
): Transform {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale * factor));
  const k = scale / t.scale;
  return {
    scale,
    tx: ax - (ax - t.tx) * k,
    ty: ay - (ay - t.ty) * k,
  };
}

export function toGraph(t: Transform, vx: number, vy: number): { x: number; y: number } {
  return { x: (vx - t.tx) / t.scale, y: (vy - t.ty) / t.scale };
}

/* -------------------------------------------- function-list filtering */

export interface FnSpan { va: number; size: number }

/** Does a function's VA span overlap the selection's VA range?
    (Half-open ranges; a zero-size function counts as 1 byte.) */
export function fnOverlaps(
  fn: FnSpan, vaStart: number, vaEnd: number,
): boolean {
  const end = fn.va + Math.max(1, fn.size);
  return fn.va < vaEnd && end > vaStart;
}
