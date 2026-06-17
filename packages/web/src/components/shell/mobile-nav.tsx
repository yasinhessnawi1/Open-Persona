"use client";

import { Menu } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { Brand } from "./brand";
import { SidebarBody } from "./sidebar-body";
import type { SidebarData } from "./sidebar-data";

export function MobileNav({ data }: { data: SidebarData }) {
  const [open, setOpen] = useState(false);
  const t = useTranslations("nav");
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        aria-label={t("menu")}
        className={cn(
          buttonVariants({ variant: "ghost", size: "icon" }),
          "md:hidden",
        )}
      >
        <Menu className="size-5" />
      </SheetTrigger>
      <SheetContent side="left" className="flex w-72 flex-col bg-sidebar p-4">
        <SheetTitle className="sr-only">{t("menu")}</SheetTitle>
        <div className="flex h-full min-h-0 flex-col gap-6">
          <Brand className="px-1" />
          <SidebarBody data={data} onNavigate={() => setOpen(false)} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
