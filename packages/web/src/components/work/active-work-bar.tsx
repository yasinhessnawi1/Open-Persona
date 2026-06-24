"use client";

/**
 * Spec P1 (D-P1-v7-indicator) — the global "active work" bar.
 *
 * The chat/run sibling of V7's mini call-bar: a subtle, from-anywhere
 * "working — return to it" affordance shown while one or more conversations
 * have an in-progress detached turn. Hoisted in `AppShell` ALONGSIDE
 * `<MiniCallBar/>` (additive — V7's component is untouched), so the two read as
 * one "active session" system. Hidden when no chat work is in flight.
 *
 * It is itself the one-tap return: a link to a working conversation. Sits above
 * the mini call-bar's bottom-right slot so the rare both-active case stacks
 * cleanly rather than overlapping.
 */

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useActiveWork } from "@/lib/work/active-work-context";

export function ActiveWorkBar(): React.JSX.Element | null {
  const t = useTranslations("work");
  const { activeChats } = useActiveWork();

  if (activeChats.length === 0) {
    return null;
  }
  const first = activeChats[0];

  return (
    <Link
      href={`/chat/${first.conversationId}`}
      data-slot="active-work-bar"
      aria-label={t("bar.return")}
      className="fixed right-4 bottom-20 z-30 inline-flex items-center gap-2 rounded-full border bg-background/95 px-3 py-1.5 type-caption normal-case tracking-normal text-primary shadow-[var(--elevation-1)] backdrop-blur transition-colors duration-[var(--motion-duration-fast)] hover:bg-background focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span
        className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none"
        aria-hidden="true"
      />
      {t("bar.label", { count: activeChats.length })}
    </Link>
  );
}
