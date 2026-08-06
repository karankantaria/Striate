/* Interactive CFG view — Phase 10.

   elkjs layout runs in a Web Worker (layout of a big function takes
   hundreds of ms; the main thread never waits), cached per function VA so
   pan/zoom/reselect never re-lays-out. Rendering is Canvas2D — a
   per-function CFG is 100–2,000 text lines, squarely inside the "Canvas2D,
   not DOM, not WebGL" band — with a uniform-grid hit index and an LOD that
   drops instruction text below 0.4 zoom.

   Uncertainty is drawn, not smoothed over: low-confidence blocks get
   dashed borders, unresolved indirect jumps terminate at a "?" sentinel,
   heuristically-discovered functions are badged in the list, and a packed
   binary shows a banner instead of garbage functions. */

import {
  getCfg,
  type BinaryModel, type CfgBlock, type CfgDoc, type FunctionIndexEntry,
  type FunctionsDoc,
} from "../api.ts";
import { el, html, rawHtml, replace, span } from "../dom.ts";
import { focusTabStop, optionList } from "../listnav.ts";
import {
  fmtHex, fmtSize, regionAtOff, type SelectionStore,
} from "../store.ts";
import { hideTooltip, showTooltip } from "../tooltip.ts";
import {
  FONT_PX, HEADER_H, HitGrid, LINE_H, PAD_X, PAD_Y, SENTINEL_R,
  TEXT_MIN_SCALE, blockLines, fitTransform, fnOverlaps, prepareGraph,
  toGraph, zoomAt,
  type LaidEdge, type LaidNode, type LayoutResult, type PreparedGraph,
  type Transform,
} from "./cfgutil.ts";

interface Loaded {
  doc: CfgDoc;
  prepared: PreparedGraph;
  layout: LayoutResult;
  grid: HitGrid;
  nodeById: Map<string, LaidNode>;
  blockById: Map<number, CfgBlock>;
}

const MONO = `${FONT_PX}px ui-monospace, Consolas, monospace`;
const HEADER_FONT = `10px ui-monospace, Consolas, monospace`;

/* Edge ink per kind — true/false branches use the validated categorical
   aqua/orange (they must never be confusable), unconditional flow is
   neutral, unresolved-indirect is the fuchsia reserved for uncertainty. */
const EDGE_INK: Record<string, string> = {
  true: "#25ae56", false: "#cf4946", uncond: "#a8a492",
  fallthrough: "#a8a492", indirect_unresolved: "#a644a0",
};

const BADGE_TITLES: Record<string, string> = {
  symbol: "from the symbol table — ground truth",
  entry: "the program entry point — ground truth",
  call_target: "target of a direct call (confidence 0.9)",
  ptr_target: "address-sized immediate pointing into code (confidence 0.8)",
  prologue: "matched a prologue signature — a guess (confidence 0.5)",
  gap_sweep: "linear sweep over unclaimed bytes — a guess (confidence 0.2)",
};

export class CfgView {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private host: HTMLElement;
  private listEl: HTMLElement;
  private bannerEl: HTMLElement;
  private statusEl: HTMLElement;
  private searchEl: HTMLInputElement;
  private filterSelEl: HTMLInputElement;
  private store: SelectionStore;

  private id = "";
  private model: BinaryModel | null = null;
  private funcs: FunctionsDoc | null = null;

  private worker: Worker;
  private seq = 0;
  private pending = new Map<number, number>();      // seq -> function va
  private docs = new Map<number, [CfgDoc, PreparedGraph]>();  // staged per seq
  private cache = new Map<number, Loaded>();        // function va -> laid graph
  private current: Loaded | null = null;
  private currentVa: number | null = null;

  private view: Transform = { scale: 1, tx: 0, ty: 0 };
  /** A fit asked for while the pane had no size; the resize observer
      completes it once the pane is on screen. */
  private fitPending = false;
  private hoverId: string | null = null;
  private raf = 0;
  private charW: number;

  // pan state
  private down: { x: number; y: number; tx: number; ty: number } | null = null;
  private panned = false;

  constructor(
    host: HTMLElement,
    controls: {
      list: HTMLElement; banner: HTMLElement; status: HTMLElement;
      search: HTMLElement; filterSel: HTMLElement;
    },
    store: SelectionStore,
  ) {
    this.host = host;
    this.listEl = controls.list;
    this.bannerEl = controls.banner;
    this.statusEl = controls.status;
    this.searchEl = controls.search as HTMLInputElement;
    this.filterSelEl = controls.filterSel as HTMLInputElement;
    this.store = store;

    this.canvas = document.createElement("canvas");
    host.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    this.ctx.font = MONO;
    this.charW = this.ctx.measureText("0").width;

    this.worker = new Worker(
      new URL("../workers/layout.worker.ts", import.meta.url),
      { type: "module" },
    );
    this.worker.onmessage = (ev) => this.onLayout(ev.data);

    new ResizeObserver(() => {
      // a fit deferred because the pane was hidden lands here
      if (this.fitPending) this.fit(); else this.requestDraw();
    }).observe(host);

    store.on("selection", () => {
      if (this.filterSelEl.checked) this.renderList();
      this.requestDraw();
    });
    this.searchEl.addEventListener("input", () => this.renderList());
    this.filterSelEl.addEventListener("change", () => this.renderList());

    this.canvas.addEventListener("pointerdown", (e) => this.onDown(e));
    this.canvas.addEventListener("pointermove", (e) => this.onMove(e));
    this.canvas.addEventListener("pointerup", (e) => this.onUp(e));
    this.canvas.addEventListener("pointerleave", () => {
      this.hoverId = null;
      hideTooltip();
      this.store.setHover(null);
      this.requestDraw();
    });
    this.canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = this.canvas.getBoundingClientRect();
      const factor = Math.exp(-e.deltaY * 0.0015);
      this.view = zoomAt(this.view, factor, e.clientX - r.left, e.clientY - r.top);
      this.requestDraw();
    }, { passive: false });
  }

  /* ------------------------------------------------------------ input */

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.funcs = null;
    this.cache.clear();
    this.pending.clear();
    this.docs.clear();
    this.current = null;
    this.currentVa = null;
    this.bannerEl.hidden = true;
    this.statusEl.textContent = "";
    replace(this.listEl, el("div", { class: "cfg-note" },
                            "waiting for analysis…"));
    this.requestDraw();
  }

  /** Called by the shell once the `functions` artifact is ready. */
  setFunctions(doc: FunctionsDoc): void {
    this.funcs = doc;
    if (doc.packed) {
      this.bannerEl.hidden = false;
      this.bannerEl.textContent =
        "⚠ This binary appears packed — static CFG recovery is not " +
        "meaningful, so heuristic function discovery was suppressed. The " +
        "entropy, histogram, and dot-plot views remain fully valid; supply " +
        "an unpacked dump via `binviz open --raw` for disassembly.";
    } else {
      this.bannerEl.hidden = true;
    }
    this.renderList();
    // auto-open: prefer main, else the entry point, else the first function
    const fns = doc.functions;
    if (!fns.length) return;
    const first =
      fns.find((f) => f.name === "main") ??
      fns.find((f) => f.discovery === "entry") ?? fns[0];
    this.openFunction(first.va);
  }

  /* -------------------------------------------------- function list */

  private visibleFns(): FunctionIndexEntry[] {
    if (!this.funcs) return [];
    let fns = this.funcs.functions;
    const q = this.searchEl.value.trim().toLowerCase();
    if (q) {
      fns = fns.filter((f) =>
        f.name.toLowerCase().includes(q) || f.va.toString(16).includes(q));
    }
    const va = this.store.state.vaRange;
    if (this.filterSelEl.checked && va) {
      fns = fns.filter((f) => fnOverlaps(f, va.start, va.end));
    }
    return fns;
  }

  private renderList(): void {
    if (!this.funcs) return;
    // this list rebuilds when a function is opened, which happens *because*
    // the user pressed Enter on a row — note where focus was so it can go
    // back afterwards rather than falling out to the document body
    const hadFocus = this.listEl.contains(document.activeElement);
    const doc = this.funcs;
    const fns = this.visibleFns();
    const total = doc.functions.length;

    const shownNote = fns.length !== total
      ? `${fns.length} of ${total} functions` : `${total} functions`;
    const unclaimed = doc.unclaimed_blocks.length;

    // `f.name` is the raw symbol name — one of S2's two live sinks. As a
    // text node it cannot be markup regardless of what the binary says.
    replace(this.listEl,
      !total && el("div", { class: "cfg-note" }, doc.packed
        ? "No functions recovered — expected for a packed binary (see banner)."
        : "No functions recovered."),
      !!total && !fns.length && el("div", { class: "cfg-note" },
        `0 of ${total} functions match the current filter.`),

      ...fns.map((f) => el("div", {
        class: `fn-row${f.va === this.currentVa ? " active" : ""}`,
        "data-va": f.va,
      },
        el("span", {
          class: "fn-name",
          title: `${f.name} · ${f.insns} insns`
            + (f.unresolved ? ` · ${f.unresolved} unresolved` : ""),
        }, f.name),
        el("span", {
          class: `fn-badge b-${f.discovery}`,
          title: BADGE_TITLES[f.discovery] ?? f.discovery,
        }, f.discovery),
        !f.complete && el("span", {
          class: "fn-badge b-incomplete",
          title: "decoding hit a failure or the instruction cap",
        }, "partial"),
        span("fn-meta", `${f.blocks}b · ${fmtSize(f.size)}`))),

      el("div", { class: "cfg-note" }, shownNote
        + (unclaimed ? ` · ${unclaimed} unclaimed low-confidence blocks` : "")));

    // `.active` is stamped during the build above, so aria-selected and the
    // list's single tab stop follow from it without a second pass.
    optionList(this.listEl, "Functions",
      [...this.listEl.querySelectorAll<HTMLElement>(".fn-row")],
      (row) => void this.openFunction(Number(row.dataset.va)));
    if (hadFocus) focusTabStop(this.listEl);
  }

  /* ------------------------------------------------- function loading */

  async openFunction(va: number): Promise<void> {
    this.currentVa = va;
    this.renderList();
    const cached = this.cache.get(va);
    if (cached) {
      this.show(va, cached);
      return;
    }
    this.statusEl.textContent = "loading…";
    let doc: CfgDoc;
    try {
      doc = await getCfg(this.id, va);
    } catch (e) {
      this.statusEl.textContent = String((e as Error).message ?? e);
      return;
    }
    if (this.currentVa !== va) return;      // user clicked away meanwhile
    const prepared = prepareGraph(doc, this.charW);
    const seq = ++this.seq;
    this.pending.set(seq, va);
    this.docs.set(seq, [doc, prepared]);
    this.statusEl.textContent = `layout: ${doc.blocks.length} blocks…`;
    this.worker.postMessage({
      seq,
      nodes: prepared.nodes,
      edges: prepared.edges.map(({ id, src, dst }) => ({ id, src, dst })),
    });
  }

  private onLayout(res: LayoutResult & { error?: string }): void {
    const va = this.pending.get(res.seq);
    const staged = this.docs.get(res.seq);
    this.pending.delete(res.seq);
    this.docs.delete(res.seq);
    if (va === undefined || !staged) return;
    if (res.error) {
      this.statusEl.textContent = `layout failed: ${res.error}`;
      return;
    }
    const [doc, prepared] = staged;
    const loaded: Loaded = {
      doc, prepared, layout: res,
      grid: new HitGrid(res.nodes, res.w),
      nodeById: new Map(res.nodes.map((n) => [n.id, n])),
      blockById: new Map(doc.blocks.map((b) => [b.id, b])),
    };
    this.cache.set(va, loaded);
    if (this.currentVa === va) this.show(va, loaded);
  }

  private show(va: number, loaded: Loaded): void {
    this.current = loaded;
    this.currentVa = va;
    const f = loaded.doc.function;
    this.statusEl.textContent =
      `${f.name} · ${loaded.doc.blocks.length} blocks · ` +
      `${loaded.doc.edges.length} edges` +
      (loaded.doc.unresolved.length
        ? ` · ${loaded.doc.unresolved.length} unresolved` : "") +
      ` · layout ${loaded.layout.ms.toFixed(0)} ms`;
    this.fit();
  }

  fit(): void {
    if (!this.current) return;
    const w = this.host.clientWidth, h = this.host.clientHeight;
    // A layout can now finish while this pane is hidden — the Code
    // workspace is one tab among five (§3.4) — and fitting a graph to a
    // 0x0 viewport produces a transform that draws it as a one-pixel
    // sliver at the left edge, which survives every later redraw because
    // nothing recomputes the fit. Defer instead, and let the resize
    // observer finish the job when the pane is actually on screen.
    if (w < 2 || h < 2) { this.fitPending = true; return; }
    this.fitPending = false;
    this.view = fitTransform(this.current.layout.w, this.current.layout.h, w, h);
    this.requestDraw();
  }

  /* ------------------------------------------------------ interaction */

  private onDown(e: PointerEvent): void {
    this.canvas.setPointerCapture(e.pointerId);
    const r = this.canvas.getBoundingClientRect();
    this.down = {
      x: e.clientX - r.left, y: e.clientY - r.top,
      tx: this.view.tx, ty: this.view.ty,
    };
    this.panned = false;
  }

  private onMove(e: PointerEvent): void {
    const r = this.canvas.getBoundingClientRect();
    const vx = e.clientX - r.left, vy = e.clientY - r.top;
    if (this.down) {
      const dx = vx - this.down.x, dy = vy - this.down.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) this.panned = true;
      if (this.panned) {
        this.view = { ...this.view, tx: this.down.tx + dx, ty: this.down.ty + dy };
        hideTooltip();
        this.requestDraw();
        return;
      }
    }
    this.updateHover(vx, vy, e.clientX, e.clientY);
  }

  private onUp(e: PointerEvent): void {
    this.canvas.releasePointerCapture(e.pointerId);
    const wasPan = this.panned;
    this.down = null;
    this.panned = false;
    if (wasPan || !this.current) return;
    const r = this.canvas.getBoundingClientRect();
    const p = toGraph(this.view, e.clientX - r.left, e.clientY - r.top);
    const node = this.current.grid.hit(p.x, p.y);
    if (!node) return;
    const block = this.current.blockById.get(Number(node.id));
    if (!block || block.file_off < 0) return;
    // this is the linkage moment: a CFG block becomes a file range and
    // every other view follows
    this.store.setSelection({
      start: block.file_off,
      end: block.file_off + (block.end_va - block.va),
    });
    this.store.setCaret(block.file_off);
  }

  private updateHover(
    vx: number, vy: number, clientX: number, clientY: number,
  ): void {
    if (!this.current) return;
    const p = toGraph(this.view, vx, vy);
    const node = this.current.grid.hit(p.x, p.y);
    const id = node?.id ?? null;
    if (id !== this.hoverId) {
      this.hoverId = id;
      this.requestDraw();
    }
    if (!node) {
      hideTooltip();
      this.store.setHover(null);
      return;
    }
    if (node.id.startsWith("q")) {
      const blockId = Number(node.id.slice(1));
      const u = this.current.prepared.sentinels.get(blockId);
      if (u) {
        showTooltip(clientX, clientY, html`<b>unresolved indirect jump</b><br><span class="t2">at ${fmtHex(u.va)} · ${u.reason}${u.hint ? ` · ${u.hint}` : ""}</span><br><span class="t2">targets unknown — this edge is a hole in the graph, not an absence of flow</span>`);
      }
      return;
    }
    const b = this.current.blockById.get(Number(node.id));
    if (!b) return;
    const region = this.model && b.file_off >= 0
      ? regionAtOff(this.model.regions, b.file_off) : null;
    showTooltip(clientX, clientY, html`<b>${fmtHex(b.va)}–${fmtHex(b.end_va)}</b> · ${b.insns.length} insns<br><span class="t2">file ${b.file_off >= 0 ? fmtHex(b.file_off) : "–"}${region ? ` (${region.name})` : ""} · ends with ${b.terminator} · confidence ${b.confidence}</span>${b.confidence === "low"
  ? rawHtml(`<br><span class="t2">low confidence: from a linear sweep, not `
            + `proven reachable</span>`) : ""}`);
    if (b.file_off >= 0) this.store.setHover(b.file_off);
  }

  /* -------------------------------------------------------- rendering */

  private requestDraw(): void {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      this.draw();
    });
  }

  private css(name: string): string {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  }

  private draw(): void {
    // pull the size from the DOM at draw time (handover gotcha #3)
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (w < 2 || h < 2) return;
    const dpr = window.devicePixelRatio || 1;
    if (this.canvas.width !== Math.round(w * dpr) ||
        this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
      this.canvas.style.width = w + "px";
      this.canvas.style.height = h + "px";
    }
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const cur = this.current;
    if (!cur) return;

    const t = this.view;
    ctx.setTransform(dpr * t.scale, 0, 0, dpr * t.scale, dpr * t.tx, dpr * t.ty);
    const showText = t.scale >= TEXT_MIN_SCALE;

    const ink = this.css("--ink");
    const ink2 = this.css("--ink-2");
    const muted = this.css("--muted");
    const surface = this.css("--page");
    const border = this.css("--baseline");
    const accent = this.css("--accent");
    const selFill = this.css("--select-fill");
    const edgeInk = EDGE_INK;

    // ---- edges under blocks
    const kindOf = new Map(cur.prepared.edges.map((e) => [e.id, e.kind]));
    ctx.lineWidth = 1.4 / t.scale > 1.4 ? 1.4 / t.scale : 1.4;
    for (const e of cur.layout.edges) {
      const kind = kindOf.get(e.id) ?? "uncond";
      if (e.points.length < 2) continue;
      ctx.strokeStyle = edgeInk[kind] ?? ink2;
      ctx.setLineDash(kind === "indirect_unresolved" ? [5, 4] : []);
      ctx.beginPath();
      ctx.moveTo(e.points[0].x, e.points[0].y);
      for (let i = 1; i < e.points.length; i++) {
        ctx.lineTo(e.points[i].x, e.points[i].y);
      }
      ctx.stroke();
      this.arrowhead(ctx, e);
    }
    ctx.setLineDash([]);

    // ---- blocks
    const sel = this.store.state.offsetRange;
    for (const n of cur.layout.nodes) {
      if (n.id.startsWith("q")) {
        this.drawSentinel(ctx, n, edgeInk.indirect_unresolved, surface, showText);
        continue;
      }
      const b = cur.blockById.get(Number(n.id));
      if (!b) continue;
      const isEntry = b.va === cur.doc.function.va;
      const low = b.confidence === "low";
      const inSel = sel !== null && b.file_off >= 0 &&
        b.file_off < sel.end && b.file_off + (b.end_va - b.va) > sel.start;

      ctx.fillStyle = surface;
      ctx.beginPath();
      ctx.roundRect(n.x, n.y, n.w, n.h, 4);
      ctx.fill();
      if (inSel) { ctx.fillStyle = selFill; ctx.fill(); }
      ctx.setLineDash(low ? [4, 3] : []);
      ctx.strokeStyle = isEntry ? accent
        : this.hoverId === n.id ? accent : border;
      ctx.lineWidth = isEntry || this.hoverId === n.id ? 2 : 1;
      ctx.stroke();
      ctx.setLineDash([]);

      if (!showText) {
        // LOD: solid rectangles carry only the confidence signal
        if (low) {
          ctx.fillStyle = muted;
          ctx.globalAlpha = 0.25;
          ctx.fillRect(n.x, n.y, n.w, n.h);
          ctx.globalAlpha = 1;
        }
        continue;
      }

      // header: address + terminator
      ctx.font = HEADER_FONT;
      ctx.fillStyle = muted;
      ctx.textBaseline = "middle";
      ctx.fillText(fmtHex(b.va), n.x + PAD_X, n.y + HEADER_H / 2 + 1);
      const term = b.terminator;
      const termInk = term === "indirect" ? edgeInk.indirect_unresolved
        : term === "invalid" ? "#d03b3b" : muted;
      ctx.fillStyle = termInk;
      const tw = ctx.measureText(term).width;
      ctx.fillText(term, n.x + n.w - PAD_X - tw, n.y + HEADER_H / 2 + 1);
      ctx.strokeStyle = border;
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(n.x, n.y + HEADER_H);
      ctx.lineTo(n.x + n.w, n.y + HEADER_H);
      ctx.stroke();

      // instructions
      ctx.font = MONO;
      ctx.fillStyle = ink;
      const lines = blockLines(b);
      let y = n.y + HEADER_H + PAD_Y + LINE_H / 2;
      for (const line of lines) {
        ctx.fillText(line, n.x + PAD_X, y);
        y += LINE_H;
      }
    }
  }

  private arrowhead(ctx: CanvasRenderingContext2D, e: LaidEdge): void {
    const n = e.points.length;
    const p1 = e.points[n - 1], p0 = e.points[n - 2];
    const dx = p1.x - p0.x, dy = p1.y - p0.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    const s = 5;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p1.x - ux * s - uy * s * 0.6, p1.y - uy * s + ux * s * 0.6);
    ctx.lineTo(p1.x - ux * s + uy * s * 0.6, p1.y - uy * s - ux * s * 0.6);
    ctx.closePath();
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fill();
  }

  private drawSentinel(
    ctx: CanvasRenderingContext2D, n: LaidNode,
    color: string, surface: string, showText: boolean,
  ): void {
    const cx = n.x + n.w / 2, cy = n.y + n.h / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, SENTINEL_R, 0, Math.PI * 2);
    ctx.fillStyle = surface;
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = this.hoverId === n.id ? 2 : 1.4;
    ctx.stroke();
    ctx.setLineDash([]);
    if (showText) {
      ctx.fillStyle = color;
      ctx.font = `bold ${FONT_PX + 2}px ui-monospace, Consolas, monospace`;
      ctx.textBaseline = "middle";
      const tw = ctx.measureText("?").width;
      ctx.fillText("?", cx - tw / 2, cy + 1);
    }
  }
}
