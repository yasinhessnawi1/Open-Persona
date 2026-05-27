"""PersonaGym loader.

Vendors the benchmark from https://github.com/vsamuel2003/PersonaGym (MIT)
into ``benchmarks_data/personagym/``. The on-disk layout we expect after
vendoring::

    benchmarks_data/personagym/
    ├── personas.json           # list[str], 200 free-form persona descriptions
    └── questions/
        └── <persona_text>.json # {task_name: [10 questions]} for 5 task names

The natural-language persona strings do not map cleanly to our typed
``Persona`` schema. We use a documented, lossy rule-based mapper that
extracts:

- ``identity.name`` — synthesised (e.g. "PersonaGym persona #042"); the
  source string carries no name.
- ``identity.role`` — the source string verbatim (≤ 200 chars trimmed).
- ``identity.background`` — the source string verbatim (capped to fit).
- ``self_facts`` — light heuristic split on commas / ``and`` / phrases
  that read as discrete biographical claims, prefixed with "I ".
- ``worldview`` — claims after ``advocating``, ``promoting``, ``passionate
  about``, ``fighting for`` style verbs become belief-tagged worldview
  entries; otherwise empty.
- ``constraints`` — empty (PersonaGym does not supply constraints).

The mapping is deliberately conservative: it preserves the source string
verbatim in ``background`` so the report can show any reviewer the same
text the original benchmark reasoned over.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from loguru import logger

from persona_rag.benchmarks.base import BenchmarkConversation
from persona_rag.schema.persona import (
    BACKGROUND_MAX,
    Persona,
    PersonaIdentity,
    SelfFact,
    WorldviewClaim,
)

BENCHMARK_NAME = "personagym"

PERSONAGYM_TASKS = (
    "Expected Action",
    "Toxicity",
    "Linguistic Habits",
    "Persona Consistency",
    "Action Justification",
)

# The advocacy / belief verbs PersonaGym authors lean on heavily. Used to split
# off worldview claims from the role/background text. Pattern boundaries: word
# boundaries on each side; case-insensitive.
_WORLDVIEW_VERBS = (
    "advocating for",
    "advocating",
    "promoting",
    "passionate about",
    "fighting for",
    "fighting against",
    "preserving",
    "supporting",
    "raising awareness",
    "championing",
    "combating",
)
_WORLDVIEW_RE = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in _WORLDVIEW_VERBS) + r")\s+([^,;.]+)",
    re.IGNORECASE,
)

# Light split for self-facts. PersonaGym strings are typically a comma-joined
# clause structure: "A 71-year-old retired nurse from Italy, volunteering in
# hospice care and advocating for compassionate end-of-life support". We split
# on commas and the ``ing``-verb continuations.
_SELF_FACT_SPLIT_RE = re.compile(r",\s+| who | from ", re.IGNORECASE)


def _slugify(text: str, *, max_len: int = 80) -> str:
    """ASCII-slug the persona text for use as a filename / persona_id stem."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text)
    cleaned = cleaned.strip("_").lower()
    return cleaned[:max_len] or "persona"


def _split_self_facts(persona_text: str, *, max_facts: int = 4) -> list[str]:
    """Heuristic clause split for self-facts. Each fact is prefixed with 'I '."""
    pieces = [p.strip() for p in _SELF_FACT_SPLIT_RE.split(persona_text) if p.strip()]
    facts: list[str] = []
    for piece in pieces[:max_facts]:
        # Drop articles and "X-year-old" prefixes so the result reads as a
        # first-person statement.
        cleaned = re.sub(r"^(A|An|The)\s+", "", piece, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\d+[-\s]?year[-\s]?old\s+", "", cleaned, flags=re.IGNORECASE)
        if not cleaned:
            continue
        # Strip trailing connective fragments (e.g. ", and").
        cleaned = re.sub(r"^(and|who|from)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.rstrip(".").strip()
        if not cleaned:
            continue
        # Tense match: "I am a ..." for noun phrases, "I ..." for verb phrases.
        first_word = cleaned.split()[0].lower()
        if first_word.endswith("ing"):
            facts.append(f"I am {cleaned}.")
        else:
            facts.append(f"I am a {cleaned}." if first_word.isalpha() else f"I {cleaned}.")
    return facts


def _split_worldview(persona_text: str) -> list[tuple[str, str]]:
    """Return ``[(domain, claim_text)]`` pairs for advocacy/belief verbs."""
    out: list[tuple[str, str]] = []
    for match in _WORLDVIEW_RE.finditer(persona_text):
        verb = match.group(1).lower().split()[0]
        body = match.group(2).strip()
        if not body:
            continue
        # Domain is a coarse bucket from the verb family.
        if verb.startswith("advoc"):
            domain = "advocacy"
        elif verb.startswith("promot"):
            domain = "promotion"
        elif verb.startswith("fight"):
            domain = "activism"
        elif verb.startswith("preserv"):
            domain = "preservation"
        elif verb.startswith("support"):
            domain = "support"
        elif verb.startswith("combat"):
            domain = "activism"
        elif verb.startswith("rais") or verb.startswith("champion"):
            domain = "advocacy"
        else:
            domain = "belief"
        out.append((domain, f"I am {match.group(0).lower()}."))
    return out


def map_persona_string_to_typed(
    persona_text: str,
    *,
    persona_id: str | None = None,
    sequence_number: int | None = None,
) -> Persona:
    """Convert one PersonaGym free-form string into our typed ``Persona``.

    Lossy by design — see module docstring. ``persona_id`` defaults to a
    slug of ``persona_text`` (truncated to 80 chars).
    """
    persona_text = persona_text.strip()
    if not persona_text:
        raise ValueError("PersonaGym persona text cannot be empty")
    pid = persona_id or _slugify(persona_text)
    name = (
        f"PersonaGym persona #{sequence_number:03d}"
        if sequence_number is not None
        else f"PersonaGym persona {pid}"
    )
    role = persona_text[:200].rstrip()
    background = persona_text[:BACKGROUND_MAX]

    self_fact_strs = _split_self_facts(persona_text)
    self_facts = [SelfFact(fact=f, confidence=0.9) for f in self_fact_strs]

    worldview_claims: list[WorldviewClaim] = []
    for domain, claim_text in _split_worldview(persona_text):
        worldview_claims.append(
            WorldviewClaim(
                claim=claim_text,
                domain=domain,
                epistemic="belief",
                valid_time="always",
                confidence=0.7,
            )
        )

    identity = PersonaIdentity(
        name=name,
        role=role,
        background=background,
        constraints=[],  # PersonaGym does not supply constraints
    )
    return Persona(
        persona_id=pid,
        identity=identity,
        self_facts=self_facts,
        worldview=worldview_claims,
        episodic=[],
    )


def _safe_question_filename(persona_text: str) -> str:
    """PersonaGym uses the persona string itself as the filename; reproduce that."""
    # The repo ships these with literal commas / spaces / apostrophes, but no
    # path separators or NUL — passing them through is fine on POSIX.
    return persona_text + ".json"


def load_personagym(
    root: Path,
    *,
    n_personas: int = 50,
    n_questions_per_persona: int = 10,
    seed: int = 42,
    tasks: tuple[str, ...] = PERSONAGYM_TASKS,
) -> tuple[list[Persona], list[BenchmarkConversation]]:
    """Load a deterministic sample of PersonaGym.

    Returns ``(personas, conversations)``. Each conversation is single-turn
    (one user query, no probe). Sampling: random seeded subset of
    ``n_personas`` personas; for each, ``n_questions_per_persona`` questions
    drawn round-robin across the configured tasks (so the sample is balanced
    across the five task axes).

    Vendored layout::

        <root>/personas.json          list[str]
        <root>/questions/<text>.json  {task: [str, ...]}
    """
    root = Path(root)
    personas_path = root / "personas.json"
    questions_dir = root / "questions"
    if not personas_path.exists():
        raise FileNotFoundError(
            f"PersonaGym personas list not found at {personas_path}; "
            "vendor with `scripts/fetch_personagym.py` first"
        )
    if not questions_dir.exists():
        raise FileNotFoundError(
            f"PersonaGym questions dir not found at {questions_dir}; "
            "vendor with `scripts/fetch_personagym.py` first"
        )

    all_strings: list[str] = json.loads(personas_path.read_text(encoding="utf-8"))
    if n_personas > len(all_strings):
        n_personas = len(all_strings)
    rng = random.Random(seed)
    sampled_indices = sorted(rng.sample(range(len(all_strings)), n_personas))

    personas: list[Persona] = []
    conversations: list[BenchmarkConversation] = []
    for ix in sampled_indices:
        text = all_strings[ix]
        persona = map_persona_string_to_typed(text, sequence_number=ix)
        personas.append(persona)

        q_path = questions_dir / _safe_question_filename(text)
        if not q_path.exists():
            logger.warning("PersonaGym: missing questions file for #{}: {}", ix, q_path)
            continue
        questions: dict[str, list[str]] = json.loads(q_path.read_text(encoding="utf-8"))
        # Round-robin draw across configured tasks.
        ordered: list[tuple[str, str]] = []  # (task, question)
        per_task: dict[str, list[str]] = {t: list(questions.get(t, [])) for t in tasks}
        while len(ordered) < n_questions_per_persona and any(per_task.values()):
            for t in tasks:
                if not per_task[t]:
                    continue
                ordered.append((t, per_task[t].pop(0)))
                if len(ordered) >= n_questions_per_persona:
                    break
        for q_ix, (task, question) in enumerate(ordered):
            conv_id = f"{persona.persona_id}::pg_{ix:03d}::q_{q_ix:02d}"
            conversations.append(
                BenchmarkConversation(
                    conversation_id=conv_id,
                    persona_id=persona.persona_id or _slugify(text),
                    benchmark=BENCHMARK_NAME,
                    user_turns=[question],
                    probe=None,
                    notes=f"task={task}",
                )
            )

    logger.info(
        "PersonaGym: sampled {} personas x {} questions each = {} conversations (seed={})",
        len(personas),
        n_questions_per_persona,
        len(conversations),
        seed,
    )
    return personas, conversations


def index_personagym(
    raw_personas_path: Path,
    raw_questions_dir: Path,
    *,
    target_root: Path,
) -> None:
    """Vendor a fresh PersonaGym checkout into ``target_root``.

    Convenience for the one-shot fetch script. Does no schema mutation; just
    copies ``personas.py`` extraction (as JSON) + the per-persona question
    JSONs into the layout ``load_personagym`` expects. Pure file IO; safe to
    re-run.
    """
    target_root = Path(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "questions").mkdir(parents=True, exist_ok=True)
    raw_personas_path = Path(raw_personas_path)
    raw_questions_dir = Path(raw_questions_dir)

    if raw_personas_path.suffix == ".py":
        # Extract `benchmark_personas` from the source file by parsing it as
        # Python — safer than regex for embedded quotes.
        source = raw_personas_path.read_text(encoding="utf-8")
        ns: dict[str, Any] = {}
        exec(compile(source, str(raw_personas_path), "exec"), ns)
        personas: list[str] = ns["benchmark_personas"]
    else:
        personas = json.loads(raw_personas_path.read_text(encoding="utf-8"))

    (target_root / "personas.json").write_text(
        json.dumps(personas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    n_qfiles = 0
    for src in raw_questions_dir.glob("*.json"):
        (target_root / "questions" / src.name).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        n_qfiles += 1
    logger.info(
        "PersonaGym: vendored {} personas + {} question files to {}",
        len(personas),
        n_qfiles,
        target_root,
    )


__all__ = [
    "BENCHMARK_NAME",
    "PERSONAGYM_TASKS",
    "index_personagym",
    "load_personagym",
    "map_persona_string_to_typed",
]
