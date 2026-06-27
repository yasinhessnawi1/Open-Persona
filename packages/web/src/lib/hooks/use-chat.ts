"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/auth";
import type { ChatMessageView } from "@/components/chat/message-element";
import { reduceActivityEnd, reduceActivityStart } from "@/lib/activity";
import { ApiError, createApiClient, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { consumeSSE, type RawSSEEvent } from "@/lib/sse";
import type { ProactiveProposal } from "@/lib/sse-types";
import { parseChatEvent } from "@/lib/sse-types";
import { useActiveWork } from "@/lib/work/active-work-context";

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

type Patch = (fn: (a: ChatMessageView) => ChatMessageView) => void;

/**
 * Apply ONE SSE frame from a turn's stream to the assistant turn (Spec P1 T7).
 *
 * Extracted so the originating POST stream (`send`) AND the reattach tail
 * (`reattach` → `…/active-turn/events`) share the exact same frame handling —
 * the reattach is the same proven streaming code, not a fork. Returns `"error"`
 * when the worker emitted an `error` frame (a turn that failed server-side) so
 * the caller can surface it and stop; `"ok"` otherwise.
 */
function applyTurnFrame(raw: RawSSEEvent, patch: Patch): "ok" | "error" {
  // The detached worker emits an `error` frame on a server-side turn failure
  // (Spec P1) before ending the stream; surface it rather than silently stopping.
  if (raw.event === "error") return "error";
  const ev = parseChatEvent(raw);
  if (!ev) return "ok";
  if (ev.event === "thinking") {
    // The model is generating this round — show a "working" pulse during the gap
    // before any text/tool event (notably while writing a long code_execution
    // call). Cleared by the next chunk / tool_calling.
    patch((a) => ({ ...a, working: true }));
  } else if (ev.event === "chunk") {
    patch((a) => ({
      ...a,
      working: false,
      content: a.content + ev.data.delta,
      events: [
        ...(a.events ?? []),
        { kind: "text", delta: ev.data.delta } as const,
      ],
    }));
  } else if (ev.event === "tool_calling") {
    patch((a) => ({
      ...a,
      working: false,
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
        if (tools[i].toolName === ev.data.tool_name && tools[i].pending) {
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
            // F4 T02b: forward structured produced_files when the runtime
            // amendment surfaces them. Renders inline via the OutputDispatcher in
            // MessageElement (T10). Absent on pre-amendment frames + tools that
            // don't produce files.
            producedFiles: ev.data.produced_files,
            // Spec 28: forward persisted artifacts (the unified FileCard path;
            // preferred over produced_files downstream).
            artifacts: ev.data.artifacts,
          } as const,
        ],
      };
    });
  } else if (ev.event === "activity_start") {
    // P2: open the live "using <X>…" state — a SEPARATE channel from `tools` (the card
    // stays sourced from tool_result during keep-both, P2-D-3). Idempotent on the
    // reattach replay (dedup by activity_id).
    patch((a) => ({
      ...a,
      working: false,
      activities: reduceActivityStart(a.activities, ev.data),
    }));
  } else if (ev.event === "activity_end") {
    // P2: resolve the matching live state by activity_id (no-op if no start seen).
    patch((a) => ({
      ...a,
      activities: reduceActivityEnd(a.activities, ev.data),
    }));
  } else if (ev.event === "asking_user") {
    // Spec 30 (D-30-2): the chat-proactive-question rail. The shared loop emits
    // this for a tool-gap / MCP-gap consent offer (the question prose also
    // streamed as chunks above). Attach the interactive prompt to the assistant
    // turn so the rail renders inline; `proposal` (when present) carries the
    // accept→grant→retry descriptor.
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
    // Spec 35 (D-35-4): the "thinking / remembering" state — one frame per typed
    // store consulted while composing.
    patch((a) => ({
      ...a,
      recall: [
        ...(a.recall ?? []),
        { store: ev.data.store, count: ev.data.count },
      ],
    }));
  } else if (ev.event === "done") {
    // Spec 31 (D-31-1/2): carry the model decision + budget snapshot alongside
    // the tier (both absent on rule-based turns ⇒ undefined).
    patch((a) => ({
      ...a,
      tier: ev.data.tier,
      routing: ev.data.routing,
      budget: ev.data.budget,
    }));
  }
  return "ok";
}

/**
 * Chat state + SSE streaming (spec §4.2) + persistent/resumable turns (Spec P1).
 *
 * On send: optimistically append the user turn + a streaming assistant turn,
 * then consume the SSE stream from the detached turn (`chunk` → accumulate,
 * `tool_calling`/`tool_result` → cards, `done` → tier).
 *
 * Spec P1 — the turn now runs server-side detached, so it survives navigation:
 * - The active stream's `AbortController` is aborted on UNMOUNT (navigate away),
 *   which stops the fetch but NOT the server-side turn — it keeps running.
 * - On MOUNT/return, `reattach()` asks `GET …/active-turn`; if a turn is live it
 *   marks the assistant turn streaming and resubscribes to `…/active-turn/events`
 *   (the same frame handling as send), then RECONCILES via the persisted history
 *   on stream end so the final content is authoritative (no gap/double survives a
 *   completed turn).
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
  // Spec P1: the active turn's fetch controller — aborted on unmount so a
  // navigate-away stops the stream WITHOUT cancelling the detached server turn.
  const abortRef = useRef<AbortController | null>(null);
  // Latest `reattach`, so `send`'s 409 path + the mount effect call it without a
  // dependency cycle / re-running on every `streaming` toggle.
  const reattachRef = useRef<() => Promise<void>>(async () => {});

  // Spec P1 (D-P1-v7-indicator): advertise the in-progress turn to the app-level
  // active-work session so the conversation row / global bar show a "working"
  // cue. Register while streaming; unregister when the turn ENDS WHILE MOUNTED.
  // Deliberately NO unmount cleanup — a navigate-away must KEEP the indicator (the
  // turn keeps running server-side); the provider's poll clears it on completion.
  const { registerChat, unregisterChat } = useActiveWork();
  useEffect(() => {
    if (streaming) registerChat({ conversationId, personaId });
    else unregisterChat(conversationId);
  }, [streaming, conversationId, personaId, registerChat, unregisterChat]);

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
        // F3 (T06): the optimistic user-turn carries `images` so the bubble can
        // render the just-attached image inline before the server echoes it back.
        // Spec 35: `documents` ride the same optimistic turn (display-only).
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
          // F2 D-F2-15: events[] preserves stream order for interleaved render.
          events: [],
          streaming: true,
        },
      ]);
      setStreaming(true);

      const patch: Patch = (fn) =>
        setMessages((m) => m.map((msg) => (msg.id === asstId ? fn(msg) : msg)));

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const jwt = await token();
        // F3 (T06) — store-by-reference: omit `images` entirely (NOT `[]`) when
        // empty (the server's min_length=1 validator rejects an empty list). The
        // body carries ONLY workspace_path + media_type, never base64 bytes.
        const requestBody: { content: string; images?: ImageRef[] } = {
          content,
        };
        if (attachedImages.length > 0) requestBody.images = attachedImages;
        for await (const raw of consumeSSE(
          `${API}/v1/conversations/${conversationId}/messages`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${jwt}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(requestBody),
            signal: ctrl.signal,
          },
        )) {
          if (applyTurnFrame(raw, patch) === "error") {
            setError(true);
            break;
          }
        }
        patch((a) => ({ ...a, streaming: false, working: false }));
        setStreaming(false);
      } catch (e) {
        // Navigate-away aborts the fetch — the detached turn keeps running; do
        // NOT mark the turn failed or reload (the next mount reattaches).
        if ((e as Error)?.name === "AbortError") return;
        setStreaming(false);
        patch((a) => ({ ...a, streaming: false, working: false }));
        // A 409 means a turn is already active for this conversation (one-active-
        // turn): reattach to it rather than showing an error.
        if (e instanceof ApiError && e.status === 409) {
          await reattachRef.current();
          return;
        }
        setError(true);
        // Other mid-stream failures (network) → recover from persisted history.
        // A clean ApiError (e.g. 429) keeps the optimistic turn so it can retry.
        if (!(e instanceof ApiError)) await reload().catch(() => {});
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
      }
    },
    [conversationId, streaming, token, reload],
  );

  // Spec P1 — reattach to a live turn on mount/return. Detect via
  // `GET …/active-turn` (404 ⇒ nothing live), mark the assistant turn streaming,
  // resubscribe to the live tail, then reconcile via persisted history on end.
  const reattach = useCallback(async () => {
    if (streaming || abortRef.current) return;
    const jwt = await token();
    let active: {
      message_id: string;
      streaming_status: string;
      content: string;
    } | null = null;
    try {
      const res = await fetch(
        `${API}/v1/conversations/${conversationId}/active-turn`,
        { headers: { Authorization: `Bearer ${jwt}` } },
      );
      if (res.status === 404) return; // no live turn — normal mount
      if (!res.ok) return;
      active = await res.json();
    } catch {
      return; // detection is best-effort; a failure just means "no reattach"
    }
    if (!active) return;
    const asstId = active.message_id;

    setStreaming(true);
    // Seed: mark the in-progress assistant row (already present from the
    // server-fetched history) as streaming. Its content is the persisted
    // checkpoint; the live tail APPENDS new deltas, and the reconcile on end
    // replaces it with the authoritative final — so a completed turn never shows
    // a gap or a double.
    setMessages((m) => {
      const exists = m.some((msg) => msg.id === asstId);
      const seeded = m.map((msg) =>
        msg.id === asstId ? { ...msg, streaming: true, working: true } : msg,
      );
      return exists
        ? seeded
        : [
            ...seeded,
            {
              id: asstId,
              role: "assistant" as const,
              content: active?.content ?? "",
              tools: [],
              events: [],
              streaming: true,
            },
          ];
    });
    const patch: Patch = (fn) =>
      setMessages((m) => m.map((msg) => (msg.id === asstId ? fn(msg) : msg)));

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      for await (const raw of consumeSSE(
        `${API}/v1/conversations/${conversationId}/active-turn/events`,
        { headers: { Authorization: `Bearer ${jwt}` }, signal: ctrl.signal },
      )) {
        if (applyTurnFrame(raw, patch) === "error") {
          setError(true);
          break;
        }
      }
      patch((a) => ({ ...a, streaming: false, working: false }));
      setStreaming(false);
      // Reconcile: the persisted final is authoritative (covers the throttled-
      // checkpoint seed boundary). Never resume the raw SSE.
      await reload().catch(() => {});
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return; // unmount; turn keeps running
      setStreaming(false);
      patch((a) => ({ ...a, streaming: false, working: false }));
      // 404 ⇒ the turn finished between detect and tail; reconcile.
      await reload().catch(() => {});
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
    }
  }, [conversationId, streaming, token, reload]);

  // Keep the ref pointing at the latest `reattach` (closes over current state).
  reattachRef.current = reattach;

  // On mount/return: try to reattach to a live turn (ONCE per conversation —
  // keyed on the id, not on `reattach`, so a `streaming` toggle doesn't re-fire
  // it). On unmount (navigate away): abort the active fetch — the detached server
  // turn keeps running, re-tailable on the next mount. conversationId is the
  // intended trigger (App Router reuses this component across /chat/[id], so the
  // effect must re-fire on id change); the body reads the latest reattach via a
  // ref by design.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see comment above — conversationId is the deliberate re-fire key; reattach is read via ref.
  useEffect(() => {
    void reattachRef.current();
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [conversationId]);

  // Spec 30 (D-30-2): grant a capability the runtime offered (the rail's accept
  // path). Persisting the grant is what makes the retry effective.
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
  // grants the capability then RE-SENDS the prior user message (surface-and-
  // retry). Every other answer is just the next user message.
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

  return {
    messages,
    streaming,
    error,
    send,
    reload,
    reattach,
    respondToProactive,
  };
}
