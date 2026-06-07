---
name: docx_generation
description: Produce a professional Word document via python-docx, executed in the code sandbox.
when_to_use: >
  Use when the user asks for a DOCX file, formatted word document,
  or downloadable docx as a file (not text in the chat). Skip for plain-text replies
  or Markdown drafts. Compose with document_drafting (content) first if the user wants
  prose-then-format — the bridge is your own context, NOT a .md file read back in.
tools_required:
  - code_execution
---

# DOCX Generation

Author a real `.docx` by writing Python that uses `python-docx==1.1.2` and
running it through the `code_execution` tool. The skill teaches what good
output requires; the sandbox executes the code; the file lands in the
workspace.

If `persona.identity.visual_style` is set, prefer those aesthetic hints
(colour palette, font preference, voice register) over generic defaults;
otherwise use format defaults below.

## Output path

Write the final document to `/workspace/out/<descriptive-name>.docx` from
**inside the sandbox** — that is the path the model uses. The runtime
surfaces produced files to the conversation; cross-call persistence in
v0.1 is same-session only (do not promise the user later sessions can
re-open the file in-sandbox).

Filename: lowercase, hyphenated, ends in `.docx`. Example:
`tenant-protection-memo.docx`.

## Compose with `document_drafting`

If the user wants prose-then-format, `document_drafting` populates your
own context with the drafted prose. Embed that prose as a **Python
string** in the generation code — do NOT round-trip through a `.md` file
on disk.

## Minimum bar (every docx ships these)

A produced docx is judged on the file, not the exit code. Hit every row:

1. **Heading 1 / 2 / 3 applied as named styles** — `p.style = doc.styles["Heading 1"]` (or pass `style="Heading 1"` to `add_paragraph`). Not raw bold + size.
2. **Body font set explicitly** — set `doc.styles["Normal"].font.name` (e.g. `"Calibri"`) and `doc.styles["Normal"].font.size = Pt(11)`. Word's defaults are not consistent across versions.
3. **Page numbers in footer** — every multi-page document. See `supplements/styles.md` (footer field code idiom).
4. **Table of contents** if the document has ≥4 headings or the user asked for one. python-docx writes the **field shell**; Word fills it on **first open**. The doc will look empty until then — tell the user once: *"Open in Word and press F9, or right-click the TOC and choose Update Field."* See `supplements/toc.md`.
5. **Tables** with a header row + ≥2 data rows + explicit column widths. See `supplements/tables.md`.
6. **Images** sized to ~3–6 inches wide (`Inches(4)`), not raw 96-dpi giant. See `supplements/images.md`.
7. **No double blank paragraphs**, no leftover `[NEEDS: ...]` markers, no styles-applied-to-empty-runs.

## Skeleton

```python
from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches

doc = Document()

# Body font — set BEFORE adding content.
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(11)

doc.add_heading("Document title", level=0)        # Title
doc.add_heading("First section", level=1)          # Heading 1
doc.add_paragraph("Lead sentence of the section.")
# ... more content ...

Path("/workspace/out/example.docx").parent.mkdir(parents=True, exist_ok=True)
doc.save("/workspace/out/example.docx")
print("WROTE: /workspace/out/example.docx")
```

## When to read a supplement

The SKILL.md body covers the must-do path. Read a supplement **inside
your generated code** before writing the verbose case:

```python
from pathlib import Path
guide = Path(
    "/workspace/in/.skills/docx_generation/supplements/tables.md"
).read_text()
# Read it, then write the table code following the guidance.
```

Available supplements (staged automatically when this skill is active):

- `supplements/tables.md` — multi-row headers, merged cells, column widths, header repeat across pages.
- `supplements/styles.md` — named-style chains, list styles, page-numbers-in-footer field code, paragraph spacing.
- `supplements/images.md` — sizing, inline vs floating, captions, **embedding a Spec 17 chart** at `/workspace/out/charts/<id>.png` (same-session reachability only — do not assume cross-session).
- `supplements/toc.md` — the `fldSimple` / `fldChar` TOC shell, the Word "Update Field" gotcha, and how to pre-populate cached TOC text so it looks filled on first open.

Read the supplement only when the task needs the depth. A 1-page memo
with no TOC does not need `toc.md`.

## Common-pitfall avoidance

- **Don't** use `add_paragraph("Heading", style="Title")` for section headings — `Title` is page-title only. Heading 1 / 2 / 3 are the section styles.
- **Don't** style by applying `run.bold = True` + `run.font.size = Pt(16)` ad hoc. Word's outline / TOC / accessibility tooling reads **named styles**, not visual formatting. Named styles are not optional.
- **Don't** write paragraphs with leading / trailing whitespace runs — they survive in the XML and produce ragged layout.
- **Don't** `add_picture(path)` without `width=Inches(N)` — raw pixel images at 96 dpi blow past the page margin and clip.
- **Don't** generate a TOC and not tell the user it shows empty until Word's first "Update Field" — they will think the file is broken.
- **Don't** call `doc.save("output.docx")` with a relative path — write to `/workspace/out/<name>.docx` explicitly so the runtime surfaces the produced file.

## After the run

When `code_execution` returns successfully, tell the user:

1. The filename you wrote (`/workspace/out/<name>.docx` — they receive it as a referenced artifact).
2. Anything they need to refresh on open (TOC field; `Ctrl+A` then `F9` updates all fields).
3. Any limitation you hit — a partial PASS is honest; a silent PASS on a broken file is not.

## If `code_execution` raises

A Python traceback comes back as a tool error. Read the error, fix the
code, run again. Do not catch the error inside the skill code itself —
the loop's tool-error-recovery handles the round-trip. Typical fixes:

- `KeyError: 'no style with name "Heading 1"'` — the document was created from an unusual base; call `doc.styles.add_style(...)` or fall back to the default template (`Document()` with no template path uses the bundled `default.docx`).
- `docx.image.exceptions.UnrecognizedImageError` — the image is not a supported raster (PNG / JPEG / GIF / BMP / TIFF). Re-save as PNG before embed; SVG is unsupported in python-docx 1.1.2.
- `PackageNotFoundError` on save — the output path's parent does not exist. `mkdir(parents=True, exist_ok=True)` before save.
