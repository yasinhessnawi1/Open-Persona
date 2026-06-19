---
name: document_generation
description: Produce a downloadable document (docx, pdf, pptx, xlsx, md, txt) by writing code in the sandbox, routed by a format parameter.
when_to_use: >
  Use when the user wants a document FILE they can download — a Word doc, PDF,
  PowerPoint, Excel workbook, Markdown file, or plain-text file (not prose in
  the chat). Pass format=docx|pdf|pptx|xlsx|md|txt. For prose-then-format, draft
  the prose in your own context first, then activate this skill and embed the
  prose as a Python string. This skill also COVERS condensing/summarising source
  material into a brief — say so via content_spec. Skip for inline replies.
tools_required:
  - code_execution
metadata:
  parameters:
    type: object
    additionalProperties: false
    required: [format]
    properties:
      format:
        type: string
        enum: [docx, pdf, pptx, xlsx, md, txt]
        description: Output file format. Routes to the matching handler.
      template:
        type: string
        enum: [memo, report, business_letter, research_paper]
        description: Optional starting structure; its Markdown is staged for you to follow.
      domain:
        type: string
        description: Optional domain hint (e.g. legal, business, academic) for tone.
      content_spec:
        type: object
        description: Structured content to render (title, sections, summary, ...).
  not_for:
    - Inline chat replies or single-paragraph answers — just write the text.
    - Reading or parsing an existing document — that is document ingestion, not generation.
    - A format outside the registered six — add a handler module, do not improvise.
  composes_with:
    - web_research
    - code_review
  output_format: A file written to /workspace/out/<name>.<ext>, surfaced to the conversation as an artifact.
  token_budget: 2000
---

# Document Generation

One skill, six formats. You pick the format via the `format` parameter; the
runtime routes to the right handler and stages that format's supplements into
the sandbox. You author the document by writing Python that runs through the
`code_execution` tool — the file lands in the workspace and is surfaced to the
user. New formats are added by the platform (a handler module), never by you
improvising an unsupported one.

## Shared conventions (every format)

- **The sandbox has NO internet — use ONLY pre-installed libraries.** Egress is
  disabled by design, so any runtime package install ALWAYS fails (it hangs
  until the setup timeout). NEVER shell out to a package manager, probe-then-
  install a module, or make any network call from your code. Pre-installed and
  ready to `import`: `python-docx`, `openpyxl`, `matplotlib` (with
  `PIL`/Pillow), plus `pandas`/`numpy`. NOT installed and unobtainable offline:
  `reportlab`, `python-pptx`, `pdfkit`, `fpdf`, `weasyprint`. Route each format
  below to a pre-installed library; when none fits, **degrade honestly**
  (produce the content in a format that works and say so) — never attempt a
  doomed install.
- **Output path.** Write to `/workspace/out/<descriptive-name><ext>` from
  inside the sandbox — lowercase, hyphenated filename. The runtime pre-creates
  `/workspace/out` and surfaces the produced file. Same-session persistence
  only; do not promise cross-session re-open.
- **Visual style.** If `persona.identity.visual_style` is set, prefer those
  aesthetic hints (palette, font, register) over generic defaults.
- **Compose, don't round-trip.** For prose-then-format, the bridge is your own
  context — embed drafted prose as a Python **string**. Do NOT write a `.md`
  file and read it back.
- **Summarise in place.** When the user wants a condensed brief, do the
  condensing as you build `content_spec` (lead with the finding, cut to the
  essentials) — there is no separate summarise skill; this is the folded
  capability (D-24-7).
- **Depth on demand.** The section below is the must-do path. Read a supplement
  **from inside your generated code** only when the task needs the depth:
  `Path("/workspace/in/.skills/document_generation/supplements/<format>-<topic>.md").read_text()`.
- **Templates.** If a `template` is given, its Markdown is staged at
  `/workspace/in/.skills/document_generation/templates/<template>.md` — read it
  and follow its structure, filling `{{placeholders}}` from `content_spec`.

## Formats

### `docx` — Word (`python-docx`, pre-installed)

Named styles, not ad-hoc bold. Set `Normal` font + size before content; apply
`Heading 1/2/3` as named styles; page numbers in the footer; a TOC field shell
for ≥4 headings (tell the user to press F9). Supplements: `docx-tables`,
`docx-styles`, `docx-images`, `docx-toc`.

```python
from docx import Document
from docx.shared import Pt
doc = Document()
doc.styles["Normal"].font.name = "Calibri"; doc.styles["Normal"].font.size = Pt(11)
doc.add_heading("Title", level=0); doc.add_heading("Section", level=1)
doc.add_paragraph("Lead sentence.")
doc.save("/workspace/out/example.docx")
```

### `xlsx` — workbook (`openpyxl`, pre-installed)

Header row + typed cells; column widths; formulas as strings (`"=SUM(B2:B9)"`);
number formats for currency/percent. Supplements: `xlsx-formulas`,
`xlsx-formatting`, `xlsx-charts`.

```python
from openpyxl import Workbook
wb = Workbook(); ws = wb.active; ws.append(["Item", "Qty"]); ws.append(["A", 3])
ws["B4"] = "=SUM(B2:B3)"
wb.save("/workspace/out/sheet.xlsx")
```

### `pdf` — report (`matplotlib.backends.backend_pdf.PdfPages`, pre-installed)

There is NO `reportlab` offline. Render the PDF with matplotlib's `PdfPages`:
one `figure` per page, `fig.text(...)` for headings/body (wrap long lines with
`textwrap.wrap`), and real `Axes` for charts. Good for simple/short documents.
For heavy rich-text/tables where matplotlib is too poor, **degrade honestly**:
produce the same content as `docx` (best fidelity offline) and tell the user a
high-fidelity native PDF needs the custom sandbox template (pending). Use a
headless backend. Supplements: `pdf-flowables`, `pdf-pagination`, `pdf-images`.

```python
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from textwrap import wrap
with PdfPages("/workspace/out/report.pdf") as pdf:
    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
    fig.text(0.08, 0.95, "Title", fontsize=18, weight="bold", va="top")
    y = 0.88
    for line in wrap("Lead paragraph of the report body.", 90):
        fig.text(0.08, y, line, fontsize=11, va="top"); y -= 0.025
    plt.axis("off"); pdf.savefig(fig); plt.close(fig)
    fig2, ax = plt.subplots(figsize=(8.27, 11.69))  # a chart page
    ax.plot(["Jan", "Feb", "Mar"], [42, 47, 51], marker="o"); ax.set_title("Trend")
    pdf.savefig(fig2); plt.close(fig2)
```

### `pptx` — slides (NOT available offline — degrade honestly)

`python-pptx` is NOT installed and cannot be installed offline. Do NOT attempt
any install (it will hang and fail). Instead tell the user that `.pptx`
generation needs the custom sandbox template (pending), and offer a working
alternative now: a slide-structured `docx` (one heading per slide) or a `md`
outline. Pick whichever the user prefers and produce that via the format above.
Supplements: `pptx-layouts`, `pptx-charts`, `pptx-theme` (read for slide
structure you can mirror into the docx/md fallback).

### `md` — Markdown (stdlib)

Plain text with Markdown structure. No library — write the string to disk.

```python
from pathlib import Path
Path("/workspace/out/notes.md").write_text("# Title\n\nLead paragraph.\n")
```

### `txt` — plain text (stdlib)

Same, without Markdown syntax. Wrap to a sane width; no markup.

```python
from pathlib import Path
Path("/workspace/out/notes.txt").write_text("Title\n\nLead paragraph.\n")
```

## After the run

When `code_execution` returns successfully, tell the user: the filename you
wrote, anything to refresh on open (e.g. a Word TOC needs F9), and any
limitation you hit (e.g. PDF rendered via matplotlib; PPTX degraded to docx). A
partial PASS is honest; a silent PASS on a broken file is not.

## If `code_execution` raises

A Python traceback comes back as a tool error. Read it, fix the code, run again
— the loop's tool-error recovery handles the round-trip. Do not catch the error
inside your generated code. A `ModuleNotFoundError` for a missing library means
that library is NOT in the sandbox — switch to the pre-installed route above (or
degrade), never try to install it. Typical fixes live in the format's
supplements.
