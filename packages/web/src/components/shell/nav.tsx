"use client";

import { Home, MessagesSquare, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

// Primary nav links. Settings is NOT here — it is pinned to the bottom of the
// desktop sidebar (and rendered in the mobile sheet's pinned footer).
const ITEMS = [
  { href: "/", key: "home", icon: Home },
  { href: "/personas", key: "personas", icon: Sparkles },
  { href: "/conversations", key: "conversations", icon: MessagesSquare },
] as const;

export function Nav({
  onNavigate,
  collapsed = false,
}: {
  onNavigate?: () => void;
  collapsed?: boolean;
}) {
  const pathname = usePathname();
  const t = useTranslations("nav");
  return (
    <nav aria-label={t("primary")} className="flex flex-col gap-1">
      {ITEMS.map(({ href, key, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        const link = (
          <Link
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            aria-label={collapsed ? t(key) : undefined}
            className={cn(
              "flex items-center rounded-md text-sm font-medium outline-none transition-colors duration-[var(--motion-duration-fast)] focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
              collapsed ? "size-9 justify-center mx-auto" : "gap-3 px-3 py-2",
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" />
            {collapsed ? null : t(key)}
          </Link>
        );

        if (collapsed) {
          return (
            <Tooltip key={href}>
              <TooltipTrigger render={link} />
              <TooltipContent side="right">{t(key)}</TooltipContent>
            </Tooltip>
          );
        }
        return <span key={href}>{link}</span>;
      })}
    </nav>
  );
}
