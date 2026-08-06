/* Model info panel — format/arch summary, warnings, and a clickable region
   list (clicking a region drives the SelectionStore, so every view
   follows). */

import type { BinaryModel } from "../api.ts";
import { esc } from "../escape.ts";
import { fmtHex, fmtSize, type SelectionStore } from "../store.ts";

export class InfoPanel {
  private el: HTMLElement;
  private store: SelectionStore;
  private model: BinaryModel | null = null;

  constructor(el: HTMLElement, store: SelectionStore) {
    this.el = el;
    this.store = store;
    store.on("selection", () => this.markActive());
  }

  setBinary(model: BinaryModel): void {
    this.model = model;
    this.render();
  }

  private render(): void {
    const m = this.model;
    if (!m) { this.el.innerHTML = ""; return; }
    const rows: [string, string][] = [
      ["format", `${m.format} · ${m.arch} · ${m.bits}-bit ${m.endian}`],
      ["size", fmtSize(m.size)],
      ["entry", m.entry_va !== null ? fmtHex(m.entry_va) : "–"],
      ["symbols", String(m.symbols.length)],
      ["imports", String(m.imports.length)],
      ["sha256", m.sha256.slice(0, 16) + "…"],
    ];
    let html = "<table>" + rows.map(([k, v]) =>
      `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join("") + "</table>";

    if (m.warnings.length) {
      html += `<h3>Warnings</h3>` + m.warnings.map((w) =>
        `<div class="warn">⚠ ${esc(w)}</div>`).join("");
    }

    html += `<h3>Regions</h3>`;
    m.regions.forEach((r, i) => {
      if (r.file_off < 0 || r.file_size <= 0) return;
      html += `<div class="region-row" data-ri="${i}">` +
        `<span class="rname" title="${esc(r.name)}">${esc(r.name)}</span>` +
        `<span class="rperms">${r.perms || "–"}</span>` +
        `<span class="rsize">${fmtSize(r.file_size)}</span></div>`;
    });
    this.el.innerHTML = html;

    this.el.querySelectorAll<HTMLElement>(".region-row").forEach((row) => {
      row.addEventListener("click", () => {
        const r = m.regions[Number(row.dataset.ri)];
        this.store.setSelection(
          { start: r.file_off, end: r.file_off + r.file_size });
        this.store.setCaret(r.file_off);
      });
    });
  }

  private markActive(): void {
    const sel = this.store.state.offsetRange;
    const m = this.model;
    if (!m) return;
    this.el.querySelectorAll<HTMLElement>(".region-row").forEach((row) => {
      const r = m.regions[Number(row.dataset.ri)];
      const active = !!sel && r.file_off === sel.start &&
        r.file_off + r.file_size === sel.end;
      row.classList.toggle("active", active);
    });
  }
}
