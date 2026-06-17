/**
 * AppShell — the authenticated app frame: a rich, resizable/collapsible
 * desktop sidebar + a sticky header, with a mobile sheet at < md.
 *
 * Server component: it fetches the sidebar's recency-derived data once
 * (`fetchSidebarData`) and hands the serialisable result to the client
 * `<Sidebar>` (which owns resize/collapse + the section model) and to the
 * mobile `<MobileNav>` sheet. The header keeps the Clerk `<UserMenu />` +
 * `<ThemeToggle />` islands untouched (Phase-1 plumbing) — the account stays in
 * the header; Settings lives in the sidebar's pinned-bottom slot, not here.
 */

import type { ReactNode } from "react";
import { ToastProvider } from "@/components/patterns/toast";
import { Sidebar } from "@/components/shell/sidebar";
import { UserMenu } from "@/components/shell/user-menu";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";
import { MobileNav } from "./mobile-nav";
import type { SidebarData } from "./sidebar-data";
import { fetchSidebarData } from "./sidebar-fetch";

export async function AppShell({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const data = await fetchSidebarData();

  return (
    <div className={cn("flex min-h-svh", className)} data-slot="app-shell">
      <Sidebar data={data} />
      <div className="flex min-w-0 flex-1 flex-col">
        <ShellHeader data={data} />
        <main className="flex flex-1 flex-col" data-slot="app-shell-main">
          {children}
        </main>
      </div>
      {/* F2 T23: single toast surface for the auth'd app. */}
      <ToastProvider />
    </div>
  );
}

/**
 * Sticky header — mobile-nav trigger + theme toggle + Clerk UserButton.
 * The backdrop-blur + bg-background/85 reads as the F1 paper-on-paper lift;
 * `shadow-[var(--elevation-1)]` is the explicit resting elevation token.
 */
function ShellHeader({ data }: { data: SidebarData }) {
  return (
    <header
      className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b bg-background/85 px-4 shadow-[var(--elevation-1)] backdrop-blur"
      data-slot="app-shell-header"
    >
      <MobileNav data={data} />
      <div className="flex-1" />
      <ThemeToggle />
      <UserMenu />
    </header>
  );
}
