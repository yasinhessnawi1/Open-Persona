import { Phone } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { ChatPresenceOrb } from "@/components/chat/chat-presence-orb";
import { ChatWindow } from "@/components/chat/chat-window";
import type { ChatMessageView } from "@/components/chat/message-element";
import { buttonVariants } from "@/components/ui/button";
import { unwrap } from "@/lib/api";
import { serverApi } from "@/lib/api/server";
import { parsePersonaYaml } from "@/lib/persona";
import { personaIdentityStyle } from "@/lib/persona-identity";

/**
 * T26: rebuilt chat screen. Header swaps the scaffold's <Avatar> +
 * `bg-primary/10` uniform fallback for the D-F1-5 PersonaIdentityHeader
 * (avatar in the derived identity colour + 1px identity-coloured underline
 * beneath the persona name + the optional constraint cue). Chat body
 * delegates to <ChatWindow>, which now renders <MessageElement> with the
 * D-F2-15 interleaved tool layout.
 *
 * DO NOT TOUCH: the serverApi() conversation + persona fetches, the
 * notFound() on 404, parsePersonaYaml, the conversation-message → view
 * mapping, the h-[calc(100svh-3.5rem)] viewport calculation. Audit
 * §chat.plumbing covers the strangler-fig inventory.
 */
export default async function ChatPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  const t = await getTranslations("voice");
  const api = await serverApi();

  const convRes = await api.GET("/v1/conversations/{conversation_id}", {
    params: { path: { conversation_id: conversationId } },
  });
  if (convRes.response.status === 404) notFound();
  const conv = await unwrap(convRes);

  const personaRes = await api.GET("/v1/personas/{persona_id}", {
    params: { path: { persona_id: conv.persona_id } },
  });
  const persona = personaRes.data
    ? parsePersonaYaml(personaRes.data.yaml)
    : null;
  const name = persona?.name ?? conv.title ?? "Persona";
  const role = persona?.role ?? "";
  const constraint = persona?.constraints[0];

  // The persona shape PersonaIdentityHeader + MessageElement consume.
  // id drives the deterministic identity-colour derivation; avatar_url
  // overrides the initials-mark when present.
  const personaForDisplay = {
    id: conv.persona_id,
    name,
    avatar_url: personaRes.data?.avatar_url ?? undefined,
    role,
    constraint,
  };

  const initialMessages: ChatMessageView[] = conv.messages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    // Spec 35 D-35-2: the persisted routing tier, so the per-message tier chip
    // renders on a reloaded conversation (not just the live turn). Null on
    // historical / non-assistant rows ⇒ no chip (clean degrade).
    tier: m.tier_used ?? undefined,
  }));

  return (
    <div className="flex h-[calc(100svh-3.5rem)] flex-col">
      {/* Spec 35: chat header on the editorial .v-chat__head, with the
          identity presence orb (#3) as the avatar — it pulses while live. */}
      <div
        className="v-chat__head"
        style={personaIdentityStyle(personaForDisplay)}
      >
        <ChatPresenceOrb
          persona={{ id: personaForDisplay.id, name }}
          avatarUrl={personaForDisplay.avatar_url}
          size={40}
        />
        <div className="v-chat__head-meta">
          <div className="v-chat__name">
            <span className="v-id-underline">{name}</span>
          </div>
          {role ? <div className="v-chat__role">{role}</div> : null}
        </div>
        {/* V6 D-V6-4 — "Talk to {persona}" entry; binds this conversation. */}
        <Link
          href={`/chat/${conversationId}/voice`}
          aria-label={t("talk", { name })}
          className={buttonVariants({ variant: "secondary", size: "icon" })}
        >
          <Phone />
        </Link>
      </div>
      <ChatWindow
        conversationId={conversationId}
        persona={personaForDisplay}
        initialMessages={initialMessages}
        // F3 (T19) — D-F3-X-capabilities-prop-drill-shape: prop drill from
        // PersonaDetail → ChatWindow → ComposerAttachControl. NOT context,
        // NOT a global store. `capabilities` may be undefined when the
        // runtime isn't wired (test paths / pre-T02 deployments); the
        // composer treats that as "vision unknown" and falls open by
        // default (server stays authoritative — Spec 13 fail-loud refuses
        // image turns on text-only deployments per T15's (c) safety net).
        capabilities={personaRes.data?.capabilities ?? null}
      />
    </div>
  );
}
