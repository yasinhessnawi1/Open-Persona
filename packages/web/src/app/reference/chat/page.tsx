/**
 * Spec F1 T07 — Reference composition: chat with Astrid (light).
 *
 * The single-persona §4 proof + the D-F1-5 accent-not-wash composite. Fixture-
 * fed; no API; no live data. Uses the shared <ChatComposition> so T12's dark
 * variant renders the same structure under a different theme.
 *
 * What to look at:
 *   - Astrid's identity is felt without the UI shouting (avatar + name with
 *     1px identity-coloured underline + 2px identity-coloured message border).
 *   - The vermilion --primary stays the brightest accent in the room (the
 *     frontier tier badge); Astrid's identity colour never out-shouts it.
 *   - The streaming caret on the last message uses Astrid's identity colour,
 *     not vermilion — the caret is a *who's typing* signal, not a brand cue.
 *   - The tool-call cue (web_search) shows the system doing its work — the
 *     "instrument" half of the editorial-instrument north star.
 */
import { ChatComposition } from "../_chat-composition";
import { ASTRID, ASTRID_CHAT } from "../_fixtures";

export default function ChatLightPage() {
  return (
    <div className="space-y-10">
      <header className="space-y-2">
        <p className="type-caption text-muted-foreground">T07 · D-F1-5 · §4</p>
        <h1 className="type-display">Chat — Astrid (light)</h1>
        <p className="type-body text-muted-foreground max-w-prose">
          The single-persona accent-not-wash composite. Astrid's identity is
          three small accents (avatar + name underline + message border-left);
          message surface stays neutral so her words remain the figure.
        </p>
      </header>
      <ChatComposition persona={ASTRID} messages={ASTRID_CHAT} />
    </div>
  );
}
