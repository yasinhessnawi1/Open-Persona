/**
 * Spec F1 T07 — chat reference composition (shared between light /reference/chat
 * and dark /reference/chat-dark, T12).
 *
 * Implements the D-F1-5 accent-not-wash COMPOSITE:
 *   1. <PersonaAvatar> in identity-coloured fill at the identity header.
 *   2. Persona name in Fraunces with a 1px identity-coloured UNDERLINE beneath
 *      the name only (from Variant B exploration).
 *   3. Persona messages carry a 2px identity-coloured BORDER-LEFT; message
 *      surface stays bg-card (neutral).
 *   4. User messages unchanged from scaffold (right-aligned bg-secondary).
 *   5. Streaming caret on the last assistant message.
 *   6. Tier badge on completed turns.
 *
 * Three small accents per persona (avatar + underline + border), never a wash.
 * The §4 individuality + accent-not-wash test: Astrid's presence is felt
 * everywhere it should be, nowhere it shouldn't.
 *
 * Identity-coloured borders/underlines consume the same `--identity-*` CSS
 * vars that <PersonaAvatar> sets — derived once, used everywhere in the
 * subtree. No re-deriving, no inline OKLCH per-element.
 */
import { PersonaAvatar } from "@/components/persona/persona-avatar";
import { personaIdentityStyle } from "@/lib/persona-identity";
import { cn } from "@/lib/utils";
import type { ChatMessage, ReferencePersona } from "./_fixtures";

const TIER_CLASSES: Record<string, string> = {
  frontier: "border-tier-frontier/40 text-tier-frontier",
  mid: "border-tier-mid/50 text-tier-mid",
  small: "border-tier-small/50 text-tier-small",
};

/** The OKLCH expression consuming the identity CSS vars. Used on inline
 *  borderLeftColor / borderBottomColor so the colour resolves at the same
 *  --identity-h/l/c the avatar sets. */
const IDENTITY_OKLCH =
  "oklch(var(--identity-l) var(--identity-c) var(--identity-h))";

function PersonaIdentityHeader({ persona }: { persona: ReferencePersona }) {
  return (
    <header
      style={personaIdentityStyle(persona)}
      className="border-border flex items-start gap-4 border-b pb-5"
    >
      <PersonaAvatar persona={persona} size="md" />
      <div className="min-w-0 flex-1">
        <p
          // The 1px identity-coloured underline beneath the name — D-F1-5
          // (Variant B contribution). Inline-block keeps the underline tight
          // to the name itself rather than spanning the line.
          style={{
            borderBottomColor: IDENTITY_OKLCH,
            borderBottomWidth: "1px",
            borderBottomStyle: "solid",
          }}
          className="type-display inline-block leading-tight"
        >
          {persona.name}
        </p>
        <p className="type-ui text-muted-foreground mt-1">{persona.role}</p>
        <p className="type-body text-muted-foreground/80 mt-2 italic">
          {persona.character}
        </p>
      </div>
    </header>
  );
}

function StreamingCaret() {
  return (
    <span
      // 3px-wide, identity-coloured. The motion-token-driven `animate-pulse`
      // is the scaffold default; T15 will swap it to a static dot under
      // prefers-reduced-motion. Keeps functional streaming intact under all
      // motion settings.
      style={{ background: IDENTITY_OKLCH }}
      className="ml-0.5 inline-block h-4 w-[3px] translate-y-0.5 animate-pulse rounded-full"
      aria-hidden
    />
  );
}

function ToolCallCue({
  tool,
}: {
  tool: NonNullable<ChatMessage["tools"]>[number];
}) {
  return (
    <div
      style={{ borderLeftColor: IDENTITY_OKLCH }}
      className="bg-muted/40 type-caption text-muted-foreground border-border border border-l-2 rounded px-3 py-2 normal-case tracking-normal not-italic"
    >
      <span className="text-foreground/70 font-mono">{tool.toolName}</span>
      <span className="text-muted-foreground"> · </span>
      <span className="text-muted-foreground">{tool.args.q ?? ""}</span>
      {tool.result ? (
        <>
          <span className="text-muted-foreground"> → </span>
          <span className="text-foreground/70">{tool.result}</span>
        </>
      ) : null}
    </div>
  );
}

function PersonaMessage({
  message,
  persona,
}: {
  message: ChatMessage;
  persona: ReferencePersona;
}) {
  // The 2px identity-coloured border-left + neutral bg-card — D-F1-5 lock.
  return (
    <div
      style={{
        ...personaIdentityStyle(persona),
        borderLeftColor: IDENTITY_OKLCH,
      }}
      className="bg-card border-border space-y-3 rounded-r border border-l-2 py-3 pr-4 pl-4"
    >
      {message.tools?.length ? (
        <div className="space-y-2">
          {message.tools.map((t, i) => (
            <ToolCallCue key={`${t.toolName}-${i}`} tool={t} />
          ))}
        </div>
      ) : null}
      <p className="type-body text-foreground whitespace-pre-wrap">
        {message.content}
        {message.streaming ? <StreamingCaret /> : null}
      </p>
      {message.tier && !message.streaming ? (
        <span
          className={cn(
            "type-caption inline-flex w-fit items-center rounded border px-1.5 py-0.5",
            TIER_CLASSES[message.tier],
          )}
          title={`${message.tier} tier`}
        >
          {message.tier}
        </span>
      ) : null}
    </div>
  );
}

function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="flex justify-end">
      <div className="bg-secondary text-secondary-foreground type-body max-w-[80%] rounded-2xl rounded-br-sm px-4 py-2.5 whitespace-pre-wrap">
        {message.content}
      </div>
    </div>
  );
}

export function ChatComposition({
  persona,
  messages,
}: {
  persona: ReferencePersona;
  messages: ChatMessage[];
}) {
  return (
    <div className="space-y-6">
      <PersonaIdentityHeader persona={persona} />
      <div className="space-y-4">
        {messages.map((message) =>
          message.role === "user" ? (
            <UserMessage key={message.id} message={message} />
          ) : (
            <PersonaMessage
              key={message.id}
              message={message}
              persona={persona}
            />
          ),
        )}
      </div>
    </div>
  );
}
