"""Domain exceptions for the voice persona-conditioning thread (spec V5).

Per ``ENGINEERING_STANDARDS`` §2.1, domain logic raises domain exceptions, not
bare ``ValueError`` / ``RuntimeError``. These subclass persona-core's
:class:`persona.errors.PersonaError` so they carry the structured
``context: dict[str, str]`` log payload the rest of the platform expects.
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = ["VoiceIntegrationError"]


class VoiceIntegrationError(PersonaError):
    """A voice persona-conditioning integration invariant was violated.

    The base error for the ``persona_voice.model`` thread — e.g. a
    :class:`~persona_voice.model.turn_context.VoiceTurnContext` constructed
    without one of the four required typed memory stores (the persona could not
    be conditioned, which would be a persona-bypass — spec V5 §8).
    """
