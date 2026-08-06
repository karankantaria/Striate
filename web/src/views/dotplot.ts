/* Dot plot view — self-similarity matrix (Phase 9). Cell (x, y) lights
   up when the k-byte window at the x-axis offset matches the one at the
   y-axis offset. The server picks exact mode for small ranges and
   progressive stochastic sampling otherwise (§5.5); this view drives the
   sampled mode's refinement loop — re-requesting with an advancing
   cursor until every position is resolved — and keeps the mode +
   progress label persistent, because a sparse sampled plot must never be
   mistaken for "no self-similarity". */

import { getSurface, type BinaryModel } from "../api.ts";
import { RasterCanvas } from "../canvas/raster.ts";
import { VIRIDIS, type Theme } from "../colormap.ts";
import { html } from "../dom.ts";
import { clearPaneError, paneError } from "../panestatus.ts";
import {
  fmtHex, fmtSize, type OffsetRange, type SelectionStore,
} from "../store.ts";
import { hideTooltip, showTooltip } from "../tooltip.ts";

const MIN_SIDE = 64;
const MAX_SIDE = 1024;
const REFINE_DELAY_MS = 40;        // breathing room between sampling passes
const MAX_PASSES = 200;            // hard stop; progress label says the rest

export type DotAxis = "selection" | "file";

export interface DotPlotControls {
  ax1: HTMLSelectElement;          // x axis range: selection | file
  ax2: HTMLSelectElement;          // y axis range
  window: HTMLInputElement;        // k
  samples: HTMLInputElement;       // positions per sampling pass
  run: HTMLInputElement;           // checkbox: keep refining
  status: HTMLElement;
}

interface DotMeta {
  mode: "exact" | "sampled" | "empty";
  progress?: number;
  hits?: number;
  resolved?: number;
  positions?: number;
  cursor?: number;
  lit_cells?: number;
  max_cell?: number;
  warnings?: string[];
  tiled?: boolean;          // huge axis 2: range streamed in tiles (P12)
  tiles?: number;
  tiles_done?: number;
  rows_sampled?: number;    // huge axis 1: fixed random row subset (P12)
}

export class DotPlotView {
  private view: RasterCanvas;
  private c: DotPlotControls;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  private meta: DotMeta | null = null;
  private r1: OffsetRange = { start: 0, end: 0 };
  private r2: OffsetRange = { start: 0, end: 0 };
  private side = 0;
  private fetchSeq = 0;
  private passes = 0;
  private inFlight = false;
  private restartTimer: number | undefined;
  private refineTimer: number | undefined;

  constructor(host: HTMLElement, controls: DotPlotControls,
              store: SelectionStore, _theme: Theme) {
    this.c = controls;
    this.store = store;
    this.view = new RasterCanvas(host, {
      fit: "square",
      onResize: () => this.debouncedRestart(),
      onPointer: (ev) => {
        if (!ev.inside) { hideTooltip(); return; }
        if (ev.type === "move") this.tooltip(ev.clientX, ev.clientY, ev.cellX, ev.cellY);
        if (ev.type === "down") {
          const off = this.cellOffset(ev.cellX, this.r1, this.view.w);
          if (off !== null) this.store.setCaret(off);
        }
        if (ev.type === "leave") hideTooltip();
      },
    });
    this.view.setLut(VIRIDIS);

    store.on("selection", () => this.debouncedRestart());
    controls.ax1.addEventListener("change", () => this.restart());
    controls.ax2.addEventListener("change", () => this.restart());
    controls.window.addEventListener("change", () => this.restart());
    controls.samples.addEventListener("change", () => this.restart());
    controls.run.addEventListener("change", () => {
      if (controls.run.checked) this.refine(); else this.stopRefining();
    });
  }

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.restart();
  }

  /* ----------------------------------------------------------- ranges */

  private axisRange(which: DotAxis): OffsetRange {
    const size = this.model?.size ?? 0;
    const sel = this.store.state.offsetRange;
    return which === "selection" && sel ? sel : { start: 0, end: size };
  }

  private params(): { k: number; samples: number } {
    const k = parseInt(this.c.window.value, 10);
    const s = parseInt(this.c.samples.value, 10);
    return {
      k: Number.isFinite(k) ? Math.max(1, Math.min(k, 64)) : 8,
      samples: Number.isFinite(s) ? Math.max(1000, Math.min(s, 10_000_000))
                                  : 500_000,
    };
  }

  private debouncedRestart(): void {
    window.clearTimeout(this.restartTimer);
    this.restartTimer = window.setTimeout(() => this.restart(), 200);
  }

  /** New ranges/params: drop the old accumulator's identity and refetch
      from scratch. The server keys accumulators by (range, params, seed),
      so a changed key simply starts a fresh one. */
  restart(): void {
    if (!this.id || !this.model) return;
    this.stopRefining();
    this.r1 = this.axisRange(this.c.ax1.value as DotAxis);
    this.r2 = this.axisRange(this.c.ax2.value as DotAxis);
    this.view.sync();
    // Hidden pane (a workspace the user is not on, §3.4): do nothing. This
    // is the one view where that matters beyond politeness — a dot plot
    // restart runs a sampling pass server-side and the refine loop keeps
    // asking for more, so a hidden one would burn CPU and disk on a picture
    // nobody is looking at. Showing the pane resizes the canvas, which
    // fires onResize -> debouncedRestart.
    if (this.view.cssW === 0) return;
    this.side = Math.max(MIN_SIDE, Math.min(
      MAX_SIDE, Math.min(this.view.cssW, this.view.cssH) || 512));
    this.passes = 0;
    this.fetchSeq++;               // invalidate any in-flight response
    void this.fetchPass();
  }

  private stopRefining(): void {
    window.clearTimeout(this.refineTimer);
  }

  private refine(): void {
    if (!this.meta || this.meta.mode !== "sampled") return;
    if ((this.meta.progress ?? 1) >= 1 || this.passes >= MAX_PASSES) return;
    if (!this.c.run.checked || this.inFlight) return;
    this.stopRefining();
    this.refineTimer = window.setTimeout(
      () => void this.fetchPass(), REFINE_DELAY_MS);
  }

  /* ------------------------------------------------------------ fetch */

  private async fetchPass(): Promise<void> {
    if (!this.id || this.inFlight) return;
    const seq = this.fetchSeq;
    const { k, samples } = this.params();
    this.inFlight = true;
    try {
      const raster = await getSurface(this.id, "dotplot", {
        start: this.r1.start, end: this.r1.end,
        off2: this.r2.start, end2: this.r2.end,
        w: this.side, h: this.side,
        window: k, max_samples: samples, seed: 0,
        cursor: this.passes,        // pacing only; keeps each GET distinct
      });
      if (seq !== this.fetchSeq) return;
      this.meta = raster.meta.meta as unknown as DotMeta;
      this.passes += 1;
      this.view.setRaster(raster.pixels, raster.w, raster.h);
      clearPaneError(this.view.host);
    } catch (e) {
      if (seq !== this.fetchSeq) return;
      const status = (e as { status?: number }).status;
      if (status === 409 || status === 410) {
        window.clearTimeout(this.restartTimer);
        this.restartTimer = window.setTimeout(() => this.restart(), 700);
      } else {
        paneError(this.view.host, "could not compute the dot plot", e,
                  () => this.restart());
      }
      return;
    } finally {
      this.inFlight = false;
    }
    this.renderStatus();
    this.refine();
  }

  /* ------------------------------------------------------------ label */

  private renderStatus(): void {
    const m = this.meta;
    if (!m) return;
    const parts: string[] = [];
    if (m.mode === "exact") {
      parts.push("exact");
      parts.push(`${(m.lit_cells ?? 0).toLocaleString()} lit cells`);
    } else if (m.mode === "sampled") {
      const pct = Math.floor((m.progress ?? 0) * 100);
      parts.push(`sampled ${pct}%`);
      if (m.tiled) parts.push(`tile ${m.tiles_done ?? 0}/${m.tiles ?? 0}`);
      if (m.rows_sampled !== undefined) {
        parts.push(`${m.rows_sampled.toLocaleString()} rows sampled`);
      }
      parts.push(`${(m.hits ?? 0).toLocaleString()} hits`);
      if ((m.progress ?? 1) < 1) {
        parts.push(this.c.run.checked && this.passes < MAX_PASSES
          ? "refining…" : "paused — unsampled areas may hide matches");
      }
    } else {
      parts.push("range shorter than the window");
    }
    parts.push(`${fmtSize(this.r1.end - this.r1.start)} × ` +
               `${fmtSize(this.r2.end - this.r2.start)}`);
    this.c.status.textContent = parts.join(" · ");
  }

  /* ------------------------------------------------------ interaction */

  private cellOffset(cell: number, r: OffsetRange, extent: number): number | null {
    if (extent <= 0 || r.end <= r.start) return null;
    return r.start + Math.floor((cell / extent) * (r.end - r.start));
  }

  private tooltip(cx: number, cy: number, cellX: number, cellY: number): void {
    const ox = this.cellOffset(cellX, this.r1, this.view.w);
    const oy = this.cellOffset(cellY, this.r2, this.view.h);
    if (ox === null || oy === null) return;
    showTooltip(cx, cy, html`<span class="t2">x</span> ${fmtHex(ox)}<br><span class="t2">y</span> ${fmtHex(oy)}`);
  }
}
