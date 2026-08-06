/* CFG layout worker — ELK `layered` off the main thread (PLAN §P10).

   A 500-node layout takes hundreds of milliseconds; run on the main
   thread it would freeze the UI on every function click. The protocol is
   dumb on purpose: {seq, nodes, edges} in, {seq, positions, bends} out.
   The view caches results by function VA, so each function lays out once.

   ELK over dagre: CFGs are layered DAGs plus back-edges (every loop) and
   self-loops — the main event, not an edge case. ELK's layered algorithm
   runs the full Sugiyama pipeline with real cycle-breaking and orthogonal
   edge routing. */

// elk-api + an explicit worker factory, NOT elk.bundled.js: the bundled
// build's internal fake-Worker shim does not survive Vite's CJS
// prebundling ("_Worker is not a constructor"). The ?worker import hands
// Vite the GWT engine script to bundle as a real nested worker.
import ELK from "elkjs/lib/elk-api.js";
import ElkEngineWorker from "elkjs/lib/elk-worker.min.js?worker";
import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";
import type { LayoutRequest, LayoutResult } from "../views/cfgutil.ts";

const elk = new ELK({
  workerFactory: () => new ElkEngineWorker() as unknown as Worker,
});

const LAYOUT_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.layering.strategy": "NETWORK_SIMPLEX",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
  "elk.layered.spacing.nodeNodeBetweenLayers": "28",
  "elk.spacing.nodeNode": "22",
  "elk.spacing.edgeNode": "12",
  "elk.spacing.edgeEdge": "10",
  "elk.layered.mergeEdges": "false",
};

const ctx = self as unknown as {
  postMessage(msg: unknown): void;
  onmessage: ((ev: MessageEvent<LayoutRequest>) => void) | null;
};

ctx.onmessage = async (ev) => {
  const { seq, nodes, edges } = ev.data;
  const t0 = performance.now();
  const graph: ElkNode = {
    id: "root",
    layoutOptions: LAYOUT_OPTIONS,
    children: nodes.map((n) => ({ id: n.id, width: n.w, height: n.h })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.src], targets: [e.dst] })),
  };
  try {
    const laid = await elk.layout(graph);
    const out: LayoutResult = {
      seq,
      w: laid.width ?? 0,
      h: laid.height ?? 0,
      nodes: (laid.children ?? []).map((c) => ({
        id: c.id, x: c.x ?? 0, y: c.y ?? 0, w: c.width ?? 0, h: c.height ?? 0,
      })),
      edges: ((laid.edges ?? []) as ElkExtendedEdge[]).map((e) => {
        const pts: { x: number; y: number }[] = [];
        for (const s of e.sections ?? []) {
          pts.push(s.startPoint, ...(s.bendPoints ?? []), s.endPoint);
        }
        return { id: e.id, points: pts };
      }),
      ms: performance.now() - t0,
    };
    ctx.postMessage(out);
  } catch (err) {
    ctx.postMessage({ seq, error: String(err) });
  }
};
