import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "@/i18n/messages/en.json";
import {
  INITIAL_CALL_STATE,
  type VoiceCallState,
} from "@/lib/voice/call-state";
import { VoiceCallSurface } from "./voice-call-surface";

// A mutable handle the useVoiceCall mock reads — set per test (vi.hoisted so the
// hoisted vi.mock factory can close over it).
const h = vi.hoisted(() => ({
  state: null as VoiceCallState | null,
  start: vi.fn(),
  end: vi.fn(),
  toggleMute: vi.fn(),
  enableAudio: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: async () => "jwt" }),
}));
vi.mock("@/lib/voice/use-persona-avatar-src", () => ({
  usePersonaAvatarSrc: () => null,
}));
// The orb is rAF/identity-heavy and irrelevant here — stub it so we can assert
// presence (live) vs absence (terminal).
vi.mock("@/components/voice/identity-orb", () => ({
  IdentityOrb: () => <div data-testid="orb" />,
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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: h.replace, push: vi.fn() }),
}));
vi.mock("@/lib/voice/use-voice-call", () => ({
  useVoiceCall: () => ({
    state: h.state,
    captions: [],
    start: h.start,
    end: h.end,
    toggleMute: h.toggleMute,
    enableAudio: h.enableAudio,
    getMicLevel: () => 0,
    getPersonaLevel: () => 0,
  }),
}));

function renderSurface(state: VoiceCallState) {
  h.state = state;
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <VoiceCallSurface
        persona={{ id: "p1", name: "Astrid", role: "Advisor" }}
        conversationId="c1"
      />
    </NextIntlClientProvider>,
  );
}

const withPhase = (
  phase: VoiceCallState["phase"],
  error: VoiceCallState["error"] = null,
): VoiceCallState => ({ ...INITIAL_CALL_STATE, phase, error });

describe("VoiceCallSurface (C3) terminal states", () => {
  beforeEach(() => {
    h.start.mockClear();
    h.replace.mockClear();
  });

  it("renders the live orb + end control when connected (not a failure card)", () => {
    renderSurface(withPhase("connected"));
    expect(screen.getByTestId("orb")).toBeInTheDocument();
    // Spec 35: the end control is now an icon button — query its accessible name.
    expect(
      screen.getByRole("button", { name: "End call" }),
    ).toBeInTheDocument();
  });

  it("mic_denied error → kind-specific copy + a retry action, no orb", () => {
    renderSurface(
      withPhase("error", { kind: "mic_denied", message: "blocked" }),
    );
    expect(screen.getByText("Microphone blocked")).toBeInTheDocument();
    expect(screen.getByText("Try again")).toBeInTheDocument();
    expect(screen.queryByTestId("orb")).not.toBeInTheDocument();
  });

  it("unauthorized error → a sign-in link, not a retry", () => {
    renderSurface(
      withPhase("error", { kind: "unauthorized", message: "expired" }),
    );
    const signIn = screen.getByText("Sign in");
    expect(signIn).toHaveAttribute("href", "/sign-in");
    expect(screen.queryByText("Try again")).not.toBeInTheDocument();
  });

  it("not_found error → only the back arrow (nothing to retry)", () => {
    renderSurface(withPhase("error", { kind: "not_found", message: "gone" }));
    expect(screen.getByText("Persona unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Try again")).not.toBeInTheDocument();
    expect(screen.queryByText("Sign in")).not.toBeInTheDocument();
    // Back is the top-left arrow (aria-label), present in every state.
    expect(screen.getByLabelText("Back to chat")).toBeInTheDocument();
  });

  it("dropped → reconnect affordance", () => {
    renderSurface(withPhase("dropped"));
    expect(screen.getByText("Call dropped")).toBeInTheDocument();
    expect(screen.getByText("Try again")).toBeInTheDocument();
  });

  it("ended → navigates back to the conversation (no dead-end card)", () => {
    // Spec 35: a clean hang-up returns to the conversation instead of a
    // "Call ended" card — voice + text are one thread.
    renderSurface(withPhase("ended"));
    expect(screen.queryByText("Call ended")).toBeNull();
    expect(h.replace).toHaveBeenCalledWith("/chat/c1");
  });
});
