# Open Persona — self-hosted LiveKit SFU image (app: open-persona-livekit).
#
# Thin layer over the official LiveKit OSS server (the SAME image + version the
# community edition runs via docker-compose.yml: livekit/livekit-server:v1.13.1).
# v1.13.1 is pinned because livekit-client@2.19 uses the /rtc/v1/ signaling path,
# which older servers 404 (see docker-compose.yml + docs/research). Keep prod ==
# community pin.
#
# We add: the prod livekit.yaml + a Fly entrypoint that injects --node-ip (the
# app's dedicated Fly v4) and binds to fly-global-services (Fly's UDP rule).
#
# Build context = packages/livekit/ (fly.toml's [build] dockerfile = "Dockerfile"
# resolves here). Unlike the api/voice images, this one needs NO repo-root context.
FROM livekit/livekit-server:v1.13.1

# Baked prod config; the entrypoint reads $LIVEKIT_CONFIG_PATH (fly.toml [env]).
COPY livekit.yaml /etc/livekit/livekit.yaml
COPY entrypoint.sh /entrypoint.sh

# The base image is alpine, so /bin/sh + busybox getent/awk are present. Make the
# entrypoint executable (preserve the bit regardless of host checkout perms).
RUN chmod +x /entrypoint.sh

# Override the base ENTRYPOINT (["/livekit-server"]) with our Fly wrapper, which
# exec's livekit-server with the runtime --node-ip / --bind flags.
ENTRYPOINT ["/entrypoint.sh"]
