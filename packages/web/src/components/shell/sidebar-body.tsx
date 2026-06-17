"use client";

import { Plus, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { buttonVariants } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { Nav } from "./nav";
import type { SidebarData } from "./sidebar-data";
import { MessagesList, PersonasRail } from "./sidebar-sections";

/**
 * Shared inner content for the MOBILE sheet. Mirrors the desktop section model
 * (New persona · Nav · PERSONAS · MESSAGES · pinned Settings) but always in the
 * expanded layout — the sheet has no resize/collapse affordance. Desktop uses
 * the richer `<Sidebar>` (resize + collapse) directly.
 */
export function SidebarBody({
  data,
  onNavigate,
}: {
  data: SidebarData;
  onNavigate?: () => void;
}) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const settingsActive = pathname === "/settings";

  return (
    <TooltipProvider>
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        <Link
          href="/personas/new"
          onClick={onNavigate}
          className={cn(buttonVariants(), "justify-start gap-2")}
        >
          <Plus className="size-4" />
          {t("newPersona")}
        </Link>

        <Nav onNavigate={onNavigate} />

        <Separator className="bg-sidebar-border" />

        <section className="flex flex-col gap-1.5">
          <h2 className="px-2 type-caption text-muted-foreground">
            {t("sidebar.personas")}
          </h2>
          <PersonasRail
            personas={data.personas}
            collapsed={false}
            onNavigate={onNavigate}
          />
        </section>

        <section className="flex min-h-0 flex-1 flex-col gap-1.5">
          <h2 className="px-2 type-caption text-muted-foreground">
            {t("sidebar.messages")}
          </h2>
          <div className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1">
            <MessagesList
              conversations={data.conversations}
              collapsed={false}
              onNavigate={onNavigate}
            />
          </div>
        </section>

        <div className="mt-auto">
          <Separator className="mb-2 bg-sidebar-border" />
          <Link
            href="/settings"
            onClick={onNavigate}
            aria-current={settingsActive ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              settingsActive
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )}
          >
            <Settings className="size-4 shrink-0" />
            {t("settings")}
          </Link>
        </div>
      </div>
    </TooltipProvider>
  );
}
