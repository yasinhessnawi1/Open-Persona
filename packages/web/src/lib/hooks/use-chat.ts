"use client";

import { useCallback, useRef, useState } from "react";
import { useAuth } from "@/auth";
import type { ChatMessageView } from "@/components/chat/message-element";
import { ApiError, createApiClient, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { consumeSSE } from "@/lib/sse";
import type { ProactiveProposal } from "@/lib/sse-types";
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
 * Spec 35 — a document attached to a turn, carried on the optimistic user
 * message so the file is visible in the thread. Display-only (the backend reads
 * the document from conversation context; it isn't re-sent in the request body).
 */
export type AttachedDoc = {
  doc_ref: string;
  filename: string;
  format: string;
  size_bytes: number | null;
  strategy?: "whole_inject" | "retrieval" | "vision_handoff";
};

/**
 * Chat state + SSE streaming (spec §4.2). On send: optimistically append the
 * user turn + a streaming assistant turn, then consume the SSE stream
 * (`chunk` → accumulate, `tool_calling`/`tool_result` → cards, `done` → tier).
 * Reconnection (spec §8): on a mid-stream disconnect, re-fetch the persisted
 * history — never resume the raw SSE. (A clean ApiError like 429 keeps the
 * optimistic turn so the user can retry.)
 */
export function useChat(
  conversationId: string,
  initial: ChatMessageView[],
  personaId: string,
) {
  const { getToken } = useAuth();
  const [messages, setMessages] = useState<ChatMessageView[]>(initial);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(false);
  // Spec 30 (D-30-2): the last user message, so an in-chat consent accept can
  // re-send it (surface-and-retry) once the capability is granted.
  const lastUserMessage = useRef<string>("");

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
    async (
      content: string,
      attachedImages: ImageRef[] = [],
      attachedDocs: AttachedDoc[] = [],
    ) => {
      if (!content.trim() || streaming) return;
      setError(false);
      lastUserMessage.current = content;
      const userId = crypto.randomUUID();
      const asstId = crypto.randomUUID();
      setMessages((m) => [
        ...m,
        // F3 (T06): the optimistic user-turn carries `images` so the bubble
        // can render the just-attached image inline before the server echoes
        // it back on history reload. Empty array means text-only — message
        // element renders the existing text-only path byte-for-byte.
        // Spec 35: `documents` ride the same optimistic turn so the attached
        // file is visible in the thread (the backend reads them as conversation
        // context; they're display-only on the request side).
        {
          id: userId,
          role: "user",
          content,
          images: attachedImages.length > 0 ? attachedImages : undefined,
          documents: attachedDocs.length > 0 ? attachedDocs : undefined,
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
                  // Spec 30 T01 (D-30-1): the source badge the card renders.
                  kind: c.kind,
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
                      toolKind: c.kind,
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
                    // Prefer the result frame's kind; keep the call's if absent.
                    kind: ev.data.kind ?? tools[i].kind,
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
                    toolKind: ev.data.kind,
                    // F4 T02b: forward structured produced_files when the
                    // runtime amendment surfaces them. Renders inline via the
                    // OutputDispatcher in MessageElement (T10). Absent on
                    // pre-amendment frames + tools that don't produce files.
                    producedFiles: ev.data.produced_files,
                    // Spec 28: forward persisted artifacts (the unified
                    // FileCard path; preferred over produced_files downstream).
                    artifacts: ev.data.artifacts,
                  } as const,
                ],
              };
            });
          } else if (ev.event === "asking_user") {
            // Spec 30 (D-30-2): the chat-proactive-question rail. The shared
            // loop emits this for a tool-gap / MCP-gap consent offer (the
            // question prose also streamed as chunks above). Attach the
            // interactive prompt to the assistant turn so the rail renders
            // inline; `proposal` (when present) carries the accept→grant→retry
            // descriptor. A plain clarifying ask (no proposal) renders the 3+1
            // / free-text prompt without a grant action.
            patch((a) => ({
              ...a,
              proactive: {
                question: ev.data.question,
                options: ev.data.options,
                allowFreeForm: ev.data.allow_free_form,
                proposal: ev.data.proposal,
              },
            }));
          } else if (ev.event === "memory_recall") {
            // Spec 35 (D-35-4): the "thinking / remembering" state — one frame
            // per typed store consulted while composing. Accumulate in order so
            // the streaming turn renders the staged "Recalling from <store>
            // memory" pulse ahead of the answer.
            patch((a) => ({
              ...a,
              recall: [
                ...(a.recall ?? []),
                { store: ev.data.store, count: ev.data.count },
              ],
            }));
          } else if (ev.event === "done") {
            // Spec 31 (D-31-1/2): carry the model decision + budget snapshot
            // alongside the tier (both absent on rule-based turns ⇒ undefined).
            patch((a) => ({
              ...a,
              tier: ev.data.tier,
              routing: ev.data.routing,
              budget: ev.data.budget,
            }));
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

  // Spec 30 (D-30-2): grant a capability the runtime offered (the rail's
  // accept path). For an `mcp:<server>` proposal the API consent path admits
  // catalog-valid MCP names (D-30-X-mcp-gap-accept-target). RLS-scoped via the
  // bearer token. Persisting the grant to the persona's allow-list is what makes
  // the retry effective — the next turn rebuilds the toolbox with the new tool.
  const grantCapability = useCallback(
    async (toolName: string) => {
      const jwt = await token();
      const client = createApiClient(() => Promise.resolve(jwt));
      await unwrap(
        await client.POST("/v1/personas/{persona_id}/tools", {
          params: { path: { persona_id: personaId } },
          body: { tool_name: toolName },
        }),
      );
    },
    [personaId, token],
  );

  // Spec 30 (D-30-2): answer an in-chat proactive question. The enable option
  // (with a `grant_tool` proposal) grants the capability then RE-SENDS the prior
  // user message (surface-and-retry — chat is request/response, no held SSE).
  // Every other answer (decline / explain / free-form) is just the next user
  // message. Clears the prompt on the assistant turn either way.
  const respondToProactive = useCallback(
    async (
      messageId: string,
      answer: string,
      opts: { isAccept: boolean; proposal?: ProactiveProposal },
    ) => {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === messageId ? { ...msg, proactive: undefined } : msg,
        ),
      );
      if (opts.isAccept && opts.proposal?.action === "grant_tool") {
        await grantCapability(opts.proposal.name);
        await send(lastUserMessage.current);
        return;
      }
      await send(answer);
    },
    [grantCapability, send],
  );

  return { messages, streaming, error, send, reload, respondToProactive };
}
