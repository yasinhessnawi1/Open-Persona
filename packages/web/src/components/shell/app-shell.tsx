/**
 * Spec F2 T19 — AppShell.
 *
 * Replaces the scaffold's (app)/layout.tsx + the 5 shell files (app-sidebar /
 * brand / mobile-nav / nav / sidebar-body). The new shell consumes F1
 * tokens explicitly:
 *   - --elevation-1 for the sticky header backdrop;
 *   - --motion-duration-fast for header backdrop blur transition;
 *   - the mobile sheet (T07) carries --motion-duration-slow per its retokenise.
 *
 * Persistent sidebar (≥md breakpoint per D-F2-11 Tailwind defaults); mobile
 * sheet (< md). Clerk <UserButton /> + <ThemeToggle /> preserved in the
 * header (Phase-1 plumbing, untouched).
 *
 * Server component at the outer level; client children (MobileNav, ThemeToggle,
 * UserButton) hydrate as their own islands per D-F2-3.
 *
 * Scaffold-shell files (app-sidebar.tsx, brand.tsx, mobile-nav.tsx, nav.tsx,
 * sidebar-body.tsx) are still in tree — used by the scaffold's current
 * (app)/layout.tsx. The T19 swap (rewriting (app)/layout.tsx to import this
 * AppShell) deprecates them; T26 close fully removes them.
 */

import { UserButton } from "@clerk/nextjs";
import type { ReactNode } from "react";
import { ToastProvider } from "@/components/patterns/toast";
import { Brand } from "@/components/shell/brand";
import { SidebarBody } from "@/components/shell/sidebar-body";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";
import { MobileNav } from "./mobile-nav";

export function AppShell({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-svh", className)} data-slot="app-shell">
      <DesktopSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <ShellHeader />
        <main className="flex flex-1 flex-col" data-slot="app-shell-main">
          {children}
        </main>
      </div>
      {/* F2 T23: toast surface for the auth'd app. Single mount point so
       * `toast(...)` calls from any child render here regardless of route.
       */}
      <ToastProvider />
    </div>
  );
}

/**
 * Persistent sidebar (desktop only). Hidden at < md per D-F2-11; the mobile
 * sheet provides the equivalent navigation at narrower viewports.
 */
function DesktopSidebar() {
  return (
    <aside
      className="hidden w-64 shrink-0 flex-col gap-6 border-r border-sidebar-border bg-sidebar p-4 md:flex"
      data-slot="app-shell-sidebar"
    >
      <Brand className="px-1 pt-1" />
      <SidebarBody />
    </aside>
  );
}

/**
 * Sticky header — mobile-nav trigger + theme toggle + Clerk UserButton.
 * The backdrop-blur + bg-background/85 reads as the F1 paper-on-paper
 * lift; F2 promotes the explicit elevation token via inline shadow.
 *
 * F2 T19 retokenise:
 *   - `shadow-[var(--elevation-1)]` for the explicit-on-token resting lift.
 *   - The backdrop blur tier is a Tailwind v4 utility (`backdrop-blur`) —
 *     positional, not a design value.
 */
function ShellHeader() {
  return (
    <header
      className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b bg-background/85 px-4 shadow-[var(--elevation-1)] backdrop-blur"
      data-slot="app-shell-header"
    >
      <MobileNav />
      <div className="flex-1" />
      <ThemeToggle />
      <UserButton />
    </header>
  );
}
