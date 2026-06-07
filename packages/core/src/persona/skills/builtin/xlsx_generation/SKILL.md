---
name: xlsx_generation
description: Produce a professional Excel workbook via openpyxl, executed in the code sandbox.
when_to_use: >
  Use when the user asks for a XLSX file, formatted excel workbook,
  or downloadable xlsx as a file (not text in the chat). Skip for plain-text replies
  or Markdown drafts. Compose with document_drafting (content) first if the user wants
  prose-then-format — the bridge is your own context, NOT a .md file read back in.
tools_required:
  - code_execution
---

# XLSX Generation

Produce a professional Excel workbook with `openpyxl` inside the
`code_execution` sandbox. The skill encodes the structural, formatting,
and formula craft that distinguishes a workbook a user will actually use
from a generated-and-ugly one.

If `persona.identity.visual_style` is set, prefer those aesthetic hints
(colour palette, font preference, voice register) over the generic
defaults below; otherwise use format defaults.

## When to use

Activate for "build a budget spreadsheet", "produce an xlsx with monthly
figures and a summary sheet", "give me a workbook with formulas". Skip
for plain text (use `document_drafting`), CSV dumps (use `file_write`),
or chart-only requests (Spec 17).

For prose-then-format flows, draft with `document_drafting` first, keep
the prose in your own context, then activate this skill and embed prose
as Python strings. Do not write a `.md` file and read it back in.

## Environment

- Library: **`openpyxl==3.1.5`** (already in the sandbox image).
- Output path inside the sandbox: **`/workspace/out/<descriptive>.xlsx`**.
  Use lowercase, hyphenated names (`budget-2026.xlsx`). The runtime
  surfaces the file via `ExecutionResult.produced_files`.

## Procedure

### Step 1: Skeleton + descriptive sheet names

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from pathlib import Path

wb = Workbook()
del wb["Sheet"]  # drop the default "Sheet" — never ship "Sheet1"
months = wb.create_sheet("Months")
summary = wb.create_sheet("Summary")
```

Sheet names appear in cross-sheet formulas and in the user's tab bar —
**always descriptive**, never `Sheet1`/`Sheet2`.

### Step 2: Styled header row + freeze pane

```python
headers = ["Category", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Total"]
months.append(headers)

header_font = Font(bold=True, color="FFFFFF")
header_fill = PatternFill("solid", fgColor="305496")
for cell in months[1]:
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = Alignment(horizontal="center")

months.freeze_panes = "A2"
```

Every data sheet gets a styled header + a freeze-pane on row 2.

### Step 3: Data + per-row formulas (prefer formulas over values)

```python
categories = ["Rent", "Salaries", "Utilities", "Supplies"]
for row_idx, cat in enumerate(categories, start=2):
    months.cell(row=row_idx, column=1, value=cat)
    months.cell(row=row_idx, column=14,
                value=f"=SUM(B{row_idx}:M{row_idx})")
```

For currency / date / percent / number formats, read
`/workspace/in/.skills/xlsx_generation/supplements/formatting.md`. The
default `"General"` is wrong for almost every business workbook.

### Step 4: Cross-sheet formulas — the hard part

The Summary sheet pulls from Months. **Always reference the source sheet
by its descriptive name (`Months!`)**, never `Sheet1!`. The canonical
pattern is a `SUMIF` against the source-sheet category column:

```python
summary.append(["Category", "Year total"])
for row_idx, cat in enumerate(categories, start=2):
    summary.cell(row=row_idx, column=1, value=cat)
    summary.cell(
        row=row_idx, column=2,
        value=f"=SUMIF(Months!$A:$A, A{row_idx}, Months!$N:$N)",
    )
```

The formula reads: "sum values in `Months!$N:$N` (the year-total column)
where `Months!$A:$A` (the category column on Months) equals the category
in `A{row_idx}` (this row on Summary)."

`$A:$A` and `$N:$N` are **absolute column references** — they stay
correct when the formula is copied down rows. `A{row_idx}` is **relative**
so each Summary row picks up its own category. For deeper detail on
absolute vs relative refs, named ranges, and multi-condition `SUMIFS`,
read
`/workspace/in/.skills/xlsx_generation/supplements/formulas.md`.

### Step 5: Column widths

```python
for col in range(1, 15):  # A..N
    months.column_dimensions[get_column_letter(col)].width = 14
months.column_dimensions["A"].width = 22  # category names are wider
```

The default ~8.43-char column truncates currency to `###`. Always size.

### Step 6: Charts (optional)

Prefer **openpyxl-native charts** — data-linked, in-cell, update when the
user edits the source. Read
`/workspace/in/.skills/xlsx_generation/supplements/charts.md` for the
strong default; a raster fallback for embedding Spec 17 PNGs is at the
tail of that file.

### Step 7: Save

```python
out = Path("/workspace/out/budget-2026.xlsx")
out.parent.mkdir(parents=True, exist_ok=True)
wb.save(out)
print(f"wrote {out}")
```

Print the path so the user (and you, on the next turn) can see what
landed.

## Quality bar — verify before declaring done

- [ ] No `Sheet1`/`Sheet2`; every sheet has a descriptive name.
- [ ] Header row styled (bold + fill) on every data sheet.
- [ ] `freeze_panes` set on every data sheet (usually `"A2"`).
- [ ] Currency / date / percent cells: `number_format != "General"`.
- [ ] Cross-sheet formulas reference the source sheet by **name**, not
      `Sheet1!`.
- [ ] Aggregation cells are **formulas**, not constants.
- [ ] Column widths set explicitly.
- [ ] File saved under `/workspace/out/` and the path printed.

## Failure modes

**The "Sheet1" trap.** Forgetting `del wb["Sheet"]` ships an empty
default sheet. Always drop it.

**The hard-coded total trap.** Computing the sum in Python and writing
the result as a value: right today, wrong the moment the user edits.
**Always write the formula.**

**The cross-sheet `#REF!` trap.** Writing `=Sheet1!B2` then renaming
`Sheet1` to `Months` breaks the formula. Reference by the final
descriptive sheet name (or a named range — see `formulas.md`).

**The `"General"` format trap.** A currency column showing `42` instead
of `42,00 kr` reads as broken. Apply `number_format`.

**The narrow-column trap.** Default width truncates to `###`. Size
columns.

**The "open in Excel and it's blank" trap.** Forgetting `wb.save(path)`
or saving outside `/workspace/out/`. The runtime surfaces only files
under that root.

## On supplements

For depth on the three hardest sub-topics, read the matching supplement
from inside your sandbox code **on demand** — do not pre-load all three:

- Formulas → `/workspace/in/.skills/xlsx_generation/supplements/formulas.md`
- Formatting → `/workspace/in/.skills/xlsx_generation/supplements/formatting.md`
- Charts → `/workspace/in/.skills/xlsx_generation/supplements/charts.md`

The runtime stages these when you activate this skill. If a read raises
`FileNotFoundError`, the supplement is not staged (M0 fallback) —
engineer from the body alone.
