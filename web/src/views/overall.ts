/* Overall + Zoomed view — one component, two bindings (the reference's
   overall_view used twice). A full-file instance owns the m1/m2 drag-select
   marker state machine; a selection-bound instance renders whatever range
   the SelectionStore holds.

   Layouts: linear (row-major) and hilbert (locality-preserving). Modes:
   byte class (categorical, mode-aggregated server-side), entropy signal
   (linear only — the backend's hilbert surface has no signal mode), raw
   byte value. Pixel->offset for hilbert goes through the xy2d port so a
   click lands on the same offset the server used. */

import { getSurface, type BinaryModel, type ScalarRaster } from "../api.ts";
import { RasterCanvas, type CellPointerEvent } from "../canvas/raster.ts";
import {
  BYTE_CLASS_COLORS, BYTE_CLASS_NAMES, byteClassLut, GRAY, LOCATE_RGB,
  VIRIDIS, type Lut, type Theme,
} from "../colormap.ts";
import { html, joinHtml, replace, span, type SafeHtml } from "../dom.ts";
import { d2xy, offsetAtXY } from "../hilbert.ts";
import { clearPaneError, paneError } from "../panestatus.ts";
import {
  fmtHex, offToVa, regionAtOff,
  type OffsetRange, type SelectionStore,
} from "../store.ts";
import { hideTooltip, showTooltip } from "../tooltip.ts";

export type OverallLayout = "linear" | "hilbert";
export type OverallMode = "byteclass" | "signal" | "value";

type DragState =
  | { kind: "idle" }
  | { kind: "new"; anchor: number }
  | { kind: "m1" }
  | { kind: "m2" }
  | { kind: "band"; grabOff: number; origin: OffsetRange };

const EDGE_HIT_PX = 5;

export class OverallView {
  private view: RasterCanvas;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  private binding: "file" | "selection";
  layout: OverallLayout = "linear";
  mode: OverallMode = "byteclass";
  private theme: Theme;
  // fetched raster extent
  private start = 0;
  private end = 0;
  private nCells = 0;
  private order = 0;                 // hilbert only
  private sigMeta: { lo: number; hi: number; unit: string } | null = null;
  private drag: DragState = { kind: "idle" };
  private fetchSeq = 0;
  private refetchTimer: number | undefined;
  private legendEl: HTMLElement | null;

  constructor(
    host: HTMLElement, store: SelectionStore, theme: Theme,
    binding: "file" | "selection", legendEl: HTMLElement | null = null,
  ) {
    this.store = store;
    this.theme = theme;
    this.binding = binding;
    this.legendEl = legendEl;
    this.view = new RasterCanvas(host, {
      fit: "stretch",
      onResize: () => this.refetch(),
      onPointer: (ev) => this.pointer(ev),
      drawOverlay: (ctx, v) => this.overlay(ctx, v),
    });
    store.on("selection", () => {
      if (this.binding === "selection") this.debouncedRefetch();
      this.view.redrawOverlay();
    });
    store.on("hover", () => this.view.redrawOverlay());
    store.on("locate", () => this.view.redrawOverlay());
    store.on("theme", (t) => { this.theme = t; this.applyLutForMode(); this.renderLegend(); });
  }

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.refetch();
  }

  setLayout(layout: OverallLayout): void {
    if (layout === "hilbert" && this.mode === "signal") this.mode = "byteclass";
    this.layout = layout;
    this.refetch();
  }

  setMode(mode: OverallMode): void {
    this.mode = mode;
    this.refetch();
    this.renderLegend();
  }

  private range(): OffsetRange | null {
    if (!this.model) return null;
    if (this.binding === "file") return { start: 0, end: this.model.size };
    return this.store.state.offsetRange;
  }

  private debouncedRefetch(): void {
    window.clearTimeout(this.refetchTimer);
    this.refetchTimer = window.setTimeout(() => this.refetch(), 90);
  }

  async refetch(): Promise<void> {
    this.view.sync();
    const r = this.range();
    if (!this.id || !r || this.view.cssW === 0) return;
    const seq = ++this.fetchSeq;
    const params: Record<string, string | number> = {
      start: r.start, end: r.end,
      w: Math.max(16, this.view.cssW), h: Math.max(16, this.view.cssH),
      mode: this.mode,
    };
    if (this.mode === "signal") {
      params.signal = "entropy_4096";
      params.reduce = "max";          // spikes must survive — never mean
    }
    let raster: ScalarRaster;
    try {
      raster = await getSurface(this.id, this.layout, params);
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 409 || status === 410) {
        // analysis still settling — retry until the artifact exists
        window.clearTimeout(this.refetchTimer);
        this.refetchTimer = window.setTimeout(() => this.refetch(), 700);
      } else {
        paneError(this.view.host, "could not render this surface", e,
                  () => this.refetch());
      }
      return;
    }
    if (seq !== this.fetchSeq) return;   // superseded
    clearPaneError(this.view.host);
    const m = raster.meta.meta as Record<string, unknown>;
    this.start = (m.start as number) ?? r.start;
    this.end = (m.end as number) ?? r.end;
    this.nCells = raster.w * raster.h;
    if (this.layout === "hilbert") this.order = (m.order as number) ?? 0;
    this.sigMeta = this.mode === "signal"
      ? {
          lo: (m.value_range as number[])[0], hi: (m.value_range as number[])[1],
          unit: (m.unit as string) ?? "",
        }
      : null;
    this.view.setRaster(raster.pixels, raster.w, raster.h);
    this.applyLutForMode();
    this.renderLegend();
  }

  private applyLutForMode(): void {
    const lut: Lut = this.mode === "byteclass" ? byteClassLut(this.theme)
      : this.mode === "signal" ? VIRIDIS
      : GRAY;
    this.view.setLut(lut);
  }

  /* ------------------------------------------------- offset mapping */

  private nbytes(): number { return Math.max(1, this.end - this.start); }

  offsetAtCell(cellX: number, cellY: number): number {
    if (this.layout === "hilbert") {
      return offsetAtXY(this.order, cellX, cellY, this.start, this.nbytes());
    }
    const cell = cellY * this.view.w + cellX;
    return this.start + Math.floor((cell * this.nbytes()) / this.nCells);
  }

  private cellOfOffset(off: number): number {
    const c = Math.floor(((off - this.start) * this.nCells) / this.nbytes());
    return Math.max(0, Math.min(this.nCells - 1, c));
  }

  /* -------------------------------------------------- interactions */

  private pointer(ev: CellPointerEvent): void {
    if (!this.model) return;
    const off = ev.inside ? this.offsetAtCell(ev.cellX, ev.cellY) : null;

    if (ev.type === "leave") {
      this.store.setHover(null);
      hideTooltip();
      return;
    }
    if (ev.type === "move" && this.drag.kind === "idle") {
      this.store.setHover(off);
      if (off !== null) this.tooltip(ev, off);
      else hideTooltip();
    }
    if (this.binding !== "file") {
      // zoomed instance: click moves the hex caret, nothing else
      if (ev.type === "down" && off !== null) this.store.setCaret(off);
      return;
    }

    const sel = this.store.state.offsetRange;
    if (ev.type === "down" && off !== null) {
      hideTooltip();
      this.drag = this.hitTest(off, sel);
      if (this.drag.kind === "new") {
        this.store.setSelection({ start: off, end: off + 1 });
      }
      this.store.setCaret(off);
    } else if (ev.type === "move" && this.drag.kind !== "idle" && (ev.buttons & 1)) {
      if (off === null) return;
      this.dragTo(off);
    } else if (ev.type === "up") {
      this.drag = { kind: "idle" };
    }
  }

  /** m1/m2/band hit test — dragging near a marker moves it, dragging inside
      the band moves both, anywhere else starts a new selection. */
  private hitTest(off: number, sel: OffsetRange | null): DragState {
    if (sel && this.layout === "linear") {
      const cellPx = Math.max(this.view.cellCssW, 1);
      const tolCells = Math.ceil(EDGE_HIT_PX / cellPx) * this.nbytes() / this.nCells;
      const tol = Math.max(1, tolCells);
      if (Math.abs(off - sel.start) <= tol) return { kind: "m1" };
      if (Math.abs(off - sel.end) <= tol) return { kind: "m2" };
      if (off > sel.start && off < sel.end) {
        return { kind: "band", grabOff: off, origin: { ...sel } };
      }
    }
    return { kind: "new", anchor: off };
  }

  private dragTo(off: number): void {
    const d = this.drag;
    const sel = this.store.state.offsetRange;
    if (d.kind === "new") {
      this.store.setSelection(off >= d.anchor
        ? { start: d.anchor, end: off + 1 }
        : { start: off, end: d.anchor + 1 });
    } else if (d.kind === "m1" && sel) {
      this.store.setSelection({ start: off, end: sel.end });
    } else if (d.kind === "m2" && sel) {
      this.store.setSelection({ start: sel.start, end: off });
    } else if (d.kind === "band" && this.model) {
      const shift = off - d.grabOff;
      const len = d.origin.end - d.origin.start;
      let s = d.origin.start + shift;
      s = Math.max(0, Math.min(s, this.model.size - len));
      this.store.setSelection({ start: s, end: s + len });
    }
  }

  private tooltip(ev: CellPointerEvent, off: number): void {
    if (!this.model) return;
    const va = offToVa(this.model.mappings, off);
    const region = regionAtOff(this.model.regions, off);
    const lines: SafeHtml[] = [
      html`<b>${fmtHex(off)}</b>${va !== null ? ` · VA ${fmtHex(va)}` : " · unmapped"}`,
    ];
    if (region) {
      lines.push(html`<span class="t2">${region.name} (${region.kind}, ${region.perms || "–"})</span>`);
    }
    if (this.sigMeta) {
      lines.push(html`<span class="t2">${this.sigMeta.lo}–${this.sigMeta.hi} ${this.sigMeta.unit}</span>`);
    }
    showTooltip(ev.clientX, ev.clientY, joinHtml(lines, "<br>"));
  }

  /* ------------------------------------------------------- overlay */

  private overlay(ctx: CanvasRenderingContext2D, v: RasterCanvas): void {
    const sel = this.store.state.offsetRange;
    const css = getComputedStyle(document.documentElement);
    const fill = css.getPropertyValue("--select-fill").trim();
    const edge = css.getPropertyValue("--select-edge").trim();

    this.locateOverlay(ctx, v);   // under the selection band

    if (sel && sel.end > this.start && sel.start < this.end && this.nCells) {
      const c0 = this.cellOfOffset(Math.max(sel.start, this.start));
      const c1 = this.cellOfOffset(Math.min(sel.end, this.end) - 1);
      ctx.fillStyle = fill;
      if (this.layout === "hilbert") {
        this.hilbertRun(ctx, v, c0, c1);
      } else {
        this.linearRun(ctx, v, c0, c1);
        this.markerTicks(ctx, v, c0, c1, edge);
      }
    }

    const hov = this.store.state.hoveredOffset;
    if (hov !== null && hov >= this.start && hov < this.end && this.nCells) {
      const c = this.cellOfOffset(hov);
      ctx.fillStyle = edge;
      if (this.layout === "hilbert") {
        const [x, y] = d2xy(this.order, c);
        const [cx, cy] = v.cellToCss(x, y);
        ctx.fillRect(cx, cy, Math.max(v.cellCssW, 2), Math.max(v.cellCssH, 2));
      } else {
        const [cx, cy] = v.cellToCss(c % v.w, Math.floor(c / v.w));
        ctx.fillRect(cx - 1, cy - 1, Math.max(v.cellCssW, 2) + 2,
                     Math.max(v.cellCssH, 2) + 2);
      }
    }
  }

  /** Fill cells c0..c1 (row-major) with the current fillStyle. */
  private linearRun(
    ctx: CanvasRenderingContext2D, v: RasterCanvas, c0: number, c1: number,
  ): void {
    const y0 = Math.floor(c0 / v.w), y1 = Math.floor(c1 / v.w);
    const x0 = c0 % v.w, x1 = c1 % v.w;
    const cw = v.cellCssW, chh = v.cellCssH;
    if (y0 === y1) {
      const [px, py] = v.cellToCss(x0, y0);
      ctx.fillRect(px, py, (x1 - x0 + 1) * cw, chh);
    } else {
      let [px, py] = v.cellToCss(x0, y0);
      ctx.fillRect(px, py, (v.w - x0) * cw, chh);          // first partial row
      if (y1 > y0 + 1) {
        [px, py] = v.cellToCss(0, y0 + 1);
        ctx.fillRect(px, py, v.w * cw, (y1 - y0 - 1) * chh); // full rows
      }
      [px, py] = v.cellToCss(0, y1);
      ctx.fillRect(px, py, (x1 + 1) * cw, chh);            // last partial row
    }
  }

  private markerTicks(
    ctx: CanvasRenderingContext2D, v: RasterCanvas,
    c0: number, c1: number, edge: string,
  ): void {
    ctx.fillStyle = edge;
    const chh = v.cellCssH, cw = v.cellCssW;
    const [m1x, m1y] = v.cellToCss(c0 % v.w, Math.floor(c0 / v.w));
    const [m2x, m2y] = v.cellToCss(c1 % v.w, Math.floor(c1 / v.w));
    ctx.fillRect(m1x - 1, m1y, 2, chh);
    ctx.fillRect(m2x + cw - 1, m2y, 2, chh);
  }

  /** Fill cells c0..c1 along the hilbert curve with the current fillStyle. */
  private hilbertRun(
    ctx: CanvasRenderingContext2D, v: RasterCanvas, c0: number, c1: number,
  ): void {
    const cw = Math.max(v.cellCssW, 1), chh = Math.max(v.cellCssH, 1);
    const step = Math.max(1, Math.floor((c1 - c0) / 60000)); // cap the work
    for (let d = c0; d <= c1; d += step) {
      const [x, y] = d2xy(this.order, d);
      const [px, py] = v.cellToCss(x, y);
      ctx.fillRect(px, py, cw, chh);
    }
  }

  /** Brush-to-locate density from the bigram view, tinted over the map
      with alpha driven by per-bin match density. */
  private locateOverlay(ctx: CanvasRenderingContext2D, v: RasterCanvas): void {
    const loc = this.store.state.locate;
    if (!loc || !this.nCells || loc.max === 0) return;
    if (loc.end <= this.start || loc.start >= this.end) return;
    const rgb = LOCATE_RGB[this.theme];
    const n = loc.density.length;
    const span = loc.end - loc.start;
    let i = 0;
    while (i < n) {                       // runs of adjacent non-empty bins
      if (loc.density[i] === 0) { i++; continue; }
      let j = i, dmax = 0;
      while (j < n && loc.density[j] > 0) {
        if (loc.density[j] > dmax) dmax = loc.density[j];
        j++;
      }
      const o0 = Math.max(loc.start + Math.floor((i * span) / n), this.start);
      const o1 = Math.min(loc.start + Math.ceil((j * span) / n), this.end);
      if (o1 > o0) {
        const c0 = this.cellOfOffset(o0);
        const c1 = this.cellOfOffset(o1 - 1);
        const alpha = 0.25 + 0.5 * (dmax / loc.max);
        ctx.fillStyle = `rgba(${rgb},${alpha.toFixed(3)})`;
        if (this.layout === "hilbert") this.hilbertRun(ctx, v, c0, c1);
        else this.linearRun(ctx, v, c0, c1);
      }
      i = j;
    }
  }

  /* -------------------------------------------------------- legend */

  private renderLegend(): void {
    if (!this.legendEl) return;
    if (this.mode === "byteclass") {
      // Swatch colours are set through the CSSOM rather than a style="…"
      // attribute so the page can keep a strict `style-src 'self'` CSP.
      const colors = BYTE_CLASS_COLORS[this.theme];
      this.legendEl.replaceChildren(...BYTE_CLASS_NAMES.map((name, i) => {
        const key = document.createElement("span");
        key.className = "key";
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = colors[i];
        key.append(swatch, name);
        return key;
      }));
    } else if (this.mode === "signal") {
      replace(this.legendEl, span("key",
        "entropy 0–8 bits/byte, window 4 KiB, max per pixel (viridis)"));
    } else {
      replace(this.legendEl, span("key", "byte value 0x00–0xFF (grey)"));
    }
  }
}
