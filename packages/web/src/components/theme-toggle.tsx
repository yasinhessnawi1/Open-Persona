"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export function ThemeToggle() {
  const { setTheme } = useTheme();
  const t = useTranslations("theme");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t("toggle")}
        className={cn(buttonVariants({ variant: "ghost", size: "icon" }))}
        data-slot="theme-toggle-trigger"
      >
        {/*
         * F2 T24: --motion-duration-fast on the icon-swap so the theme
         * transition reads as a deliberate fade instead of an instant toggle.
         * The .dark utility class still drives visibility (dark:hidden /
         * dark:block); the transition smooths the opacity in/out. F1 T15
         * silences this under prefers-reduced-motion via universal !important.
         */}
        <Sun className="size-[1.2rem] transition-opacity duration-[var(--motion-duration-fast)] dark:hidden" />
        <Moon className="hidden size-[1.2rem] transition-opacity duration-[var(--motion-duration-fast)] dark:block" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <Sun className="mr-2 size-4" />
          {t("light")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <Moon className="mr-2 size-4" />
          {t("dark")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <Monitor className="mr-2 size-4" />
          {t("system")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
