# persona-voice

> Real-time voice service for Open Persona. WebRTC transport via LiveKit OSS.
> Source-available; noncommercial use only.

**Status:** PolyForm Noncommercial 1.0.0 · Source Available (Noncommercial Use Only) · V1–V3 shipped; V4–V6 pending

## What it is

`persona-voice` is the real-time voice trunk: a LiveKit OSS substrate, a
WebRTC transport facade, a session lifecycle state machine, a
streaming-loop skeleton with V2 / V3 / V4 / V5 Protocol seams, per-user
advisory-lock concurrency, and a structured `VoiceLog`. It runs in-process
with `persona-core` (no separate language, no cross-process IPC) so the
typed-memory stores, audit log, and credits service compose directly.

Shipped sub-trunks:

- **V1, WebRTC transport** (`persona-voice 0.1.0`): LiveKit OSS substrate
  via `livekit>=1.1`, `POST /v1/voice/token` JWT-authed AccessToken
  endpoint, `VoiceRoom` facade with inbound resample to canonical PCM16
  mono 16 kHz + outbound 24 kHz publish, `Session` state machine, per-user
  voice-call concurrency via `pg_try_advisory_xact_lock`, full-duplex
  binary criterion proven on live LiveKit Server.
- **V2, Streaming STT** (`persona-voice 0.V2.0`): provider-independent
  `StreamingSTT` Protocol mirroring Spec 02 `ChatBackend`, Deepgram Nova-3
  concrete backend, Silero VAD ONNX-only adapter with mandatory
  `SileroFramer` reframer, V1 `STTStream` seam adapter, `VoiceLog`
  extended with 4 additive STT fields (`stt_partial_first_at`,
  `stt_audio_pushed_at`, `stt_provider_cost_cents_per_minute`,
  `stt_total_cents`), content-hash-only audit, `PERSONA_STT_*` env block.
- **V3, Streaming TTS**: provider-independent `StreamingTTS` Protocol,
  Cartesia concrete backend (`cartesia[websockets]>=3,<4`), voice
  resolution from persona schema, V1 outbound-rail seam adapter, mid-
  utterance `cancel()` with discard-on-cancel for the future V4 barge-in
  foundation, in-process integration spine through STT → mocked-V5 →
  TTS → outbound.

Not yet shipped (sub-trunks in research / planning):

- **V4, Turn-taking + barge-in**: interrupt handling, end-of-utterance
  detection, lifecycle hooks on `SessionEventListener`.
- **V5, Model reply producer**: streams runtime token output into V3 with
  the canonical first-token-latency measurement convention.
- **V6, Frontend voice experience**: the browser-side audio plumbing
  and UI in `persona-web`.

## Install

From PyPI (planned, once V4–V6 close):

```bash
pip install persona-voice
```

Workspace development:

```bash
git clone https://github.com/yasinhessnawi1/Open-Persona.git
cd open-persona
uv sync --all-packages
```

Prerequisites for V2 / V3 wire behaviour: a Deepgram API key
(`PERSONA_STT_API_KEY`) and a Cartesia API key
(`PERSONA_TTS_API_KEY`). The in-process integration spines run without
real provider connectivity.

## Run

`persona-voice` is a service consumed by `persona-api`; there is no
standalone CLI. The token-issuance HTTP app boots from
`persona_voice.http.app`:

```bash
uv run uvicorn persona_voice.http.app:create_app --factory --port 8001
```

You also need a running LiveKit OSS Server (see `docker-compose.yml`)
and the persona-api (`POST /v1/voice/token` is the production entry
point; the persona-voice route exists for development).

## Test

```bash
uv run pytest packages/voice                            # unit (default)
uv run pytest packages/voice -m integration             # live LiveKit + Postgres
uv run pytest packages/voice -m external                # live Deepgram / Cartesia
uv run mypy packages/voice/src
uv run ruff check packages/voice
```

The integration tests bring up a real LiveKit Server and prove full-
duplex (V1), STT pipe (V2), and end-to-end TTS through the V1 outbound
rail (V3). External smoke tests are skipped unless the provider key env
vars are set.

## Architecture role

`persona-voice` sits beside `persona-runtime` as a sibling consumer of
`persona-core`. It does not depend on `persona-runtime` and is not
imported by it; voice routes through the API which composes both. The
voice trunk owns: the LiveKit substrate, audio frame plumbing, the
streaming STT and TTS Protocols + concrete backends, the session
lifecycle, voice-call concurrency, and the additive `VoiceLog`. Per-
minute billing, V4 turn-taking, V5 model wiring, and the V6 frontend
land post-V3.

## Contribute

Contributions welcome under the same PolyForm Noncommercial 1.0.0 license.
The package is source-available for noncommercial use; commercial use
requires a separate license (contact the rights holder). Issues and pull
requests welcome at
[github.com/yasinhessnawi1/Open-Persona](https://github.com/yasinhessnawi1/Open-Persona).
See [CHANGELOG.md](CHANGELOG.md) for the spec-by-spec history.
