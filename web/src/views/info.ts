/* Model info panel — format/arch summary, warnings, and a clickable region
   list (clicking a region drives the SelectionStore, so every view
   follows). */

import type { BinaryModel } from "../api.ts";
import { el, replace, span } from "../dom.ts";
import { optionList, refreshTabStop, setOptionSelected } from "../listnav.ts";
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
    if (!m) { replace(this.el); return; }
    const rows: [string, string][] = [
      ["format", `${m.format} · ${m.arch} · ${m.bits}-bit ${m.endian}`],
      ["size", fmtSize(m.size)],
      ["entry", m.entry_va !== null ? fmtHex(m.entry_va) : "–"],
      ["symbols", String(m.symbols.length)],
      ["imports", String(m.imports.length)],
      ["sha256", m.sha256.slice(0, 16) + "…"],
    ];
    // The region rows get their own container rather than sitting loose
    // among the table and the warnings: the listbox below claims everything
    // inside it as list content, and a summary table is not a list item.
    const regions = el("div", { class: "region-list" },
      ...m.regions.flatMap((r, i) =>
        r.file_off < 0 || r.file_size <= 0 ? [] : [
          el("div", { class: "region-row", "data-ri": i },
             el("span", { class: "rname", title: r.name }, r.name),
             span("rperms", r.perms || "–"),
             span("rsize", fmtSize(r.file_size))),
        ]));

    // `r.name` is the raw ELF/PE section name and `w` quotes whatever the
    // parser found — both attacker-controlled, both inert as text nodes.
    replace(this.el,
      el("table", {}, ...rows.map(([k, v]) =>
        el("tr", {}, el("td", {}, k), el("td", {}, v)))),

      m.warnings.length > 0 && el("h3", {}, "Warnings"),
      ...m.warnings.map((w) => el("div", { class: "warn" }, `⚠ ${w}`)),

      el("h3", { id: "regions-heading" }, "Regions"),
      regions);

    optionList(regions, "Regions",
      [...regions.querySelectorAll<HTMLElement>(".region-row")],
      (row) => {
        const r = m.regions[Number(row.dataset.ri)];
        this.store.setSelection(
          { start: r.file_off, end: r.file_off + r.file_size });
        this.store.setCaret(r.file_off);
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
      setOptionSelected(row, active);
    });
    const list = this.el.querySelector<HTMLElement>(".region-list");
    if (list) refreshTabStop(list);
  }
}
