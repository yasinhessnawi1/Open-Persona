import Link from "next/link";
import { AuthedAvatarImage } from "@/components/persona/authed-avatar-image";
import { internalWorkspacePath } from "@/components/persona/persona-avatar";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Card } from "@/components/ui/card";
import type { PersonaSummary } from "@/lib/api";
import { personaInitials } from "@/lib/persona";

export function PersonaCard({ persona }: { persona: PersonaSummary }) {
  // Auto-generated/uploaded avatars sit behind the Bearer-authed serve route;
  // a raw <img> (Base UI AvatarImage) can't load them (no auth header → 404,
  // then the fallback initials show). Route those through the authed-image
  // hook so the generated avatar actually renders; external URLs keep the
  // plain Base UI Avatar path.
  const workspacePath = persona.avatar_url
    ? internalWorkspacePath(persona.avatar_url)
    : null;
  const initialsFallback = (
    <span className="grid size-11 shrink-0 place-items-center rounded-full bg-primary/10 font-heading font-medium text-primary">
      {personaInitials(persona.name)}
    </span>
  );
  return (
    <Link href={`/personas/${persona.id}`} className="group block">
      <Card className="flex flex-row items-center gap-4 p-4 transition-colors group-hover:border-primary/40 group-hover:bg-accent/40">
        {workspacePath ? (
          <AuthedAvatarImage
            personaId={persona.id}
            workspacePath={workspacePath}
            wrapperClassName="size-11 shrink-0"
            fallback={initialsFallback}
          />
        ) : (
          <Avatar className="size-11 shrink-0">
            {persona.avatar_url ? (
              <AvatarImage src={persona.avatar_url} alt="" />
            ) : null}
            <AvatarFallback className="bg-primary/10 font-heading font-medium text-primary">
              {personaInitials(persona.name)}
            </AvatarFallback>
          </Avatar>
        )}
        <div className="min-w-0">
          <p className="truncate font-heading text-lg leading-tight font-semibold">
            {persona.name}
          </p>
          <p className="truncate text-sm text-muted-foreground">
            {persona.role}
          </p>
        </div>
      </Card>
    </Link>
  );
}
