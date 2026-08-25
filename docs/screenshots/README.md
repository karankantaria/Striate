# Screenshots

The four images `README.md` embeds. Replacing one is a drop-in: keep the
filename and the README needs no edit.

They are referenced by absolute `raw.githubusercontent.com` URLs rather than
relative paths, because PyPI renders the README with no repository context and
a relative `src` is a broken image there. Two consequences: a new screenshot
only appears once it is **pushed to `main`**, and immediately after a repo is
made public the CDN can still serve a cached 404 for a few minutes.

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

| File | Workspace | What it should show |
|---|---|---|
| `overview.png` | Overview | The address-space map with a region selected, and the triage verdict beside it. |
| `bytes.png` | Bytes | Entropy over a packed binary, with the selection linked to the byte-class surface. |
| `patterns.png` | Patterns | A bigram histogram next to the dot plot — ideally a sample where the repeat structure is obvious. |
| `code.png` | Code | A control-flow graph with a recovered function selected, showing a boundary drawn as uncertain. |

Optional, if the linked-selection idea is not already obvious: a short GIF of
selecting a range in one view and the others following. Name it `linked.gif`
and keep it under 5 MB.

Current set is ~5.6 MB total. Worth watching: these live in the repository
rather than in a release asset, so every clone pays for them.
