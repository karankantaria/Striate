/* Keyboard-navigable option lists (SECURITY-UI-WORKORDER §3.6).

   Three of this app's lists were mouse-only `<div>`s with a click listener:
   the triage findings, the region list, and the CFG function list. Not
   focusable, not reachable by keyboard, invisible to a screen reader — and
   the first of those is the *primary* navigation flow in the whole tool
   ("click a finding, land on the bytes"), so the best workflow path was the
   one nobody without a mouse could take.

   One helper rather than three copies, for the reason `escape.ts` exists:
   three hand-rolled keyboard handlers would drift and two of them would be
   subtly wrong.

   **Why a listbox and not a list of buttons.** Buttons would be simpler, but
   a binary with 200 functions would then put 200 stops in the tab order and
   make Tab useless for reaching anything past the CFG pane. A listbox with a
   roving tabindex is one stop for the whole list, arrows inside it.

   **Why focus does not select.** In a plain listbox, moving the selection
   with the arrows is conventional. Here "selecting" means driving the
   SelectionStore, which re-fetches every linked view — so arrowing through
   twenty findings would fire twenty rounds of network work the user never
   asked for. This is the manual-selection variant: arrows move focus,
   Enter/Space commits. */

const WIRED = "listnavWired";

/** Mark a list container and its rows up as an ARIA listbox and wire the
    keyboard. Call after building the rows — it is safe to call again after
    every rebuild, and the keydown listener is only attached once.

    `rows` must be the interactive rows only: notes, headings and other
    decoration in the same container are left alone (a `role="option"` on a
    row nothing happens to would promise an interaction that does not
    exist). */
export function optionList(
  container: HTMLElement, label: string,
  rows: HTMLElement[], onActivate: (row: HTMLElement) => void,
): void {
  container.setAttribute("role", "listbox");
  container.setAttribute("aria-label", label);

  for (const row of rows) {
    row.setAttribute("role", "option");
    row.tabIndex = -1;
    if (!row.hasAttribute("aria-selected")) {
      row.setAttribute("aria-selected",
                       row.classList.contains("active") ? "true" : "false");
    }
    row.addEventListener("click", () => onActivate(row));
  }
  refreshTabStop(container);

  if (container.dataset[WIRED]) return;
  container.dataset[WIRED] = "1";
  container.addEventListener("keydown", (e) => {
    const rows = options(container);
    if (!rows.length) return;
    const at = rows.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Enter" || e.key === " ") {
      if (at < 0) return;
      e.preventDefault();          // Space would scroll the pane
      rows[at].click();
      return;
    }
    const to =
      e.key === "ArrowDown" ? Math.min(at + 1, rows.length - 1)
      : e.key === "ArrowUp" ? Math.max(at - 1, 0)
      : e.key === "Home" ? 0
      : e.key === "End" ? rows.length - 1
      : -1;
    if (to < 0) return;
    e.preventDefault();            // arrows would scroll the pane
    focusRow(container, rows[at < 0 ? 0 : to]);
  });
}

/** Set a row's selected state, keeping the class and ARIA in step.

    The `active` class and `aria-selected` are the same fact rendered two
    ways; letting a view set one without the other is how a list ends up
    looking right and announcing wrong. */
export function setOptionSelected(row: HTMLElement, selected: boolean): void {
  row.classList.toggle("active", selected);
  row.setAttribute("aria-selected", selected ? "true" : "false");
}

/** Put the list's single tab stop on the selected row, or the first one.

    Call after changing which row is selected: Tab should land where the
    user's attention already is, not back at the top of a 200-row list. */
export function refreshTabStop(container: HTMLElement): void {
  const rows = options(container);
  if (!rows.length) return;
  const stop = rows.find((r) => r.getAttribute("aria-selected") === "true")
    ?? rows[0];
  for (const row of rows) row.tabIndex = row === stop ? 0 : -1;
}

/** Move focus to the list's tab stop.

    For lists that rebuild their rows in response to being activated — the
    CFG list re-renders to move the `active` marker — the old focused
    element is gone by the time the new rows exist, so keyboard focus would
    fall back to the document body and the user would lose their place after
    every Enter. Call this with `container.contains(document.activeElement)`
    captured *before* the rebuild. */
export function focusTabStop(container: HTMLElement): void {
  container
    .querySelector<HTMLElement>(':scope [role="option"][tabindex="0"]')
    ?.focus();
}

function options(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(':scope [role="option"]')];
}

function focusRow(container: HTMLElement, row: HTMLElement): void {
  for (const r of options(container)) r.tabIndex = r === row ? 0 : -1;
  row.focus();
  // the rows live in a scroller; keep the focused one on screen
  row.scrollIntoView({ block: "nearest" });
}
