"use client";

/**
 * Spec P1 (D-P1-v7-indicator) — the active-chat-turn indicator.
 *
 * The chat sibling of V7's `ActiveCallIndicator`: a subtle "working" pulse shown
 * on a conversation row while that conversation has an in-progress detached turn
 * (matched by `conversationId` against the hoisted `ActiveWorkProvider`). A pure
 * read of the session — never per-component state — so it stays consistent
 * wherever the conversation appears.
 *
 * Rendered as a non-interactive span (the surrounding conversation row is
 * already the link to `/chat/{id}` — the one-tap "return to it"); the global
 * `ActiveWorkBar` is the from-anywhere link.
 */

import { useTranslations } from "next-intl";
import { useActiveWork } from "@/lib/work/active-work-context";

export function ActiveChatIndicator({
  conversationId,
  personaName,
}: {
  conversationId: string;
  personaName?: string;
}): React.JSX.Element | null {
  const t = useTranslations("work");
  const { isChatActive } = useActiveWork();

  if (!isChatActive(conversationId)) {
    return null;
  }

  return (
    <span
      data-slot="active-chat-indicator"
      title={t("indicator.return", { name: personaName ?? "" })}
      className="inline-flex items-center gap-1 type-caption normal-case tracking-normal text-primary"
    >
      <span
        className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none"
        aria-hidden="true"
      />
      {t("indicator.active")}
    </span>
  );
}
