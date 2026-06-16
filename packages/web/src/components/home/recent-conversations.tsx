import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { getFormatter, getTranslations } from "next-intl/server";
import {
  type AvatarPersona,
  PersonaAvatar,
} from "@/components/persona/persona-avatar";

/**
 * Root-dashboard "resume" list: the caller's most-recent conversations, each a
 * one-click jump back into the thread. Reuses the existing `/v1/conversations`
 * data (already sorted `updated_at DESC` server-side) so no new endpoint and no
 * recency schema are needed. Compact rows (avatar + title + persona · date),
 * mirroring the F5 conversation-row shape but trimmed for the dashboard.
 *
 * Server component — pure presentational; the resolved data is passed in.
 */
export interface RecentConversationItem {
  readonly id: string;
  readonly title: string;
  readonly updated_at: string;
  readonly persona: AvatarPersona | null;
}

export async function RecentConversations({
  conversations,
}: {
  conversations: readonly RecentConversationItem[];
}) {
  const t = await getTranslations("home");
  const format = await getFormatter();

  return (
    <ul className="flex flex-col" data-slot="recent-conversations">
      {conversations.map((c) => (
        <li key={c.id} className="group/recent border-b last:border-b-0">
          <Link href={`/chat/${c.id}`} className="flex items-center gap-3 py-3">
            {c.persona ? (
              <PersonaAvatar persona={c.persona} size="sm" />
            ) : (
              <span className="size-6 rounded-full bg-muted" aria-hidden />
            )}
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="type-body block truncate font-medium">
                {c.title || t("recent.untitled")}
              </span>
              <span className="type-caption text-muted-foreground">
                {c.persona ? c.persona.name : t("recent.unknownPersona")}
                {" · "}
                {format.dateTime(new Date(c.updated_at), {
                  dateStyle: "medium",
                })}
              </span>
            </span>
            <ChevronRight
              className="size-4 shrink-0 text-muted-foreground transition-transform duration-[var(--motion-duration-fast)] group-hover/recent:translate-x-0.5 motion-reduce:transition-none motion-reduce:group-hover/recent:translate-x-0"
              aria-hidden="true"
            />
          </Link>
        </li>
      ))}
    </ul>
  );
}
