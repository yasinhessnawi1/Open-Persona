"use client";

/**
 * Spec 35 D-35-7 — the identity orb beyond voice (signature moment #3).
 *
 * A lightweight reuse of the voice <IdentityOrb> as the chat-header avatar: it
 * pulses while the persona is live (composing a reply) and breathes calmly when
 * idle. Audio getters are no-ops (chat has no mic/TTS levels); the "live" signal
 * arrives via a decoupled `chat-streaming` window event dispatched by
 * <ChatWindow> — so the header (above the chat window in the tree) stays in
 * sync without prop-drilling streaming state up through the page.
 */

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { IdentityOrb } from "@/components/voice/identity-orb";

/** Dispatched by ChatWindow on each streaming-state change; detail = streaming. */
export const CHAT_STREAMING_EVENT = "chat-streaming";

const ZERO = () => 0;

export function ChatPresenceOrb({
  persona,
  avatarUrl,
  size = 40,
}: {
  persona: { id: string; name: string };
  avatarUrl?: string | null;
  size?: number;
}) {
  const t = useTranslations("chat");
  const [streaming, setStreaming] = useState(false);

  useEffect(() => {
    const onStreaming = (e: Event) =>
      setStreaming(Boolean((e as CustomEvent<boolean>).detail));
    window.addEventListener(CHAT_STREAMING_EVENT, onStreaming);
    return () => window.removeEventListener(CHAT_STREAMING_EVENT, onStreaming);
  }, []);

  return (
    <IdentityOrb
      persona={persona}
      // composing → the orbiting "thinking" highlight; idle → calm breathing.
      agentState={streaming ? "thinking" : "listening"}
      bargeInSignal={0}
      getMicLevel={ZERO}
      getPersonaLevel={ZERO}
      avatarUrl={avatarUrl}
      label={streaming ? t("thinking", { name: persona.name }) : persona.name}
      size={size}
    />
  );
}
