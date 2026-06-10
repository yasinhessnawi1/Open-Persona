# Changelog — persona-voice

All notable changes to `persona-voice` are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per-spec entries are added by the close-out phase of each spec. The authoritative
project-wide changelog lives at [`/CHANGELOG.md`](../../CHANGELOG.md); this file
mirrors only the `persona-voice`-touching surface.

---

## [Unreleased]

(empty — future post-v0.1 work lands here)

---

## [0.1.0] — 2026-06-07

> **First release of `persona-voice` — the voice trunk.** A 4th uv workspace package + LiveKit OSS substrate + WebRTC transport facade + session lifecycle + streaming-loop skeleton with V2/V3/V4/V5 Protocol seams + advisory-lock per-user concurrency + VoiceLog instrumentation. Branch (A) per D-V1-1 (R-V1-1 ruled out aiortc on documented 17–20× latency overhead). **84 voice unit + 5 voice integration tests against live LiveKit Server + Postgres + 10 persona-core auth tests from the T03 extraction.** Binary criterion #3 (full-duplex) **structurally proven** via live LiveKit Server.

### Added (Spec V1 — Real-Time Voice Service and WebRTC Transport, Phase 6 complete)

- **`packages/voice/` as 4th uv workspace member** with `persona-core[postgres]` + `livekit>=1.1,<2` + `livekit-api>=1.1,<2` deps (both Apache-2.0 — D-V1-X-livekit-sdk-license-stack confirmed via PyPI). Root `pyproject.toml` extended with mypy_path + pytest testpaths + `livekit.*` mypy-override + per-file ruff ignore. Root `conftest.py` adds `packages/voice/src` for the editable-`.pth` iCloud-hidden-flag workaround.
- **Token-issuance endpoint** at [`src/persona_voice/http/app.py`](src/persona_voice/http/app.py) — `POST /v1/voice/token` (JWT-authed via the extracted persona-core verifier); checks persona ownership via the configured DB; mints a LiveKit `AccessToken` (room=`persona:<session_id>`; identity=user_id; metadata={persona_id, conversation_id, session_id}; TTL 10min default); returns `{token, room_name, livekit_url}`. RLS-shape 404 on cross-tenant.
- **`VoiceRoom` facade over `livekit.rtc.Room`** at [`src/persona_voice/transport/room.py`](src/persona_voice/transport/room.py) — connect / disconnect / `track_subscribed` → `InboundAudioFrame` drain (resampled to canonical PCM16 mono 16 kHz per D-V1-6) / `publish_outbound` (PCM16 mono 24 kHz) / `capture_outbound_frame` / `RoomSubstrate: Protocol` for test substrate injection. `build_voice_room()` is the production constructor.
- **Session lifecycle state machine** at [`src/persona_voice/session/state_machine.py`](src/persona_voice/session/state_machine.py) — `Session` frozen Pydantic v2 + `SessionState = Literal["created","active","ended"]` + `SessionLifecycleEvent` StrEnum (7 V4-aligned values) + `SessionEventListener` Protocol + `InvalidSessionStateError`. `make_session_rls_engine(url, user_id)` is the per-session RLS engine (D-V1-X-rls-engine-shape, pool_size=1, user_id baked into checkout listener). `attach_to_room(voice_room)` wires `Room.on('disconnected')` → `end()` → engine.dispose → advisory-lock release via tx rollback.
- **Streaming-loop skeleton with V2/V3/V4/V5 Protocol seams** at [`src/persona_voice/loop/streaming.py`](src/persona_voice/loop/streaming.py) — `STTStream` (V2 push) / `TTSStream` (V3 + cancel for V4 barge-in) / `ModelReplyProducer` (V5) / `PassThroughEchoMode` StrEnum (ECHO/DISABLED). V2→V5→V3 pipeline runs as `asyncio` Task; D-V1-6 sample-rate guard raises on mismatch.
- **Per-user voice-call concurrency** at [`src/persona_voice/concurrency.py`](src/persona_voice/concurrency.py) — `acquire_voice_call_concurrency(*, conn, user_id)` mirrors `imagegen/concurrency.py` verbatim per D-V1-X-d15x-precedent-binding. `pg_try_advisory_xact_lock(('x' || md5(:user_id))::bit(64)::bigint)` auto-releases on tx commit/rollback; multi-worker-correct from day one. `VoiceConcurrencyCappedError(PersonaError)` analogue maps to 429 + Retry-After at the endpoint integration site.
- **VoiceLog instrumentation** at [`src/persona_voice/logging.py`](src/persona_voice/logging.py) — frozen Pydantic v2 + `extra="forbid"` per D-05-9. LiveKit canonical hops (`eou_at` / `stt_final_at` / `llm_first_token_at` / `tts_first_byte_at` / `audio_first_play_at`) coordinated with Spec 18 D-18-X-first-token-measurement-impl per D-V1-X-first-token-measurement-coordination. V1's binding share (`transport_in_ms` / `transport_out_ms` / `loop_overhead_ms`; 100ms P50 / 150ms P95 CI gate). `JSONLVoiceLogWriter` durable per-write flush.
- **T08 binary criterion #3 PROVEN** at [`tests/integration/test_full_duplex.py`](tests/integration/test_full_duplex.py) — agent (persona-voice's `VoiceRoom`) + client (raw `rtc.Room`) join the same LiveKit Server Room; publish/subscribe a 2s sine tone in BOTH directions concurrently; both ends receive ≥10 frames at the canonical D-V1-6 rates. Full-duplex is structurally proven; V4 barge-in foundation is real, not aspirational.

### Added (Spec 19 amendment — chain entry 19)

- **L6b (chain 19) D-19-X-voice-token-credit-gate** — `POST /v1/voice/token` is gated by `credits_service.require_credits` per D-11-12 pattern: returns HTTP 402 + structured `credits_exhausted` body BEFORE the LiveKit `AccessToken` is minted. Closed-spec additive extension; no Spec 11 / Spec V1 reopen. Per-minute voice deduction lands post-v0.1 once the V5 stack proves call-length telemetry.

### Cross-spec coordination (Spec V1)

- **Spec 08 additive amendment (9th in chain)** — `make_jwt_verifier` + `AuthenticatedUser` extracted to `persona.auth.jwt_verifier`. persona-api re-exports; no test breakage. Per D-12-X / D-16-X precedent.
- **Spec 15 D-15-X-concurrency-cap precedent binding** — `acquire_voice_call_concurrency` is the verbatim mirror of `imagegen/concurrency.py`. The kickoff's "Postgres rate-limit table" generic lean was wrong; corrected at Phase 1 and locked at Phase 4.
- **Spec 18 D-18-X-first-token-measurement-impl coordination** — VoiceLog's `llm_first_token_at` field uses the same shape Spec 18 records at `runtime/loop.py:465-468`. One measurement convention, two producers, V5 reads from both.
- **Spec 11 D-11-1/2/3 hosting amendment** — `livekit-server` Go binary becomes a sidecar container in docker-compose; v0.1 single-VPS sizing reviewed at MAINTENANCE.md.
- **Architecture §10 voice OOS supersession** — line 769 "Voice. Out of scope for September." superseded by a new §11-equivalent voice-layer block. Additive precedent chain: 10th entry per D-V1-X-architecture-md-update.

### Decisions (Spec V1)

23 decisions locked at Phase 4 per [`docs/specs/phase2/spec_V1/decisions.md`](../../docs/specs/phase2/spec_V1/decisions.md). Headline: **D-V1-1 branch (A) — self-hosted LiveKit OSS Server + `livekit` low-level Python SDK + hand-implemented Protocols**. Rejected aiortc on R-V1-1's documented evidence (Issue #775 500–600ms LAN Opus latency 17–20× over the 30ms budget; Issue #505 SRTP blocks asyncio event loop; unfixed memory leaks); rejected language change because it defeats in-process persona-core access. Cloudflare Realtime TURN primary + Twilio NTS fallback (D-V1-2; $0 + $5/mo at v0.1 scale). All 23 mirrored to [`docs/DECISIONS.md`](../../docs/DECISIONS.md).
