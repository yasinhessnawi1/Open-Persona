---
name: pdf_generation
description: Produce a professional PDF report via reportlab, executed in the code sandbox.
when_to_use: >
  Use when the user asks for a PDF file, formatted pdf report,
  or downloadable pdf as a file (not text in the chat). Skip for plain-text replies
  or Markdown drafts. Compose with document_drafting (content) first if the user wants
  prose-then-format — the bridge is your own context, NOT a .md file read back in.
tools_required:
  - code_execution
---

# PDF Generation

Produce a clean, multi-page PDF report via `reportlab` (4.2.5, pre-installed
in the sandbox). The library uses a *flowable* model — build a list of
`Paragraph`, `Spacer`, `Table`, `Image`, `PageBreak` objects; a
`SimpleDocTemplate` lays them out. Write to
`/workspace/out/<descriptive-name>.pdf`.

If `persona.identity.visual_style` is set, prefer those aesthetic hints
(colour palette, font preference, voice register) over generic defaults;
otherwise use format defaults.

## When to use

Activate for requests like "generate a PDF report on X," "produce a
printable PDF summary," "make this downloadable as a PDF."

Skip for plain-text or Markdown replies (use `document_drafting`), other
formats (use the matching `*_generation` skill), or reading existing PDFs.

If the user wants prose-then-PDF, draft prose in your own context, then
activate this skill and embed the prose as a Python string. Do **not**
write the draft to a `.md` file and read it back in.

## Procedure

### Step 1: Plan the structure

- **Cover vs body.** Multi-page reports get a distinct cover page
  (`PageBreak` after the title block); short summaries skip it.
- **Sections.** Sketch headings, paragraphs, tables, images in order.
- **Page-spanning tables.** Any table over ~20 rows will spill — use
  `LongTable` with `repeatRows=1` (Step 4).
- **Charts.** A Spec-17 chart produced in the **same session** lives at
  `/workspace/out/charts/<id>.png`; embed via `Image(path, …)`.

### Step 2: Set up the document

```python
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

styles = getSampleStyleSheet()
body = ParagraphStyle(
    "Body", parent=styles["Normal"], fontName="Helvetica",
    fontSize=10.5, leading=14, spaceAfter=6,
)
h1, h2 = styles["Heading1"], styles["Heading2"]

doc = SimpleDocTemplate(
    "/workspace/out/quarterly-summary.pdf",
    pagesize=A4,
    leftMargin=2 * cm, rightMargin=2 * cm,
    topMargin=2 * cm, bottomMargin=2 * cm,
    title="Quarterly summary",
)
story: list = []
```

Pick body font size **≥ 10pt** explicitly. 8pt — the common naive
choice — reads as broken.

### Step 3: Build the flowables story

Append flowables in reading order:

```python
story.append(Paragraph("Tenant protection: quarterly summary", h1))
story.append(Spacer(1, 0.5 * cm))
story.append(Paragraph("Q1 2026 · compiled 2026-04-05", body))
story.append(PageBreak())  # cover → body

story.append(Paragraph("1. Complaints by district", h2))
story.append(Paragraph("Complaints rose 12% quarter-on-quarter…", body))
```

For deeper detail, read the relevant supplement **from inside your code**:

```python
from pathlib import Path
detail = Path("/workspace/in/.skills/pdf_generation/supplements/flowables.md").read_text()
```

Supplements available:

- `flowables.md` — `Paragraph`, `Spacer`, `Table`, `LongTable`, `Image`,
  `KeepTogether`, `TableStyle`, alternating row colour.
- `pagination.md` — page breaks, page numbers via `onPage` /
  `onLaterPages`, multi-page table handling, header / footer frames.
- `images.md` — embedding PNG charts (Spec 17 contract), sizing in cm /
  inch, aspect-ratio preservation.

### Step 4: Hard feature — page-spanning table

A table over one page must be `LongTable`, not `Table`. `Table` clips or
pushes the next flowable off-page; `LongTable` splits across pages, and
`repeatRows=1` makes the header row repeat on every continuation page.

```python
from reportlab.platypus import LongTable, TableStyle
from reportlab.lib import colors

data = [["District", "Complaints", "Resolved", "Pending"]]
data += [[d, n, r, n - r] for d, n, r in district_rows]  # 25+ rows

table = LongTable(data, repeatRows=1,
                  colWidths=[5 * cm, 3 * cm, 3 * cm, 3 * cm])
table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E4053")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 10),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
     [colors.white, colors.HexColor("#F4F6F7")]),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
]))
story.append(table)
```

`colWidths` are mandatory — auto-sizing produces unpredictable splits.
Their sum must fit the page's content width (A4 minus margins ≈ 17 cm).

### Step 5: Page numbers via `onLaterPages`

```python
def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()

doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
```

`doc.page` is reportlab's 1-indexed counter. See `pagination.md` for
header frames, two-column layouts, and skipping the cover page's number.

## Quality checks

Before declaring done, confirm:

- [ ] Output at `/workspace/out/<descriptive-name>.pdf`.
- [ ] Body font set explicitly, ≥ 10pt.
- [ ] Multi-page: at least one `PageBreak` between cover and body
      (reports ≥ 2 pages).
- [ ] Any table over ~20 rows is `LongTable` with `repeatRows=1`.
- [ ] Page numbers in footer via `onLaterPages`.
- [ ] Embedded images sized in cm or inch — never raw pixel scale.
- [ ] Tables: header row styled (bold + fill) and column widths set.
- [ ] Cover page visually distinct from body content.

## Failure modes

**Default 8pt body.** reportlab's defaults are borderline. Override to
≥ 10pt.

**`Table` instead of `LongTable`.** A 30-row `Table` gets clipped at the
page boundary. Use `LongTable(data, repeatRows=1)` for anything that may
overflow.

**Raw `Image(path)` with no size.** Renders at PNG pixel dimensions
interpreted as points — a 1200×800 chart becomes 16 × 11 inches,
off-page. Always pass `width=…, height=…` in cm or inch units.

**`LayoutError` on tall flowable.** A single flowable exceeding page
height raises `LayoutError`. Wrap in `KeepInFrame` (see `flowables.md`)
or split the content.

**Cross-session persistence.** A chart from a previous session is **not**
at `/workspace/out/charts/…` on this turn — the sandbox cleans
`/workspace/out` between executions. Embed only charts produced in the
**same `code_execution` call** (or rely on sandbox session mode if the
orchestrator enables it).
