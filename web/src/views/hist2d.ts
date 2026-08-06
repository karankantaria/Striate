/* 2D histogram (bigram) view — 256×256 cells, axes are byte values:
   x = first element bin (b[i]), y = second element bin (b[i+1]), origin
   top-left, matching the server's /hist n=2 layout [first*256 + second].

   Counts ship raw; the display transform (log1p/rank/sqrt/linear) is
   applied client-side so switching modes never refetches. The dtype
   selector is wired to the shared SelectionStore.dtype, and the view
   recomputes for the current selection.

   Brush-to-locate: dragging a cell rect POSTs /hist/locate and publishes
   the offset density through SelectionStore.setLocate, which the overall
   and plot views render as a highlight — "where in the file is this
   structure?". Click without dragging clears the brush. */

import {
  getHist2, postLocate,
  type BinaryModel, type Hist2Meta, type QuantiseMeta,
} from "../api.ts";
import { applyLut, VIRIDIS, type Theme } from "../colormap.ts";
import { html, joinHtml, rawHtml, type SafeHtml } from "../dom.ts";
import { clearPaneError, paneError } from "../panestatus.ts";
import { fmtHex, type OffsetRange, type SelectionStore } from "../store.ts";
import { toDisplay, type DisplayMode } from "../transforms.ts";
import { hideTooltip, showTooltip } from "../tooltip.ts";

const ML = 36;                     // left margin: y tick labels
const MB = 16;                     // bottom margin: x tick labels
const MT = 4;
const MR = 4;
const TICKS = [0x00, 0x20, 0x41, 0x7f, 0xff];
const LOCATE_BINS = 2048;

interface Cell { x: number; y: number }  // x = first bin, y = second bin

export class Hist2DView {
  private host: HTMLElement;
  private canvas: HTMLCanvasElement;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  display: DisplayMode = "log1p";
  private counts: Uint32Array | null = null;
  private meta: Hist2Meta | null = null;
  private scratch: HTMLCanvasElement;   // 256×256 colored bigram
  private brush: { a: Cell; b: Cell } | null = null;
  private dragging = false;
  private fetchSeq = 0;
  private locateSeq = 0;
  private refetchTimer: number | undefined;
  private ro: ResizeObserver;
  private statusEl: HTMLElement | null;
  // plot rect (CSS px), set by draw()
  private px = 0; private py = 0; private side = 0;

  constructor(
    host: HTMLElement, store: SelectionStore, _theme: Theme,
    statusEl: HTMLElement | null = null,
  ) {
    this.host = host;
    this.store = store;
    this.statusEl = statusEl;
    this.canvas = document.createElement("canvas");
    host.appendChild(this.canvas);
    this.scratch = document.createElement("canvas");
    this.scratch.width = this.scratch.height = 256;
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(host);

    store.on("selection", () => this.debouncedRefetch());
    store.on("dtype", () => this.refetch());
    store.on("theme", () => this.draw());
    store.on("locate", () => this.draw());   // cleared elsewhere -> drop brush ring

    this.canvas.addEventListener("pointerdown", (e) => {
      const c = this.cellAt(e.clientX, e.clientY);
      if (!c) return;
      this.canvas.setPointerCapture(e.pointerId);
      this.dragging = true;
      this.brush = { a: c, b: c };
      hideTooltip();
      this.draw();
    });
    this.canvas.addEventListener("pointermove", (e) => {
      const c = this.cellAt(e.clientX, e.clientY);
      if (this.dragging && (e.buttons & 1)) {
        if (c) { this.brush!.b = c; this.draw(); }
      } else if (c) {
        this.tooltip(e.clientX, e.clientY, c);
      } else {
        hideTooltip();
      }
    });
    this.canvas.addEventListener("pointerup", () => {
      if (!this.dragging) return;
      this.dragging = false;
      const b = this.brush;
      if (b && b.a.x === b.b.x && b.a.y === b.b.y) {
        this.clearBrush();               // click = clear
      } else {
        this.locate();
      }
    });
    this.canvas.addEventListener("pointerleave", () => hideTooltip());
  }

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.counts = null;
    this.brush = null;
    this.refetch();
  }

  setDisplay(mode: DisplayMode): void {
    this.display = mode;
    this.renderScratch();
    this.draw();
  }

  clearBrush(): void {
    this.brush = null;
    this.store.setLocate(null);
    this.draw();
  }

  private range(): OffsetRange {
    return this.store.state.offsetRange
      ?? { start: 0, end: this.model?.size ?? 0 };
  }

  private debouncedRefetch(): void {
    window.clearTimeout(this.refetchTimer);
    this.refetchTimer = window.setTimeout(() => this.refetch(), 150);
  }

  async refetch(): Promise<void> {
    if (!this.id || !this.model) return;
    const seq = ++this.fetchSeq;
    const r = this.range();
    try {
      const { counts, meta } =
        await getHist2(this.id, this.store.state.dtype, r.start, r.end);
      if (seq !== this.fetchSeq) return;
      this.counts = counts;
      this.meta = meta;
      clearPaneError(this.host);
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 409 || status === 410) {
        window.clearTimeout(this.refetchTimer);
        this.refetchTimer = window.setTimeout(() => this.refetch(), 700);
      } else {
        paneError(this.host, "could not load the bigram", e,
                  () => this.refetch());
      }
      return;
    }
    this.renderScratch();
    this.draw();
    this.renderStatus();
    if (this.brush) this.locate();     // brushed cells over the new range
  }

  private async locate(): Promise<void> {
    const b = this.brush;
    if (!b || !this.id) return;
    const seq = ++this.locateSeq;
    const r = this.range();
    const rect = {
      first0: Math.min(b.a.x, b.b.x), first1: Math.max(b.a.x, b.b.x),
      second0: Math.min(b.a.y, b.b.y), second1: Math.max(b.a.y, b.b.y),
    };
    try {
      const { density, meta } = await postLocate(this.id, rect, {
        dtype: this.store.state.dtype, start: r.start, end: r.end,
        n: LOCATE_BINS,
      });
      if (seq !== this.locateSeq || this.brush !== b) return;
      let max = 0;
      for (let i = 0; i < density.length; i++) {
        if (density[i] > max) max = density[i];
      }
      this.store.setLocate({
        density, max, start: meta.start, end: meta.end,
        matches: meta.matches,
        label: `b[i] ${fmtHex(rect.first0)}–${fmtHex(rect.first1)} × ` +
               `b[i+1] ${fmtHex(rect.second0)}–${fmtHex(rect.second1)}`,
      });
      this.renderStatus();
    } catch (e) {
      paneError(this.host, "brush-to-locate failed", e,
                () => this.locate());
    }
  }

  /* ---------------------------------------------------------- painting */

  /** Recolor the 256×256 scratch from counts through transform + LUT. */
  private renderScratch(): void {
    if (!this.counts) return;
    const disp = toDisplay(this.counts, this.display);
    // transpose: counts are [first*256+second]; pixel (x=first, y=second)
    const img = new Uint8Array(65536);
    for (let first = 0; first < 256; first++) {
      const row = first * 256;
      for (let second = 0; second < 256; second++) {
        img[second * 256 + first] = disp[row + second];
      }
    }
    const rgba = applyLut(img, VIRIDIS);
    this.scratch.getContext("2d")!
      .putImageData(new ImageData(rgba, 256, 256), 0, 0);
  }

  draw(): void {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (w === 0 || h === 0) return;
    this.canvas.width = w * devicePixelRatio;
    this.canvas.height = h * devicePixelRatio;
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
    const ctx = this.canvas.getContext("2d")!;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, w, h);

    this.side = Math.max(32, Math.min(w - ML - MR, h - MT - MB));
    this.px = ML + Math.floor((w - ML - MR - this.side) / 2);
    this.py = MT;

    const css = getComputedStyle(document.documentElement);
    const muted = css.getPropertyValue("--muted").trim();
    const grid = css.getPropertyValue("--grid").trim();
    const edge = css.getPropertyValue("--select-edge").trim();
    const fill = css.getPropertyValue("--select-fill").trim();

    ctx.strokeStyle = grid;
    ctx.lineWidth = 1;
    ctx.strokeRect(this.px - 0.5, this.py - 0.5, this.side + 1, this.side + 1);
    if (this.counts) {
      ctx.imageSmoothingEnabled = false;   // image-rendering: pixelated
      ctx.drawImage(this.scratch, this.px, this.py, this.side, this.side);
    }

    // axis ticks at meaningful byte values
    ctx.fillStyle = muted;
    ctx.font = "9px ui-monospace, monospace";
    const cell = this.side / 256;
    for (const t of TICKS) {
      const cx = this.px + (t + 0.5) * cell;
      const cy = this.py + (t + 0.5) * cell;
      ctx.textAlign = "center";
      ctx.fillText(t.toString(16).padStart(2, "0"), cx, this.py + this.side + 11);
      ctx.textAlign = "right";
      ctx.fillText(t.toString(16).padStart(2, "0"), this.px - 4, cy + 3);
      ctx.fillRect(cx - 0.5, this.py + this.side, 1, 3);
      ctx.fillRect(this.px - 3, cy - 0.5, 3, 1);
    }
    // axis names
    ctx.textAlign = "left";
    ctx.font = "9px system-ui, sans-serif";
    ctx.fillText("b[i] →", this.px + 2, this.py + this.side + 11);
    ctx.save();
    ctx.translate(this.px - 24, this.py + 30);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("← b[i+1]", 0, 0);
    ctx.restore();

    // brush rect
    if (this.brush) {
      const { a, b } = this.brush;
      const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x);
      const y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
      const rx = this.px + x0 * cell, ry = this.py + y0 * cell;
      const rw = (x1 - x0 + 1) * cell, rh = (y1 - y0 + 1) * cell;
      ctx.fillStyle = fill;
      ctx.fillRect(rx, ry, rw, rh);
      ctx.strokeStyle = edge;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(rx, ry, rw, rh);
    }
  }

  /* ------------------------------------------------------- interaction */

  private cellAt(clientX: number, clientY: number): Cell | null {
    const r = this.canvas.getBoundingClientRect();
    const x = clientX - r.left - this.px;
    const y = clientY - r.top - this.py;
    if (this.side <= 0 || x < 0 || y < 0 || x >= this.side || y >= this.side) {
      return null;
    }
    return {
      x: Math.min(255, Math.floor((x / this.side) * 256)),
      y: Math.min(255, Math.floor((y / this.side) * 256)),
    };
  }

  private tooltip(clientX: number, clientY: number, c: Cell): void {
    if (!this.counts) return;
    const count = this.counts[c.x * 256 + c.y];
    const q = this.meta?.quantise as QuantiseMeta | undefined;
    const rows: SafeHtml[] = [];
    if (!q || q.method === "identity") {
      rows.push(html`<b>${fmtHex(c.x)} → ${fmtHex(c.y)}</b>\
${printable(c.x)}${printable(c.y)}`);
    } else {
      rows.push(html`<b>bin ${fmtHex(c.x)} → bin ${fmtHex(c.y)}</b>`);
      rows.push(html`<span class="t2">${binRange(c.x, q)} → ${binRange(c.y, q)}</span>`);
    }
    rows.push(html`<span class="t2">count</span> ${count.toLocaleString()}`);
    showTooltip(clientX, clientY, joinHtml(rows, "<br>"));
  }

  private renderStatus(): void {
    if (!this.statusEl) return;
    const parts: string[] = [];
    if (this.counts && this.meta) {
      let nz = 0, max = 0;
      for (let i = 0; i < this.counts.length; i++) {
        const c = this.counts[i];
        if (c > 0) nz++;
        if (c > max) max = c;
      }
      parts.push(`${nz.toLocaleString()} cells · max ${max.toLocaleString()}`);
      const q = this.meta.quantise as QuantiseMeta;
      if (q.method && q.method !== "identity") {
        parts.push(`${q.method} quantise ${fmtQ(q.lo)}–${fmtQ(q.hi)}`);
      }
    }
    const loc = this.store.state.locate;
    if (loc) {
      parts.push(`${loc.matches.toLocaleString()} matches highlighted`);
    }
    this.statusEl.textContent = parts.join(" · ");
  }
}

function printable(v: number): SafeHtml {
  return v >= 0x20 && v < 0x7f
    ? html` <span class="t2">'${String.fromCharCode(v)}'</span>`
    : rawHtml("");
}

function binRange(bin: number, q: QuantiseMeta): string {
  const step = (q.hi - q.lo) / 256;
  return `[${fmtQ(q.lo + bin * step)}, ${fmtQ(q.lo + (bin + 1) * step)})`;
}

function fmtQ(v: number): string {
  if (Number.isInteger(v)) return v.toLocaleString();
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3);
}
