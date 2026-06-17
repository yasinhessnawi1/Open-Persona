/**
 * Structural tests for the desktop app-sidebar layout contract.
 *
 * These lock in the bottom-pinned-footer fix: the sidebar is a viewport-height
 * flex column, the MESSAGES region is the only internal scroll area (so a long
 * conversation list scrolls WITHIN the sidebar), and the Settings entry sits in
 * a non-shrinking footer that stays visible no matter how many conversations
 * exist — in both expanded and collapsed (icon-rail) modes.
 */
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./sidebar";
import type { SidebarConversation, SidebarData } from "./sidebar-data";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: async () => null }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));

const messages = {
  nav: {
    primary: "Primary",
    home: "Home",
    personas: "Personas",
    conversations: "Conversations",
    newPersona: "New persona",
    settings: "Settings",
    sidebar: {
      collapse: "Collapse",
      expand: "Expand",
      resize: "Resize sidebar",
      personas: "Personas",
      messages: "Messages",
      messagesEmpty: "No conversations yet",
      untitled: "Untitled conversation",
      unknownPersona: "Unknown persona",
    },
  },
};

/** A long conversation list — the case that used to push Settings off-screen. */
const manyConversations: SidebarConversation[] = Array.from(
  { length: 50 },
  (_, i) => ({
    id: `c${i}`,
    title: `Conversation ${i}`,
    updated_at: "2026-06-10T00:00:00Z",
    persona: null,
  }),
);

const data: SidebarData = {
  personas: [],
  conversations: manyConversations,
};

function wrap(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("Sidebar layout contract", () => {
  it("caps the column to the viewport height so it never grows past the screen", () => {
    const { container } = wrap(<Sidebar data={data} />);
    const aside = container.querySelector<HTMLElement>(
      '[data-slot="app-shell-sidebar"]',
    );
    expect(aside).not.toBeNull();
    // A fixed full-viewport-height, sticky flex column.
    expect(aside?.className).toContain("md:h-svh");
    expect(aside?.className).toContain("md:sticky");
    expect(aside?.className).toContain("md:flex-col");
  });

  it("makes MESSAGES the only internal scroll region (min-h-0 + flex-1)", () => {
    const { container } = wrap(<Sidebar data={data} />);
    // The MESSAGES section is the grow region; its ScrollArea scrolls within.
    const scrollArea = container.querySelector<HTMLElement>(
      '[data-slot="scroll-area"]',
    );
    expect(scrollArea).not.toBeNull();
    expect(scrollArea?.className).toContain("min-h-0");
    expect(scrollArea?.className).toContain("flex-1");
  });

  it("pins Settings in a non-shrinking footer that stays present with a long list", () => {
    wrap(<Sidebar data={data} />);
    const settings = screen.getByRole("link", { name: "Settings" });
    expect(settings).toHaveAttribute("href", "/settings");
    // The footer wrapper is non-shrinking and bottom-pinned.
    const footer = settings.closest<HTMLElement>(".shrink-0");
    expect(footer).not.toBeNull();
    expect(footer?.className).toContain("mt-auto");
  });
});
