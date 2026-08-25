# Screenshots

Empty on purpose — the images are not in the repo yet. `README.md` has the
`![...]` lines ready, commented out; add a file here and uncomment its line.

Those lines point at `raw.githubusercontent.com/.../main/docs/screenshots/`
rather than at a relative path, because PyPI renders the README with no
repository context and a relative `src` is a broken image there. The cost is
that a screenshot only appears once it is **pushed to `main`** — locally and
in a pull request it stays a broken link, which is expected rather than a
mistake.

## Settings

- **Width 1600 or more.** Narrower and the five-pane layouts collapse to the
  responsive arrangement, which is not what the README is describing.
- **Dark theme.** It is the only theme; there is no light variant to choose
  between (`RELEASE.md` §3).
- **A real sample, not an empty state.** `corpus/out/hello_upx` is the most
  legible subject: the packed body reads as noise directly beside the
  unpacked stub, so the point of the tool is visible without a caption.
- **Sign-in screen excluded** unless that is the subject. It is a 380px card
  on an otherwise empty page and says nothing about what the tool does.

## The shots

| File | Workspace | What it needs to show |
|---|---|---|
| `overview.png` | Overview | The address-space map with a region selected, and the triage verdict beside it. |
| `bytes.png` | Bytes | Entropy over a packed binary, with the selection linked to the byte-class surface. |
| `patterns.png` | Patterns | A bigram histogram next to the dot plot — ideally a sample where the repeat structure is obvious. |
| `code.png` | Code | A control-flow graph with a recovered function selected, showing a boundary drawn as uncertain. |

Optional, if the linked-selection idea is not already obvious: a short GIF of
selecting a range in one view and the others following. Name it `linked.gif`
and keep it under 5 MB.
