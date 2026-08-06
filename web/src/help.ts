/* Keyboard shortcut help (§3.6).

   The work order's complaint: the only two key bindings in the app were
   documented in a `title` tooltip on a button — which a keyboard user never
   hovers and a screen reader user is never told about. There are more
   bindings now (§3.4 added workspace switching), so the list needs a home
   rather than a bigger tooltip.

   A native `<dialog>` rather than a hand-rolled overlay: `showModal()` gives
   the focus trap, the inert background, Escape-to-close and the `::backdrop`
   for free, and every one of those is a thing hand-rolled overlays get
   wrong. The cost is one element. */

import { el } from "./dom.ts";

interface Shortcut { keys: string[]; what: string }

const SHORTCUTS: { group: string; items: Shortcut[] }[] = [
  {
    group: "Workspaces",
    items: [
      { keys: ["1", "…", "5"], what: "switch workspace" },
      { keys: ["←", "→"], what: "move between tabs (when a tab has focus)" },
      { keys: ["Esc"], what: "leave a maximised pane" },
    ],
  },
  {
    group: "Files",
    items: [
      { keys: ["["], what: "previous file in the directory" },
      { keys: ["]"], what: "next file in the directory" },
    ],
  },
  {
    group: "Lists (findings, regions, functions)",
    items: [
      { keys: ["↑", "↓"], what: "move through the list" },
      { keys: ["Home", "End"], what: "first / last entry" },
      { keys: ["Enter"], what: "go to it — every linked view follows" },
    ],
  },
  {
    group: "This dialog",
    items: [
      { keys: ["?"], what: "open" },
      { keys: ["Esc"], what: "close" },
    ],
  },
];

let dialog: HTMLDialogElement | null = null;

function build(): HTMLDialogElement {
  const d = el("dialog", { id: "help-dialog", "aria-labelledby": "help-title" },
    el("h2", { id: "help-title" }, "Keyboard shortcuts"),
    ...SHORTCUTS.flatMap((section) => [
      el("h3", {}, section.group),
      el("dl", {}, ...section.items.flatMap((s) => [
        el("dt", {}, ...s.keys.map((k) =>
          k === "…" ? el("span", { class: "kbd-sep" }, "…") : el("kbd", {}, k))),
        el("dd", {}, s.what),
      ])),
    ]),
    el("button", { type: "button", class: "help-close" }, "Close"),
  ) as HTMLDialogElement;

  d.querySelector(".help-close")!.addEventListener("click", () => d.close());
  // clicking the backdrop closes: the click lands on the dialog element
  // itself only when it is outside the padded content box
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  document.body.appendChild(d);
  return d;
}

export function isHelpOpen(): boolean {
  return !!dialog?.open;
}

export function toggleHelp(): void {
  dialog ??= build();
  if (dialog.open) dialog.close();
  else dialog.showModal();
}

/** Wire the `?` key and an optional trigger button. */
export function initHelp(trigger?: HTMLElement | null): void {
  trigger?.addEventListener("click", () => toggleHelp());
  document.addEventListener("keydown", (e) => {
    if (e.key !== "?") return;
    const t = e.target as HTMLElement | null;
    const tag = t?.tagName ?? "";
    if (tag === "INPUT" || tag === "TEXTAREA" || t?.isContentEditable) return;
    e.preventDefault();
    toggleHelp();
  });
}
