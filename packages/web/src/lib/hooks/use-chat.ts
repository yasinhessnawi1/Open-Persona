"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useState } from "react";
import type { ChatMessageView } from "@/components/chat/message-element";
import { ApiError, createApiClient, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { consumeSSE } from "@/lib/sse";
import { parseChatEvent } from "@/lib/sse-types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * F3 (T06) — workspace reference for an attached image. Mirrors the API's
 * `ImageRef` shape exactly; the composer (T19) maps successful uploads
 * (`ImageAttachment.state === "success"`) onto this shape before passing
 * the array to `send()`. Store-by-reference: NEVER a base64 data URI.
 */
export type ImageRef = components["schemas"]["ImageRef"];

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
    async (content: string, attachedImages: ImageRef[] = []) => {
      if (!content.trim() || streaming) return;
      setError(false);
      const userId = crypto.randomUUID();
      const asstId = crypto.randomUUID();
      setMessages((m) => [
        ...m,
        // F3 (T06): the optimistic user-turn carries `images` so the bubble
        // can render the just-attached image inline before the server echoes
        // it back on history reload. Empty array means text-only — message
        // element renders the existing text-only path byte-for-byte.
        {
          id: userId,
          role: "user",
          content,
          images: attachedImages.length > 0 ? attachedImages : undefined,
        },
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
        // F3 (T06) — store-by-reference structural defence (Concern #4):
        // `images` is the API field per PostMessageRequest.images
        // (`Field(min_length=1, max_length=4)` at requests.py:143). Omit
        // the field entirely (NOT `images: []`) when there are no images —
        // an empty list trips the server's min_length=1 validator. The
        // refs are pre-uploaded by upload.ts; this body carries ONLY the
        // workspace_path + media_type strings, never base64 bytes.
        // T22's body-size regression test asserts a 1 MB image → < 2 KB body.
        const requestBody: { content: string; images?: ImageRef[] } = {
          content,
        };
        if (attachedImages.length > 0) {
          requestBody.images = attachedImages;
        }
        for await (const raw of consumeSSE(
          `${API}/v1/conversations/${conversationId}/messages`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${jwt}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(requestBody),
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
                    // F4 T02b: forward structured produced_files when the
                    // runtime amendment surfaces them. Renders inline via the
                    // OutputDispatcher in MessageElement (T10). Absent on
                    // pre-amendment frames + tools that don't produce files.
                    producedFiles: ev.data.produced_files,
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
