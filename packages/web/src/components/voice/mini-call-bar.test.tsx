/**
 * Spec V7 T2 — persistent mini call-bar.
 *
 * Drives the bar through a mocked `useCallSession` (the session contract is
 * owned + tested by `call-session-context.test.tsx`); here we assert the bar's
 * own behaviour: hidden when idle, the controls bind the session, the collapse
 * toggle, the return link, and the ARIA surface. Drag is pointer-only and is
 * proven by the close-out operator pass, not jsdom.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "@/i18n/messages/en.json";
import type { CallSession, CallTarget } from "@/lib/voice/call-session-context";
import {
  INITIAL_CALL_STATE,
  type VoiceCallState,
} from "@/lib/voice/call-state";
import { MiniCallBar } from "./mini-call-bar";

const h = vi.hoisted(() => ({
  session: null as CallSession | null,
  pathname: "/personas",
}));

vi.mock("@/lib/voice/call-session-context", () => ({
  useCallSession: () => h.session,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => h.pathname,
}));
vi.mock("@/components/persona/persona-avatar", () => ({
  PersonaAvatar: ({ persona }: { persona: { name: string } }) => (
    <span data-testid="avatar">{persona.name}</span>
  ),
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const TARGET: CallTarget = {
  personaId: "p-1",
  conversationId: "c-1",
  personaName: "Ada",
};

function makeSession(over: Partial<CallSession> = {}): CallSession {
  const state: VoiceCallState = {
    ...INITIAL_CALL_STATE,
    phase: "connected",
    agentState: "listening",
    micActive: true,
  };
  return {
    state,
    captions: [],
    artifacts: [],
    activities: [],
    target: TARGET,
    isActive: true,
    startedAt: Date.now(),
    pendingSwitch: null,
    start: vi.fn(),
    requestCall: vi.fn(),
    confirmSwitch: vi.fn(),
    cancelSwitch: vi.fn(),
    resumable: null,
    resumeCall: vi.fn(),
    dismissResume: vi.fn(),
    end: vi.fn(),
    toggleMute: vi.fn(),
    inputMode: "always",
    setInputMode: vi.fn(),
    pttHeld: false,
    setPttHeld: vi.fn(),
    enableAudio: vi.fn(),
    getMicLevel: () => 0,
    getPersonaLevel: () => 0,
    ...over,
  };
}

function renderBar() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <MiniCallBar />
    </NextIntlClientProvider>,
  );
}

describe("MiniCallBar", () => {
  beforeEach(() => {
    h.pathname = "/personas";
  });

  it("renders nothing when no call is active", () => {
    h.session = makeSession({ isActive: false, target: null });
    const { container } = renderBar();
    expect(container.querySelector('[data-slot="mini-call-bar"]')).toBeNull();
  });

  it("collapses away on the call's own full-view route (projection)", () => {
    h.session = makeSession();
    h.pathname = "/chat/c-1/voice";
    const { container } = renderBar();
    expect(container.querySelector('[data-slot="mini-call-bar"]')).toBeNull();
  });

  it("shows the persona, controls, and a return link when active", () => {
    h.session = makeSession();
    renderBar();
    expect(screen.getByRole("region")).toHaveAccessibleName(/Ada/);
    expect(screen.getAllByText("Ada").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Mute" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "End call" }),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Return to full call view" });
    expect(link).toHaveAttribute("href", "/chat/c-1/voice");
  });

  it("binds mute and end to the session", () => {
    const session = makeSession();
    h.session = session;
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: "Mute" }));
    expect(session.toggleMute).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "End call" }));
    expect(session.end).toHaveBeenCalledTimes(1);
  });

  it("mute control reflects mic state via aria-pressed", () => {
    h.session = makeSession({
      state: {
        ...INITIAL_CALL_STATE,
        phase: "connected",
        agentState: "listening",
        micActive: false,
      },
    });
    renderBar();
    // micActive false → muted → the control is pressed and labelled "Unmute".
    expect(screen.getByRole("button", { name: "Unmute" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("collapses to a compact puck and expands again", () => {
    h.session = makeSession();
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: "Collapse call bar" }));
    // Collapsed: the name/controls are gone, only the expand affordance remains.
    expect(
      screen.queryByRole("button", { name: "End call" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Expand call controls" }),
    );
    expect(
      screen.getByRole("button", { name: "End call" }),
    ).toBeInTheDocument();
  });
});
