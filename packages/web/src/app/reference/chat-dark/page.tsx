/**
 * Spec F1 T12 — Reference composition: chat (dark).
 *
 * The same T07 chat with Astrid, rendered in dark mode. D-F1-6 proof:
 *   - The warm dark base (oklch(0.19 0.008 60)) preserves editorial warmth.
 *   - Astrid's derived identity colour stays the same hue across modes
 *     (Astrid is still Astrid); only the surrounding ink/paper inverts.
 *   - The token swap is the ONLY mechanism — no parallel dark-mode rules.
 *
 * Implementation: this page forces the `dark` class on its own root scope
 * so the dark token set applies without the user having to toggle their OS
 * theme. The next-themes wiring in layout.tsx is bypassed for this single
 * route — the wrapper sets `.dark` directly.
 */
import { ChatComposition } from "../_chat-composition";
import { ASTRID, ASTRID_CHAT } from "../_fixtures";

export default function ChatDarkPage() {
  return (
    <div className="dark bg-background text-foreground -mx-6 -my-10 min-h-dvh px-6 py-10">
      <div className="space-y-10">
        <header className="space-y-2">
          <p className="type-caption text-muted-foreground">
            T12 · D-F1-6 · §11.10
          </p>
          <h1 className="type-display">Chat — Astrid (dark)</h1>
          <p className="type-body text-muted-foreground max-w-prose">
            The same composition as{" "}
            <code className="type-code">/reference/chat</code>, rendered in dark
            mode via the existing token-swap mechanism. Astrid's identity colour
            stays the same hue; only paper/ink inverts. The warm dark base
            preserves editorial warmth.
          </p>
        </header>
        <ChatComposition persona={ASTRID} messages={ASTRID_CHAT} />
      </div>
    </div>
  );
}
