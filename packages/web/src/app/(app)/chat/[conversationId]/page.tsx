import { notFound } from "next/navigation";
import { ChatWindow } from "@/components/chat/chat-window";
import type { ChatMessageView } from "@/components/chat/message-element";
import { PersonaIdentityHeader } from "@/components/persona/persona-identity-header";
import { unwrap } from "@/lib/api";
import { serverApi } from "@/lib/api/server";
import { parsePersonaYaml } from "@/lib/persona";

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
  }));

  return (
    <div className="flex h-[calc(100svh-3.5rem)] flex-col">
      <div className="border-b px-4 py-2.5">
        <PersonaIdentityHeader
          persona={personaForDisplay}
          size="md"
          showConstraints
        />
      </div>
      <ChatWindow
        conversationId={conversationId}
        persona={personaForDisplay}
        initialMessages={initialMessages}
      />
    </div>
  );
}
