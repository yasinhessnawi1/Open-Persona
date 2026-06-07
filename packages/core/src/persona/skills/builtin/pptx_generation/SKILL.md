---
name: pptx_generation
description: Produce a professional PowerPoint deck via python-pptx, executed in the code sandbox.
when_to_use: >
  Use when the user asks for a PPTX file, formatted powerpoint deck,
  or downloadable pptx as a file (not text in the chat). Skip for plain-text replies
  or Markdown drafts. Compose with document_drafting (content) first if the user wants
  prose-then-format — the bridge is your own context, NOT a .md file read back in.
tools_required:
  - code_execution
---

# PowerPoint Generation

Produce a professional `.pptx` deck via `python-pptx` 1.0.2
(preinstalled in the sandbox). The deliverable is a *file* written to
`/workspace/out/<descriptive-name>.pptx` — not slide markup in chat.

If `persona.identity.visual_style` is set, prefer those aesthetic hints
(colour palette, font preference, voice register) over generic defaults;
otherwise use the format defaults below.

## When to use

Activate for: "make a 6-slide deck on X", "produce a PowerPoint for
the workshop", "turn this draft into a presentation". Skip for chat
outlines or Markdown drafts. For prose-then-deck, draft with
`document_drafting` first (the prose stays in your context), then
activate this skill and embed the prose-as-Python-strings. Do **not**
save a `.md` and read it back — the bridge is your context.

## Procedure

### Step 1: Plan slides before writing code

Write the slide list in your context first: index, layout intent,
title, 2–5 bullets, and (for content slides) a speaker-notes paragraph.
A 6-slide deck without this plan produces a 6-slide mess.

### Step 2: Minimum-viable skeleton

```python
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches

prs = Presentation()  # one master per deck
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

# Default layouts: 0=Title, 1=Title+Content, 2=Section, 5=Title Only
TITLE = prs.slide_layouts[0]
TITLE_CONTENT = prs.slide_layouts[1]

s = prs.slides.add_slide(TITLE)
s.shapes.title.text = "Tenant-protection workshop"
s.placeholders[1].text = "Quarterly briefing — June 2026"

s = prs.slides.add_slide(TITLE_CONTENT)
s.shapes.title.text = "Pre-2024 rules"
body = s.placeholders[1].text_frame
body.text = "First bullet"
for line in ("Second bullet", "Third bullet"):
    body.add_paragraph().text = line

# Speaker notes — HARD FEATURE (see Step 4)
s.notes_slide.notes_text_frame.text = (
    "Walk through the three pre-2024 protection clauses; "
    "the comparison is on slide 4."
)

out = Path("/workspace/out/tenant-protection-workshop.pptx")
prs.save(out)
print(f"wrote {out} ({out.stat().st_size} bytes, {len(prs.slides)} slides)")
```

### Step 3: Lock the formatting discipline

- **One master per deck.** Use `prs.slide_layouts[N]`; never mix slides
  across two `Presentation()` instances.
- **Pick layout per intent.** `[0]` title, `[1]` title+content, `[5]`
  title-only when the body is a single chart or image. Avoid `[6]`
  (Blank) for content slides — placeholders give consistency.
- **Title ≥28pt, body ≥18pt.** Default master honours this. Only
  override with `run.font.size = Pt(28)` for non-default emphasis;
  never set body below 18pt.
- **One font family per deck.** Pick one (e.g. `"Calibri"` or the
  persona's preference) and stick with it. Mixed fonts within a slide
  is the #1 amateur tell.
- **No off-slide overflow.** Long bullet lists (>~6 lines) split to two
  slides; don't shrink-to-fit.

### Step 4: Speaker notes (the hard feature)

Speaker notes are checked programmatically in inspection:

```python
slide.notes_slide.notes_text_frame.text = "2–4 sentence speaker note."
```

`slide.notes_slide` lazily creates the notes part on first access.
Each note: 2–4 sentences, what the presenter actually says — NOT the
bullet text repeated. Empty or single-word notes fail the quality bar.
For the workshop-style task, populate notes on the substantive slides
(typically slides 3, 4, and 6 of a 6-slide deck).

### Step 5: Save under `/workspace/out/`

Use a descriptive lowercase-hyphen filename:
`tenant-protection-workshop.pptx`, not `presentation.pptx`. After
`prs.save(path)`, print a one-line confirmation with path, byte count,
and slide count.

**Same-session reachability only.** Files in `/workspace/out/` are
reachable to the next `code_execution` call in the same session; across
sessions nothing persists in v0.1. Don't promise a persistent download
URL — the file is surfaced via the produced-files list.

## On-demand depth

The body above covers ~80% of decks. For the rest, read a supplement
from inside your code:

```python
from pathlib import Path
hint = Path(
    "/workspace/in/.skills/pptx_generation/supplements/layouts.md"
).read_text()
```

- `supplements/layouts.md` — two-column, image+caption, section header,
  comparison layouts; using a non-default master.
- `supplements/theme.md` — typography weight, accent colour, master-page
  edits, mapping `persona.identity.visual_style` to palette + font.
- `supplements/charts.md` — embedding a Spec-17 PNG (D-16-5 contract)
  vs building a native python-pptx chart inline.

Read only when the slide intent needs it.

## Quality checks

Before declaring done, verify in code:

- `len(prs.slides) == <requested>`.
- Every content slide uses a non-Blank layout.
- Every requested speaker-notes slide has non-empty
  `notes_slide.notes_text_frame.text`.
- Title and body placeholders populated (no blank shapes).
- One font family across the deck.
- File at `/workspace/out/<name>.pptx`; `out.stat().st_size` >5 KB
  (a valid minimal deck is ~30 KB).

## Failure modes

**Default-template mess.** Using `slide_layouts[0]` for every slide —
the title layout has no body placeholder; bullets end up in a
mispositioned text box. Use `[1]` for slides with bullets.

**Empty notes.** `notes_text_frame.text` is `""` unless set. Set every
requested notes slide.

**Shrunk-to-fit.** python-pptx does not auto-resize; setting `Pt(12)`
to "make it fit" fails the quality bar. Split the slide instead.

**Two presentations.** Creating a second `Presentation()` and copying
shapes breaks master consistency. Stay inside one `prs` for the deck.

**Missing PNG.** When embedding via `add_picture`, the PNG must exist
at the path. Same-session only.

## Chart embedding (Spec 17)

Spec 17 produces charts at `/workspace/out/charts/<id>.png` (sandbox
path). Embed via:

```python
from pptx.util import Inches
slide.shapes.add_picture(
    "/workspace/out/charts/notice-periods.png",
    left=Inches(1.0), top=Inches(1.8),
    width=Inches(11.0), height=Inches(5.0),
)
```

PNG only (D-16-5); python-pptx 1.0.2 has no high-level SVG path. See
`supplements/charts.md` for sizing tables and the native-chart
alternative.
