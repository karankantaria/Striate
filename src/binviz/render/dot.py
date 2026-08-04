"""Graphviz DOT export of a recovered CFG — verification artifact.

`binviz cfg <file> --func main --dot out.dot && dot -Tpng out.dot` is both
the eyeball check for Phase 5 and the reference render the Phase 10 web
view is compared against.

Uncertainty is rendered, not hidden: low-confidence blocks get dashed
borders, and every unresolved indirect jump gets a visible "?" sentinel
node so a CFG with holes never reads as complete.
"""

from __future__ import annotations

_EDGE_STYLE = {
    "true":                ('color="#2e7d32"', "T"),
    "false":               ('color="#c62828"', "F"),
    "uncond":              ('color="#37474f"', ""),
    "fallthrough":         ('color="#90a4ae"', ""),
    "indirect_unresolved": ('color="#f9a825", style=dashed', "?"),
}

_TERM_COLOUR = {
    "ret": "#e8f5e9", "indirect": "#fff8e1", "invalid": "#ffebee",
    "call_noreturn": "#fce4ec", "halt": "#efebe9",
}


def _esc(s: str) -> str:
    """Escape for a DOT quoted string. Newlines become `\\l` (left-justified
    line break) — a literal newline inside quotes is legal DOT but renders
    as a space, silently collapsing every block into one line."""
    return (s.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\l").replace("\r", ""))


def cfg_to_dot(doc: dict, max_insns: int = 24) -> str:
    """Render one function's CFG document (Function.to_json()) as DOT."""
    fn = doc["function"]
    title = (f'{fn["name"]}  {fn["va"]:#x}  ({fn["discovery"]}, '
             f'confidence {fn["confidence"]}, {fn["mode"]})')
    if not fn["complete"]:
        title += "  [INCOMPLETE]"

    out = [
        "digraph cfg {",
        "  graph [fontname=\"monospace\", labelloc=t, "
        f'label="{_esc(title)}"];',
        '  node [shape=box, fontname="monospace", fontsize=9];',
        '  edge [fontname="monospace", fontsize=8];',
    ]

    for b in doc["blocks"]:
        lines = [f'{b["va"]:#x}:']
        shown = b["insns"][:max_insns]
        for i in shown:
            lines.append(f'  {i["mnemonic"]} {i["op"]}'.rstrip())
        if len(b["insns"]) > len(shown):
            lines.append(f'  ... {len(b["insns"]) - len(shown)} more')
        lines.append(f'[{b["terminator"]}]')
        low = b["confidence"] != "high"
        fill = _TERM_COLOUR.get(b["terminator"])
        # a guessed block is drawn dashed, so it reads as a guess
        style = ",".join(s for s in ("filled" if fill else "",
                                     "dashed" if low else "") if s)
        attrs = [f'label="{_esc(chr(10).join(lines))}\\l"']
        if style:
            attrs.append(f'style="{style}"')
        if fill:
            attrs.append(f'fillcolor="{fill}"')
        out.append(f'  b{b["id"]} [{", ".join(attrs)}];')

    for e in doc["edges"]:
        style, label = _EDGE_STYLE.get(e["kind"], ("", ""))
        attrs = [style] if style else []
        if label:
            attrs.append(f'label="{label}"')
        out.append(f'  b{e["src"]} -> b{e["dst"]}'
                   + (f' [{", ".join(attrs)}];' if attrs else ";"))

    # unresolved control flow gets a visible sentinel rather than silence
    by_block = {}
    for b in doc["blocks"]:
        for i in b["insns"]:
            by_block[i["va"]] = b["id"]
    for n, u in enumerate(doc["unresolved"]):
        src = by_block.get(u["va"])
        if src is None:
            continue
        hint = f'{u["reason"]}\n{u["hint"]}' if u["hint"] else u["reason"]
        out.append(f'  u{n} [label="?\\n{_esc(hint)}", shape=diamond, '
                   'color="#f9a825", fontcolor="#8d6e00"];')
        out.append(f'  b{src} -> u{n} [color="#f9a825", style=dashed];')

    out.append("}")
    return "\n".join(out) + "\n"


def save_cfg_dot(doc: dict, path: str, max_insns: int = 24) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(cfg_to_dot(doc, max_insns))
