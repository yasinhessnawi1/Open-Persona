/**
 * Spec F1 reference compositions — shared fixture data.
 *
 * Fixture personas (T08 list + T07 chat anchor + every other reference page)
 * draw from this single source so the language is consistent across the six
 * compositions. The shapes match `AvatarPersona` (from PersonaAvatar) so the
 * derivation function reaches them; richer fields (role, character lines,
 * sample messages) live alongside.
 *
 * NOT live data. NOT API-shaped. Just enough fixture for static rendering.
 */
import type { AvatarPersona } from "@/components/persona/persona-avatar";

export interface ReferencePersona extends AvatarPersona {
  /** Display role line under the name. */
  role: string;
  /** One-sentence character cue surfaced in identity headers. */
  character: string;
}

export const ASTRID: ReferencePersona = {
  id: "astrid_tenancy_law",
  name: "Astrid",
  role: "Norwegian tenancy law assistant",
  character: "Cites husleieloven sections. Never gives binding legal advice.",
};

export const KAI: ReferencePersona = {
  id: "kai_research",
  name: "Kai",
  role: "Research assistant",
  character: "Cross-checks claims against primary sources before answering.",
};

export const MAREN: ReferencePersona = {
  id: "maren_writing_coach",
  name: "Maren",
  role: "Writing coach",
  character:
    "Edits for clarity and rhythm; never the substance of the argument.",
};

export const REFERENCE_PERSONAS: readonly ReferencePersona[] = [
  ASTRID,
  KAI,
  MAREN,
];

/* ---- Chat fixture (T07 + T12) ----------------------------------------- */

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  /** Markdown-able body. Rendered as plain text in the reference comp to keep
   *  the focus on the design language, not the markdown stack. */
  content: string;
  /** Tier badge shown beneath the final assistant message of a turn. */
  tier?: "small" | "mid" | "frontier";
  /** If true, the assistant message is mid-stream (caret visible). */
  streaming?: boolean;
  /** Tool calls preceding the assistant response (one per call). */
  tools?: ChatToolCall[];
}

export interface ChatToolCall {
  toolName: string;
  args: Record<string, string>;
  state: "running" | "result";
  result?: string;
}

/** A representative Astrid conversation — turn 2 of a mid-conversation. */
export const ASTRID_CHAT: ChatMessage[] = [
  {
    id: "m1",
    role: "user",
    content:
      "Min utleier vil holde tilbake hele depositumet for slitasje på et 4-årig leieforhold. Er det lovlig?",
  },
  {
    id: "m2",
    role: "assistant",
    content:
      "Etter husleieloven § 3-5 kan utleier kun trekke fra depositumet for skader utover *normal slitasje*. Etter 4 år forventes ikke at boligen leveres tilbake i samme stand som ved innflytting — slitasje på maling, gulv og hvitevarer er som regel normalt.\n\nKan du beskrive hva utleier konkret trekker for?",
    tier: "frontier",
  },
  {
    id: "m3",
    role: "user",
    content:
      "Hele veggen i stua må males på nytt (gult fra røyking) og kjøkkenbenken har en skrubb.",
  },
  {
    id: "m4",
    role: "assistant",
    tools: [
      {
        toolName: "web_search",
        args: { q: "husleieloven §3-5 normal slitasje røyking depositum" },
        state: "result",
        result: "5 results · 1.2s",
      },
    ],
    content:
      "Røykskader er en av de få konkrete unntakene fra «normal slitasje» i praksis — utleier har ofte krav på å trekke for repainting når en boligrettsdom fastslår røyk som årsak. Husleietvistutvalget har flere saker hvor dette holdt.\n\nKjøkkenbenken er sannsynligvis normal slitasje på 4 år, men avhenger av størrelsen og plasseringen av skrubben.\n\nMin anbefaling: be utleier dokumentere kostnaden for repainting (faktura) og kostnaden for kjøkkenbenken separat — du kan motsette deg sistnevnte. Vil du at jeg utarbeider en utkast til svar?",
    streaming: true,
    tier: "frontier",
  },
];
