"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useState } from "react";
import type { ChatMessageView } from "@/components/chat/message-element";
import { ApiError, createApiClient, unwrap } from "@/lib/api/client";
import { consumeSSE } from "@/lib/sse";
import { parseChatEvent } from "@/lib/sse-types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * Chat state + SSE streaming (spec §4.2). On send: optimistically append the
 * user turn + a streaming assistant turn, then consume the SSE stream
 * (`chunk` → accumulate, `tool_calling`/`tool_result` → cards, `done` → tier).
 * Reconnection (spec §8): on a mid-stream disconnect, re-fetch the persisted
 * history — never resume the raw SSE. (A clean ApiError like 429 keeps the
 * optimistic turn so the user can retry.)
 */
export function useChat(conversationId: string, initial: ChatMessageView[]) {
  const { getToken } = useAuth();
  const [messages, setMessages] = useState<ChatMessageView[]>(initial);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(false);

  const token = useCallback(
    () => getToken(TEMPLATE ? { template: TEMPLATE } : undefined),
    [getToken],
  );

  const reload = useCallback(async () => {
    const jwt = await token();
    const client = createApiClient(() => Promise.resolve(jwt));
    const conv = await unwrap(
      await client.GET("/v1/conversations/{conversation_id}", {
        params: { path: { conversation_id: conversationId } },
      }),
    );
    setMessages(
      conv.messages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
      })),
    );
  }, [conversationId, token]);

  const send = useCallback(
    async (content: string) => {
      if (!content.trim() || streaming) return;
      setError(false);
      const userId = crypto.randomUUID();
      const asstId = crypto.randomUUID();
      setMessages((m) => [
        ...m,
        { id: userId, role: "user", content },
        {
          id: asstId,
          role: "assistant",
          content: "",
          tools: [],
          // F2 D-F2-15: events[] preserves stream order so MessageElement
          // can render text + tool cards interleaved. content + tools stay
          // populated for back-compat (markdown final render, copy-paste).
          events: [],
          streaming: true,
        },
      ]);
      setStreaming(true);

      const patch = (fn: (a: ChatMessageView) => ChatMessageView) =>
        setMessages((m) => m.map((msg) => (msg.id === asstId ? fn(msg) : msg)));

      try {
        const jwt = await token();
        for await (const raw of consumeSSE(
          `${API}/v1/conversations/${conversationId}/messages`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${jwt}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ content }),
          },
        )) {
          const ev = parseChatEvent(raw);
          if (!ev) continue;
          if (ev.event === "chunk") {
            patch((a) => ({
              ...a,
              content: a.content + ev.data.delta,
              events: [
                ...(a.events ?? []),
                { kind: "text", delta: ev.data.delta } as const,
              ],
            }));
          } else if (ev.event === "tool_calling") {
            patch((a) => ({
              ...a,
              tools: [
                ...(a.tools ?? []),
                ...ev.data.tool_calls.map((c) => ({
                  toolName: c.name,
                  args: c.args,
                  pending: true,
                })),
              ],
              events: [
                ...(a.events ?? []),
                ...ev.data.tool_calls.map(
                  (c) =>
                    ({
                      kind: "tool_call",
                      callId: c.call_id,
                      toolName: c.name,
                      args: c.args,
                    }) as const,
                ),
              ],
            }));
          } else if (ev.event === "tool_result") {
            patch((a) => {
              const tools = [...(a.tools ?? [])];
              for (let i = tools.length - 1; i >= 0; i--) {
                if (
                  tools[i].toolName === ev.data.tool_name &&
                  tools[i].pending
                ) {
                  tools[i] = {
                    ...tools[i],
                    result: ev.data.content,
                    isError: ev.data.is_error,
                    pending: false,
                  };
                  break;
                }
              }
              return {
                ...a,
                tools,
                events: [
                  ...(a.events ?? []),
                  {
                    kind: "tool_result",
                    toolName: ev.data.tool_name,
                    content: ev.data.content,
                    isError: ev.data.is_error,
                  } as const,
                ],
              };
            });
          } else if (ev.event === "done") {
            patch((a) => ({ ...a, tier: ev.data.tier }));
          }
        }
        patch((a) => ({ ...a, streaming: false }));
        setStreaming(false);
      } catch (e) {
        setStreaming(false);
        setError(true);
        patch((a) => ({ ...a, streaming: false }));
        // Mid-stream disconnect → recover from persisted history. A clean API
        // rejection (e.g. 429) keeps the optimistic turn so the user can retry.
        if (!(e instanceof ApiError)) {
          await reload().catch(() => {});
        }
      }
    },
    [conversationId, streaming, token, reload],
  );

  return { messages, streaming, error, send, reload };
}
