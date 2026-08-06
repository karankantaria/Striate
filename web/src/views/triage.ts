/* Triage panel — the verdict banner plus a clickable findings list.
   Every finding that carries offsets is a navigation surface, not text:
   clicking it drives the SelectionStore, and every linked view follows
   (the report is how you *get to* the evidence, per PLAN §P11). */

import type { TriageDoc, TriageFinding, Verdict } from "../api.ts";
import { fmtHex, type SelectionStore } from "../store.ts";

const VERDICT_LABEL: Record<Verdict, string> = {
  likely_packed: "likely packed",
  likely_benign_binary: "likely benign",
  non_executable: "non-executable",
  corrupt: "corrupt",
  inconclusive: "inconclusive",
};

export class TriagePanel {
  private el: HTMLElement;
  private store: SelectionStore;
  private doc: TriageDoc | null = null;

  constructor(el: HTMLElement, store: SelectionStore) {
    this.el = el;
    this.store = store;
    store.on("selection", () => this.markActive());
  }

  clear(): void {
    this.doc = null;
    this.el.innerHTML = `<div class="triage-pending muted">triage pending…</div>`;
  }

  set(doc: TriageDoc): void {
    this.doc = doc;
    let html =
      `<div class="triage-verdict v-${doc.verdict}">` +
      `<span class="v-label">${VERDICT_LABEL[doc.verdict]}</span>` +
      `<span class="v-conf">confidence ${doc.confidence.toFixed(2)}</span>` +
      `</div>`;
    doc.findings.forEach((f, i) => {
      const nav = f.offsets !== null;
      html += `<div class="finding-row sev-${f.severity}${nav ? " nav" : ""}"` +
        ` data-fi="${i}" title="${esc(f.detail)}">` +
        `<span class="f-sev"></span>` +
        `<span class="f-body"><span class="f-code">${esc(f.code)}</span> ` +
        `<span class="f-detail">${esc(f.detail)}</span>` +
        (nav ? `<span class="f-span">${fmtHex(f.offsets![0])}–${fmtHex(f.offsets![1])}</span>` : "") +
        `</span></div>`;
    });
    if (!doc.findings.length) {
      html += `<div class="muted triage-none">no findings</div>`;
    }
    this.el.innerHTML = html;

    this.el.querySelectorAll<HTMLElement>(".finding-row.nav").forEach((row) => {
      row.addEventListener("click", () => {
        const f = this.doc?.findings[Number(row.dataset.fi)];
        if (!f?.offsets) return;
        this.navigate(f);
      });
    });
  }

  private navigate(f: TriageFinding): void {
    const [start, end] = f.offsets!;
    this.store.setSelection({ start, end });
    this.store.setCaret(start);
  }

  private markActive(): void {
    const sel = this.store.state.offsetRange;
    this.el.querySelectorAll<HTMLElement>(".finding-row.nav").forEach((row) => {
      const f = this.doc?.findings[Number(row.dataset.fi)];
      const active = !!sel && !!f?.offsets &&
        f.offsets[0] === sel.start && f.offsets[1] === sel.end;
      row.classList.toggle("active", active);
    });
  }
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}
