"""Deterministic avatar-prompt crafter (Spec 29 T1, D-29-1).

Turns a persona's *declared* identity into a role-anchored, demographic-safe,
PII-free image prompt for build-time avatar auto-generation. The crafted
prompt flows through the same Spec 15 defences as any ``generate_image``
call (the :mod:`persona.imagegen.safety` hard-line filter + provider
moderation); this module's contract is that a well-formed persona yields a
prompt that passes the hard line cleanly.

**The field map (D-29-1 — the load-bearing safety decision).** There are
no gender/age/appearance fields in the schema and Spec 29 adds none, so the
crafter is deliberate about which declared field feeds which part of the
prompt:

* ``role`` → the **professional anchor** (the subject of the portrait).
  ``role`` is ``Field(min_length=1)`` so it is always present.
* ``visual_style`` → **THE declared-appearance channel.** This is the
  *only* place apparent gender / age / appearance may enter the prompt. A
  builder who declares "a woman in her 40s, warm photographic style" in
  ``visual_style`` gets it reflected — because it was *declared*, not
  inferred. Routed through the existing :func:`merge_visual_style`.
* ``background`` → **NOT parsed.** Free-prose extraction is exactly the
  demographic-leakage vector D-29-1 forbids ("background states an age"),
  so the crafter never reads ``background`` into the image prompt. ``role``
  already carries the professional anchor + specialty.
* ``name`` → **OMITTED.** You cannot name-stereotype a name that is not
  there (the "David → male, 35" failure); omission also keeps the prompt
  PII-free (an *archetype*, not a named individual).
* ``constraints`` → not used (behavioural, not visual).

The crafter **never** injects diversity / counter-stereotype specifiers
("diverse", "of any ethnicity") — the bias literature shows they are
model-specific and overcorrect (research §2.2.2). Where nothing is
declared it stays **silent** on demographics and conservative + role-
anchored, letting the provider's own distribution stand rather than
steering it with a guess.

**Determinism.** The single public function :func:`craft_avatar_prompt`
is pure: no seed, no randomness, no timestamp, no I/O, no model call. The
same :class:`~persona.schema.persona.PersonaIdentity` always yields a
byte-identical prompt, so a rebuild reproduces the prompt and the content-
addressed ``blake2b`` persist (Spec 15) collapses identical bytes — the
avatar is a stable function of the persona.

References:
    docs/specs/phase2/spec_29/decisions.md D-29-1;
    docs/specs/phase2/spec_29/research.md §2.2.1–§2.2.3;
    docs/specs/phase2/spec_29/spec_29_avatar_generation.md §2.1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.imagegen._merge import merge_visual_style

if TYPE_CHECKING:
    from persona.schema.persona import PersonaIdentity

__all__ = ["craft_avatar_prompt"]


#: Fixed portrait scaffolding appended after the role anchor. These are
#: demographic-silent professional-headshot quality descriptors drawn from
#: the headshot-prompt prior art (research §2.2.2) — attire framing, a
#: neutral background, and soft lighting. They contain no minor / sexual /
#: demographic vocabulary, so they cannot contribute to a hard-line
#: co-occurrence trigger (D-29-6).
_PORTRAIT_SCAFFOLD: str = "professional attire, neutral studio background, soft even lighting"

#: Fallback subject when ``role`` normalises to empty (a whitespace-only
#: authored role still satisfies ``min_length`` but carries no content).
#: Conservative + role-anchored + demographic-silent.
_FALLBACK_ROLE: str = "a professional"


def _normalise_role(role: str) -> str:
    """Collapse whitespace in the declared role; fall back if empty.

    Whitespace normalisation gives stable, byte-identical output for
    trivially-different inputs; the fallback guards the degenerate
    whitespace-only authored role. No casing change is applied — image
    models are case-insensitive and lowering would mangle proper nouns
    (``"Norwegian"``).

    Args:
        role: The persona's ``identity.role`` (``min_length=1``).

    Returns:
        The role with internal whitespace runs collapsed to single
        spaces and surrounding whitespace stripped, or
        :data:`_FALLBACK_ROLE` if nothing remains.
    """
    collapsed = " ".join(role.split())
    return collapsed or _FALLBACK_ROLE


def craft_avatar_prompt(identity: PersonaIdentity) -> str:
    """Craft a deterministic, demographic-safe avatar prompt (D-29-1).

    Builds a role-anchored professional portrait from the persona's
    *declared* identity and merges the declared ``visual_style``
    appearance channel via :func:`merge_visual_style`. The ``name`` is
    omitted and ``background`` is not parsed (see the module docstring for
    the full field map and the safety rationale).

    Args:
        identity: The persona's :class:`~persona.schema.persona.PersonaIdentity`.
            Only ``role`` and ``visual_style`` influence the output;
            ``name`` / ``background`` / ``constraints`` are deliberately
            ignored.

    Returns:
        The image prompt to pass to the Spec 15 imagegen pipeline. Pure
        function of ``identity`` — byte-identical across calls with equal
        input, with no demographic token unless one is declared in
        ``visual_style``.
    """
    role = _normalise_role(identity.role)
    base = f"a professional headshot portrait representing the role of {role}, {_PORTRAIT_SCAFFOLD}"
    return merge_visual_style(base, identity.visual_style)
