"use client";

import { Home, MessagesSquare, Settings, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/", key: "home", icon: Home },
  { href: "/personas", key: "personas", icon: Sparkles },
  { href: "/conversations", key: "conversations", icon: MessagesSquare },
  { href: "/settings", key: "settings", icon: Settings },
] as const;

export function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const t = useTranslations("nav");
  return (
    <nav aria-label={t("primary")} className="flex flex-col gap-1">
      {ITEMS.map(({ href, key, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" />
            {t(key)}
          </Link>
        );
      })}
    </nav>
  );
}
