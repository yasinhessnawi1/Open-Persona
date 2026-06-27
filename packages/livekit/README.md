# `open-persona-livekit` — self-hosted LiveKit SFU (Fly.io)

Self-hosts the LiveKit OSS media server as a **separate Fly app** so prod voice
stops depending on LiveKit Cloud. Audio-only. This is the **same** server image
(`livekit/livekit-server:v1.13.1`) the community edition already runs locally via
`docker-compose.yml` + `livekit.dev.yaml` — one config story, two deploy targets.

> **Status: PREPARED, NOT DEPLOYED.** The config here is research-backed but
> WebRTC-on-Fly is proven only by a real deploy + a real-browser operator pass.
> See the runbook before deploying. Nothing here has been browser-validated.

## Why a separate app

The voice VM (`open-persona-voice`) runs the embedder + VAD + STT/TTS/LLM
in-process. Co-locating the media SFU would re-create the CPU/event-loop
starvation the voice-VM scale-up just fixed. The SFU gets its own machine.

## Files

| File | Purpose |
|---|---|
| `fly.toml` | Fly app config: TCP signaling (443→7880), ICE/TCP (7881), **UDP single-port mux (7882, external==internal)**, `performance-1x` VM. |
| `livekit.yaml` | Prod LiveKit config, derived + hardened from `livekit.dev.yaml`. Single-port UDP, `use_external_ip:false`, TURN present-but-commented. |
| `entrypoint.sh` | Resolves the dedicated Fly v4 → `--node-ip`; binds to `fly-global-services` (Fly UDP rule); asserts `LIVEKIT_KEYS`. |
| `Dockerfile` | Thin layer over `livekit/livekit-server:v1.13.1` + the config + entrypoint. |

## The two hard Fly facts (why the config looks the way it does)

1. **UDP needs a dedicated IPv4** — `fly ips allocate-v4` is mandatory; a shared
   anycast v4 won't carry UDP, and IPv6 UDP is unsupported.
2. **Fly does NOT rewrite the UDP port** — external port must equal internal port,
   so the UDP mux is `7882` on both sides and equals `rtc.udp_port`.

Full reasoning + citations: `docs/research/livekit_selfhost_fly.md`.
Operator deploy + browser-pass steps: see that research doc's §4 + the runbook
(`docs/research/livekit_selfhost_fly_runbook.md`).

## Re-pointing voice (reversible, no code change)

Voice reads `PERSONA_VOICE_LIVEKIT_URL` / `_API_KEY` / `_API_SECRET` from env.
Switch to self-hosted by setting those secrets on `open-persona-voice`
(`PERSONA_VOICE_LIVEKIT_URL=wss://open-persona-livekit.fly.dev`); revert by
setting them back to the LiveKit Cloud values. One URL swap each way.

## Local / community

No change — community already runs this server via `docker compose up -d livekit`
(`livekit.dev.yaml`). This package is the prod deploy target only.
