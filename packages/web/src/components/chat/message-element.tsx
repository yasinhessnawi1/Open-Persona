"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useMemo } from "react";

/**
 * Spec F2 T15 + T26 — MessageElement.
 *
 * Replaces the scaffold's <MessageBubble> as the F2 canonical chat-message
 * presentation. Composes T13/T17/T16/T11/T06.
 *
 * D-F1-5 composite (locked from F1 closeout + decisions.md):
 *   - identity-coloured <PersonaAvatar> at top of each persona TURN
 *     (D-F2-7 once-per-turn rule — consecutive persona messages share one
 *     avatar; broken by a user message → render again);
 *   - 1px identity-coloured underline beneath the persona name (lives in
 *     <PersonaIdentityHeader>, used at the chat top, not per-message);
 *   - 2px identity-coloured `border-left` on each persona message wrapper.
 *
 * **D-F2-15 (NEW, post-T22 user-driven amendment):** interleaved tool layout.
 * The scaffold's MessageBubble (and the original T15 stacked layout) showed
 * all tool-call cards above the text content, batched together regardless
 * of when they happened in the stream. The user-reported chat-UX issue
 * (2026-06-06) was: (1) no thinking indicator before first chunk; (2) no
 * tool-running indicator during the pause between text spans while tools
 * execute; (3) tool cards clumping at the top; (4) text concatenation
 * losing the gap where tools happened. D-F2-15 introduces an event-log
 * rendering mode that walks `message.events[]` in stream order and emits
 * text spans + tool cards inline at the position they arrived. Falls back
 * to the stacked layout when `events[]` is absent (back-compat for tests
 * + the older API surface).
 *
 * Activity indicator state machine (D-F2-13 + the new tool-running state):
 *   - streaming && events is empty           → ThinkingIndicator (3 muted dots)
 *   - streaming && last event is text        → vermilion Caret on the last span
 *   - streaming && a tool is pending         → ToolRunningIndicator (name pulse)
 *   - !streaming                             → no indicator
 *
 * User branch: right-aligned, `rounded-2xl rounded-br-sm`, `bg-secondary`,
 * `.type-body`. The `max-w-[80%]` is a percentage layout (positional, not
 * a design value; audit.md §grep-gate-seed).
 *
 * Tier badge sits below the body when the message is terminal.
 */

import type { AvatarPersona } from "@/components/persona/persona-avatar";
import { PersonaAvatar } from "@/components/persona/persona-avatar";
import { Markdown } from "@/components/ui/markdown";
import {
  derivePersonaIdentityColor,
  personaIdentityStyle,
} from "@/lib/persona-identity";
import { cn } from "@/lib/utils";
import { StreamingTextRenderer } from "./streaming-text-renderer";
import { TierBadge } from "./tier-badge";
import { ToolCallCard, type ToolEntry } from "./tool-call-card";

/**
 * Ordered event in the assistant stream. Drives the D-F2-15 interleaved
 * rendering: tool_call events emit inline tool cards at their actual stream
 * position; text events accumulate into spans before / between / after them.
 */
export type MessageEvent =
  | { kind: "text"; delta: string }
  | {
      kind: "tool_call";
      callId: string;
      toolName: string;
      args?: Record<string, unknown>;
    }
  | {
      kind: "tool_result";
      toolName: string;
      content: string;
      isError: boolean;
    };

/**
 * The message shape <MessageElement> consumes. `events[]` is the
 * D-F2-15 ordered log; `content` + `tools[]` are kept as derived/legacy
 * fields so older consumers continue to work.
 */
export interface MessageElementView {
  id: string;
  role: string;
  content: string;
  tier?: string;
  tools?: ToolEntry[];
  /** D-F2-15: ordered event log. When present, MessageElement renders interleaved. */
  events?: MessageEvent[];
  streaming?: boolean;
}

/**
 * Canonical name used by useChat + chat-window. Aliased so the old import
 * path (`./message-bubble`) can be removed without touching every consumer.
 */
export type ChatMessageView = MessageElementView;

interface MessageElementProps {
  message: MessageElementView;
  /**
   * The persona this conversation is with. Drives the identity colour for
   * the D-F1-5 composite. F2 ships single-persona conversations; future
   * multi-persona work would key the once-per-turn rule on persona id too.
   */
  persona: AvatarPersona;
  /**
   * The immediately-preceding message in the conversation, if any. Drives
   * the D-F2-7 once-per-turn avatar rule.
   */
  prevMessage?: MessageElementView;
  className?: string;
}

export function MessageElement({
  message,
  persona,
  prevMessage,
  className,
}: MessageElementProps) {
  if (message.role === "user") {
    return <UserMessage message={message} className={className} />;
  }
  return (
    <PersonaMessage
      message={message}
      persona={persona}
      prevMessage={prevMessage}
      className={className}
    />
  );
}

function UserMessage({
  message,
  className,
}: {
  message: MessageElementView;
  className?: string;
}) {
  return (
    <div
      className={cn("flex justify-end", className)}
      data-slot="message-element"
      data-role="user"
    >
      <div className="type-body max-w-[80%] rounded-2xl rounded-br-sm bg-secondary px-4 py-2.5 whitespace-pre-wrap text-secondary-foreground">
        {message.content}
      </div>
    </div>
  );
}

function PersonaMessage({
  message,
  persona,
  prevMessage,
  className,
}: {
  message: MessageElementView;
  persona: AvatarPersona;
  prevMessage?: MessageElementView;
  className?: string;
}) {
  // D-F2-7 once-per-turn rule: render the avatar UNLESS the previous message
  // was also a persona message (then this is a continuation of the same
  // turn and the avatar would be redundant). The 2px border-left already
  // marks every persona message; the avatar anchors identity at turn start.
  const showAvatar = !prevMessage || prevMessage.role === "user";

  const identityColour = derivePersonaIdentityColor(persona);

  const t = useTranslations("chat");
  const thinkingLabel = t("thinking", { name: persona.name });

  const hasEvents = message.events && message.events.length > 0;

  return (
    <div
      style={personaIdentityStyle(persona)}
      className={cn("flex gap-3", className)}
      data-slot="message-element"
      data-role="persona"
      data-shows-avatar={showAvatar ? "true" : "false"}
      data-layout={hasEvents ? "interleaved" : "stacked"}
    >
      <div className="size-10 shrink-0">
        {showAvatar ? <PersonaAvatar persona={persona} size="md" /> : null}
      </div>

      <div
        // D-F1-5: 2px identity-coloured border-left, bg-card surface (neutral).
        style={{ borderLeftColor: identityColour.oklch }}
        className="flex min-w-0 flex-1 flex-col gap-2 rounded-r-lg border-l-2 bg-card p-3"
        data-slot="message-element-body"
      >
        {hasEvents ? (
          <InterleavedContent
            events={message.events ?? []}
            streaming={!!message.streaming}
            thinkingLabel={thinkingLabel}
            personaName={persona.name}
          />
        ) : message.streaming && !message.content ? (
          // Streaming, no events array, no content yet → thinking.
          <StreamingTextRenderer
            text=""
            streaming
            thinking
            thinkingLabel={thinkingLabel}
          />
        ) : (
          // Legacy stacked layout: tools above content (back-compat).
          <StackedContent message={message} thinkingLabel={thinkingLabel} />
        )}

        {message.tier && !message.streaming ? (
          <TierBadge tier={message.tier} />
        ) : null}
      </div>
    </div>
  );
}

/**
 * Legacy stacked layout (back-compat for messages WITHOUT an events[] array).
 * Tool cards stack above text; streaming text uses T17 mechanism B; terminal
 * uses T11 Markdown.
 */
function StackedContent({
  message,
  thinkingLabel,
}: {
  message: MessageElementView;
  thinkingLabel: string;
}) {
  return (
    <>
      {message.tools && message.tools.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {message.tools.map((tool, i) => (
            <ToolCallCard key={`${tool.toolName}-${i}`} entry={tool} />
          ))}
        </div>
      ) : null}

      {message.streaming ? (
        <StreamingTextRenderer
          text={message.content}
          streaming
          thinking={!message.content}
          thinkingLabel={thinkingLabel}
        />
      ) : message.content ? (
        <div className="type-body" data-slot="message-element-content">
          <Markdown>{message.content}</Markdown>
        </div>
      ) : null}
    </>
  );
}

/**
 * D-F2-15 interleaved rendering. Walks events in stream order, emitting:
 *   - text spans (one per contiguous text-event run, with optional caret
 *     at the end if streaming and the last event was text);
 *   - inline ToolCallCard at the position each tool_call happened (matched
 *     with its tool_result by FIFO toolName matching — the chat-SSE
 *     tool_result frame doesn't carry a call_id, only the run-stream does).
 *
 * Pre-first-event thinking indicator + per-tool running indicator handle
 * the activity-state transitions.
 */
function InterleavedContent({
  events,
  streaming,
  thinkingLabel,
  personaName,
}: {
  events: readonly MessageEvent[];
  streaming: boolean;
  thinkingLabel: string;
  personaName: string;
}) {
  const t = useTranslations("chat");

  // Render items computed in a single pass via memo for stability across
  // re-renders that don't change events.
  const { items, pendingToolName } = useMemo(() => {
    const out: ReactNode[] = [];
    let textBuffer = "";
    let textIdx = 0;
    const consumedResults = new Set<number>();
    let pending: string | null = null;
    let lastKind: MessageEvent["kind"] | null = null;

    /**
     * Flush the buffered text run as a `<Markdown>` block, optionally
     * followed by the vermilion streaming caret.
     *
     * User feedback 2026-06-06 (two iterations):
     *  · "raw markdown not rendering" → switched plain-text spans to `<Markdown>`.
     *  · "we need to try render real time markdown … each token or chunks
     *     received" → run the FULL buffer through Markdown on every flush,
     *     not just up to the last `\n`. Inline `**bold**` / `` `code` ``
     *     settle as soon as both delimiters land instead of waiting for
     *     a paragraph break. The user accepted the brief raw-syntax flicker
     *     on incomplete inline pairs as the explicit trade-off.
     *
     * Caret placement: when `showCaret` is true the caret renders as a
     * tight sibling immediately after the markdown — `-mt-1` + `leading-none`
     * pull it back against the markdown's last baseline so it doesn't read
     * as a detached "row on its own." Inline-with-last-paragraph-text
     * would need a custom react-markdown `p` override (fragile) or DOM
     * injection (worse); the tight sibling is the honest compromise.
     */
    const flushText = (key: string, showCaret: boolean) => {
      if (textBuffer) {
        out.push(
          <div
            key={key}
            className="type-body"
            data-slot={
              showCaret ? "message-event-text-live" : "message-event-text"
            }
          >
            <Markdown>{textBuffer}</Markdown>
          </div>,
        );
        textBuffer = "";
      }
      if (showCaret) {
        out.push(
          <div
            key={`${key}-caret-wrap`}
            className="-mt-1 leading-none"
            data-slot="message-element-caret-wrap"
          >
            <Caret />
          </div>,
        );
      }
    };

    for (let i = 0; i < events.length; i++) {
      const ev = events[i];
      if (ev.kind === "text") {
        textBuffer += ev.delta;
        lastKind = "text";
        continue;
      }
      if (ev.kind === "tool_call") {
        flushText(`text-${textIdx++}`, false);
        // FIFO match: next unconsumed tool_result with matching toolName.
        let resultIdx = -1;
        for (let j = i + 1; j < events.length; j++) {
          const cand = events[j];
          if (
            cand.kind === "tool_result" &&
            cand.toolName === ev.toolName &&
            !consumedResults.has(j)
          ) {
            resultIdx = j;
            break;
          }
        }
        const result =
          resultIdx >= 0
            ? (events[resultIdx] as Extract<
                MessageEvent,
                { kind: "tool_result" }
              >)
            : null;
        if (resultIdx >= 0) consumedResults.add(resultIdx);
        const entry: ToolEntry = {
          toolName: ev.toolName,
          args: ev.args,
          result: result?.content,
          isError: result?.isError,
          pending: !result,
        };
        out.push(<ToolCallCard key={`tool-${ev.callId || i}`} entry={entry} />);
        pending = result ? null : ev.toolName;
        lastKind = "tool_call";
        continue;
      }
      // tool_result events are matched lazily inside tool_call; pending
      // result-only events without a preceding call get dropped (edge
      // case: compaction-mid stream from Spec-11 fix 5).
      lastKind = "tool_result";
    }
    // Flush trailing text. Show the caret only when streaming AND the most
    // recent stream event was text AND no tool is pending (a pending tool
    // surfaces ToolRunningIndicator instead of the text caret).
    const isLive = streaming && lastKind === "text" && pending === null;
    flushText(`text-${textIdx++}`, isLive);

    return { items: out, pendingToolName: pending };
  }, [events, streaming]);

  // Activity indicator state machine:
  //   - empty + streaming  → thinking
  //   - tool pending       → ToolRunningIndicator
  //   - last was text + streaming → caret next to the inline text span
  //   - otherwise           → none
  if (events.length === 0 && streaming) {
    return (
      <StreamingTextRenderer
        text=""
        streaming
        thinking
        thinkingLabel={thinkingLabel}
      />
    );
  }

  const showToolRunning = streaming && pendingToolName !== null;

  return (
    <div
      className="flex flex-col gap-2"
      data-slot="message-element-interleaved"
    >
      {items}
      {showToolRunning && pendingToolName ? (
        <ToolRunningIndicator
          label={t("toolRunning", {
            name: personaName,
            tool: pendingToolName,
          })}
        />
      ) : null}
    </div>
  );
}

/**
 * Vermilion `--primary` streaming caret. Same shape as the T17 caret;
 * inlined here so it can render next to a text span without going through
 * StreamingTextRenderer's prop dance. Aria-hidden — the streaming text span
 * itself carries the polite live-region.
 */
function Caret() {
  return (
    <span
      aria-hidden="true"
      className="ml-0.5 inline-block h-4 w-[3px] translate-y-0.5 animate-pulse rounded-full bg-primary"
      data-slot="message-element-caret"
    />
  );
}

/**
 * D-F2-15 + (b) tool-running status indicator. Three softly-pulsing dots +
 * the visible italic label "Astrid is using web_search…". Appears when
 * streaming AND a tool_call is awaiting its tool_result. Replaces the
 * previous "between-text silence" with a natural activity cue.
 *
 * Visual treatment matches T17 ThinkingIndicator (intentional — same
 * "the persona is busy, please wait" conversation rhythm): `type-ui` +
 * italic + `text-muted-foreground`; `py-1.5` vertical breathing room;
 * `size-2` dots with a 0/200/400ms pulse stagger for a readable wave;
 * `bg-muted-foreground/70` for a softer "still loading" tone. <output>
 * has implicit role="status" + aria-live (Biome useSemanticElements
 * convention shared with the thinking indicator).
 */
function ToolRunningIndicator({ label }: { label: string }) {
  return (
    <output
      aria-label={label}
      className="type-ui inline-flex items-center gap-2 py-1.5 text-muted-foreground italic"
      data-slot="message-element-tool-running"
    >
      <span aria-hidden="true" className="inline-flex items-center gap-1">
        <span
          className="size-2 animate-pulse rounded-full bg-muted-foreground/70"
          style={{ animationDelay: "0ms" }}
        />
        <span
          className="size-2 animate-pulse rounded-full bg-muted-foreground/70"
          style={{ animationDelay: "200ms" }}
        />
        <span
          className="size-2 animate-pulse rounded-full bg-muted-foreground/70"
          style={{ animationDelay: "400ms" }}
        />
      </span>
      <span>{label}</span>
    </output>
  );
}
