"""Build-time voice auto-assignment (Issue 1, fail-soft).

At persona create the global TTS default is a single English voice, so a female
persona spoke with a male voice (and vice-versa). This service gives a freshly
created persona a *fitting* catalogue voice: a small-tier model reads the
persona's identity and the language-filtered voice catalogue and picks the voice
whose gender and character best match — the audible analogue of the avatar
auto-generation hook next to it in the create flow.

Everything fail-softs. A persona that cannot be voiced (TTS unconfigured, the
catalogue fetch fails, the model returns nothing usable, the builder already
chose a voice) keeps the global default — exactly the pre-Issue-1 behaviour, so
create never fails on a voice-pick problem.

The voice catalogue lives in the separate ``persona-voice`` service; this module
reaches it over its public ``GET /v1/voices`` endpoint, forwarding the caller's
bearer token (the same any-signed-in-user auth the web voice-selector uses), so
no service credential is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from persona.logging import get_logger
from persona.schema.conversation import ConversationMessage

from persona_api.services import persona_service

if TYPE_CHECKING:
    from persona.backends.protocol import ChatBackend
    from persona.schema.persona import Persona
    from starlette.requests import Request

__all__ = ["choose_voice", "maybe_assign_voice"]

_LOG = get_logger("api.voice_assignment")

#: Wall-clock bound on the cross-service catalogue fetch — create latency stays
#: bounded; a slow/absent voice service fail-softs to the global default.
_CATALOGUE_TIMEOUT_S = 10.0
#: Cap on how many voices reach the model prompt — keeps the prompt bounded for
#: a large provider catalogue while leaving a healthy gender mix to choose from.
_MAX_CATALOGUE = 60
#: The pick is a single voice_id; a tight cap keeps the call cheap.
_PICK_MAX_TOKENS = 64


@dataclass(frozen=True)
class _VoiceOption:
    """One catalogue voice as the picker sees it (the model-relevant fields)."""

    voice_id: str
    name: str
    gender: str
    description: str


def _pick_messages(persona: Persona, options: list[_VoiceOption]) -> list[ConversationMessage]:
    """Build the voice-pick prompt: persona identity + the compact catalogue."""
    catalogue = "\n".join(
        f"- {o.voice_id} | {o.gender} | {o.name}: {o.description}".rstrip(": ") for o in options
    )
    system = (
        "You assign a text-to-speech voice to an AI persona. You are given the "
        "persona's identity and a catalogue of available voices, each line as "
        "'voice_id | gender | name: description'. Choose the ONE voice whose "
        "gender and character best match the persona — match the persona's "
        "likely gender to the voice's gender above all else. Reply with ONLY the "
        "chosen voice_id, exactly as written, and nothing else."
    )
    user = (
        f"Persona name: {persona.identity.name}\n"
        f"Role: {persona.identity.role}\n"
        f"Background: {persona.identity.background}\n"
        f"Language: {persona.identity.language_default}\n\n"
        f"Available voices:\n{catalogue}\n\n"
        "Chosen voice_id:"
    )
    now = datetime.now(UTC)
    return [
        ConversationMessage(role="system", content=system, created_at=now),
        ConversationMessage(role="user", content=user, created_at=now),
    ]


def _extract_choice(text: str, valid_ids: list[str]) -> str | None:
    """Resolve the model's reply to a catalogue voice_id, or ``None``.

    Prefers an exact match (the instructed reply shape); falls back to any
    catalogue id that appears verbatim in the reply, so a chatty model that
    wraps the id in quotes or prose still resolves. Returns ``None`` when the
    reply names no known voice — the caller then leaves the persona unvoiced.
    """
    stripped = text.strip()
    for vid in valid_ids:
        if stripped == vid:
            return vid
    for vid in valid_ids:
        if vid in text:
            return vid
    return None


async def choose_voice(
    *, persona: Persona, backend: ChatBackend, options: list[_VoiceOption]
) -> str | None:
    """Ask ``backend`` to pick the best-fitting voice from ``options``.

    Pure of I/O beyond the model call — the orchestration seam the unit tests
    drive with a fake backend. Returns the chosen ``voice_id`` (guaranteed to be
    one of ``options``) or ``None`` when nothing usable came back.
    """
    catalogue = options[:_MAX_CATALOGUE]
    if not catalogue:
        return None
    response = await backend.chat(
        _pick_messages(persona, catalogue), temperature=0.0, max_tokens=_PICK_MAX_TOKENS
    )
    return _extract_choice(response.content, [o.voice_id for o in catalogue])


async def _fetch_catalogue(
    base_url: str, *, bearer: str | None, language: str | None
) -> tuple[str | None, list[_VoiceOption]]:
    """Fetch the language-filtered voice catalogue from the persona-voice service.

    Forwards the caller's bearer token to ``GET /v1/voices`` (the endpoint
    authorises any signed-in user). Returns ``(provider, options)``; ``provider``
    is ``None`` when TTS is unconfigured there (empty catalogue).
    """
    headers = {"authorization": bearer} if bearer else {}
    params = {"language": language} if language else {}
    url = f"{base_url.rstrip('/')}/v1/voices"
    async with httpx.AsyncClient(timeout=_CATALOGUE_TIMEOUT_S) as client:
        response = await client.get(url, headers=headers, params=params)
    response.raise_for_status()
    data = response.json()
    provider = data.get("provider")
    options = [
        _VoiceOption(
            voice_id=str(entry["voice_id"]),
            name=str(entry.get("name") or ""),
            gender=str(entry.get("gender") or "unspecified"),
            description=str(entry.get("description") or ""),
        )
        for entry in data.get("voices", [])
        if isinstance(entry, dict) and entry.get("voice_id")
    ]
    return provider, options


async def maybe_assign_voice(
    request: Request, *, owner_id: str, persona_id: str, yaml_str: str
) -> None:
    """Auto-assign a fitting voice to a freshly created persona (fail-soft).

    A no-op when the feature is unconfigured (no ``voice_service_url``), the tier
    registry is absent, the persona already declares a voice (the builder chose —
    never overridden), the catalogue is empty/unreachable, or the model returns
    nothing usable. Never raises into the create path — a voice-pick problem must
    never break persona creation (the avatar hook's fail-soft contract).
    """
    state = request.app.state
    config = getattr(state, "config", None)
    base_url = getattr(config, "voice_service_url", "") if config is not None else ""
    registry = getattr(state, "tier_registry", None)
    rls_engine = getattr(state, "rls_engine", None)
    if not base_url or registry is None or rls_engine is None:
        return

    try:
        persona = persona_service.load_persona_from_yaml(
            yaml_str, persona_id=persona_id, owner_id=owner_id
        )
    except Exception:  # noqa: BLE001 — create already validated; defensive only
        return
    if persona.identity.voice is not None:
        return  # the builder picked a voice — auto-pick never overrides it

    bearer = request.headers.get("authorization")
    try:
        provider, options = await _fetch_catalogue(
            base_url, bearer=bearer, language=persona.identity.language_default
        )
    except Exception as exc:  # noqa: BLE001 — network/provider error → keep default
        _LOG.info("voice auto-pick skipped: catalogue unavailable", persona_id=persona_id)
        _LOG.debug("voice catalogue fetch error", error=str(exc)[:200])
        return
    if provider is None or not options:
        return

    try:
        backend = registry.get(getattr(config, "voice_pick_tier", "small"))
        choice = await choose_voice(persona=persona, backend=backend, options=options)
    except Exception as exc:  # noqa: BLE001 — model/routing error → keep default
        _LOG.warning("voice auto-pick failed at model selection", persona_id=persona_id)
        _LOG.debug("voice pick model error", error=str(exc)[:200])
        return
    if choice is None:
        return

    try:
        persona_service.set_voice(
            rls_engine=rls_engine, persona_id=persona_id, provider=provider, voice_id=choice
        )
    except Exception as exc:  # noqa: BLE001 — persist error → keep default
        _LOG.warning("voice auto-pick failed to persist", persona_id=persona_id)
        _LOG.debug("voice persist error", error=str(exc)[:200])
        return
    _LOG.info("voice auto-assigned", persona_id=persona_id, provider=provider, voice_id=choice)
