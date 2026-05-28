---
name: document_drafting
description: Draft a structured document from gathered material or user input, applying a consistent outline-then-section workflow.
when_to_use: >
  Use this skill when the user asks to write, draft, or produce a
  document, report, memo, letter, summary, or other prose artefact
  longer than a paragraph. Skip for quick replies, chat responses, or
  short factual answers.
tools_required:
  - file_write
---

# Document Drafting

A procedure for producing structured documents — reports, memos,
letters, summaries, briefings. Use it when the output is longer than a
short reply and benefits from being saved to a file.

## When to use

Activate this skill for requests like:

- "Write a report on X."
- "Draft a complaint letter."
- "Produce a summary of the meeting notes."
- "Write a memo proposing Y."

Skip this skill for:

- Inline conversational replies.
- Single-paragraph answers.
- Code (use the relevant coding skill or write inline).

## Procedure

### Step 1: Confirm scope

Before writing, confirm:

- **Audience.** Who reads this? A casual reader, a domain expert, a
  busy executive, a regulator? Voice and depth depend on the answer.
- **Length.** Short (under 500 words), medium (500-2000), long (2000+)?
- **Format.** Plain prose, headers + sections, bullet points, table-heavy?
- **Stance.** Neutral overview, recommendation, advocacy?

If any are unclear and the user is reachable, ask. If they're not, pick
a sensible default and state your assumption at the top of the draft.

### Step 2: Build an outline

Write the outline before any prose:

1. **Title** — descriptive, not clever.
2. **Lede / opening** — what the document is about, in one sentence.
3. **Section headers** — 3-7 top-level sections for most documents. More
   than 7 → the document is too long; split into multiple documents or
   merge related sections.
4. **Per-section bullet points** — what each section will cover, as 2-4
   bullets. Not prose yet — just the points.
5. **Closing** — recommendation, summary, or call to action.

Review the outline as a whole. Does the order make sense? Does each
section advance the document? Cut redundant sections.

### Step 3: Draft per section

Now write each section. Apply these rules:

- **Topic sentence first.** Each paragraph leads with its main point.
- **One idea per paragraph.** If a paragraph has two ideas, split it.
- **Active voice.** "The committee decided X" beats "X was decided by
  the committee."
- **Concrete examples.** Abstractions are easier to read with one
  concrete example each.
- **Cut adjectives.** "Very", "really", "extremely", "quite" — almost
  always removable without loss.

### Step 4: Review and revise

Read the draft end to end before saving. Look for:

- **Repetition.** Did you say the same thing in two places?
- **Contradictions.** Does section 3 disagree with section 5?
- **Holes.** Did you promise to cover X in the outline and forget?
- **Tone consistency.** Does the voice shift mid-document?
- **Citations / sources.** If the document cites external material, are
  the citations consistent in format?

Fix obvious issues. Mark issues you can't fix without more information
as `[NEEDS: ...]` so the user can resolve them.

### Step 5: Save

Call `file_write` to save the document. Filename: descriptive, lowercase,
with hyphens. Examples: `tenancy-complaint-draft.md`,
`weekly-status-2026-w22.md`, `proposal-x-feature-y.md`.

## Quality checks

Before declaring done:

- [ ] Outline exists and was followed.
- [ ] Title, lede, sections, closing all present.
- [ ] Each section earns its place.
- [ ] No `[NEEDS: ...]` markers without flagging them to the user.
- [ ] Filename is descriptive.

## Failure modes

**The "draft as outline" trap.** You write the outline and then expand
each bullet to a paragraph mechanically. The result is wooden. Take
each section as its own writing task — the outline is scaffolding, not
the final structure.

**The "burying the lede" trap.** You spend three paragraphs on
background before getting to the point. Move the point up. Background
goes after the conclusion, not before.

**The "I think we should" trap.** First-person hedging in a document
that should be authoritative. Either commit to the recommendation or
present options neutrally — don't squat between.

## Templates

For common document types, use these starting structures:

### Memo

```
TO: <audience>
FROM: <author>
DATE: <yyyy-mm-dd>
RE: <one-line subject>

[Opening — what this memo is about, one sentence.]

## Background
[What context the reader needs.]

## Findings / Analysis
[The substantive content.]

## Recommendation
[What you propose.]
```

### Report

```
# <Title>

## Executive summary
[One paragraph; the whole document compressed.]

## Background
## Methods
## Findings
## Discussion
## Recommendations
## Appendix (if any)
```

### Letter (formal)

```
<Recipient address>
<Date>

Dear <Salutation>,

[Opening paragraph — purpose of letter.]
[Body paragraphs — substance.]
[Closing paragraph — what you're asking for or what happens next.]

Sincerely,
<Signature line>
```

Use these as starting points; deviate when the situation requires.
