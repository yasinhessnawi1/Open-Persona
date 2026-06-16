import { MessageSquare, Mic } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { startChat, startVoice } from "@/app/actions";
import {
  type AvatarPersona,
  PersonaAvatar,
} from "@/components/persona/persona-avatar";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Root-dashboard fast launcher: a COMPACT row (avatar + name/role + one-click
 * Chat / Call), NOT the full management `<PersonaCard>` grid card. The point of
 * the dashboard is "get back to a persona and start talking fast" — so this is
 * deliberately distinct from `/personas` (which stays the management grid).
 *
 * Chat + Call mint a fresh conversation via the relocated co-located server
 * actions (voice has no standalone route; it hangs off a conversation). Server
 * component — actions are bound per persona, so no client hydration is needed.
 */
export interface QuickLaunchPersona extends AvatarPersona {
  readonly role: string;
}

export async function QuickLaunchItem({
  persona,
}: {
  persona: QuickLaunchPersona;
}) {
  const t = await getTranslations("home");

  return (
    <Card
      size="sm"
      className="flex flex-row items-center gap-4 p-4"
      data-slot="quick-launch-item"
    >
      <PersonaAvatar persona={persona} size="md" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="type-body truncate font-heading font-medium">
          {persona.name}
        </span>
        <span className="type-caption truncate text-muted-foreground">
          {persona.role}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <form action={startChat.bind(null, persona.id)}>
          <button
            type="submit"
            className={cn(buttonVariants({ size: "sm" }), "gap-1.5")}
          >
            <MessageSquare className="size-4" aria-hidden="true" />
            {t("entry.chat")}
          </button>
        </form>
        <form action={startVoice.bind(null, persona.id)}>
          <button
            type="submit"
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
              "gap-1.5",
            )}
          >
            <Mic className="size-4" aria-hidden="true" />
            {t("entry.call")}
          </button>
        </form>
      </div>
    </Card>
  );
}
