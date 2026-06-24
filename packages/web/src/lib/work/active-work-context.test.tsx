import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActiveChatIndicator } from "@/components/work/active-chat-indicator";
import { ActiveWorkBar } from "@/components/work/active-work-bar";
import { useChat } from "@/lib/hooks/use-chat";
import {
  ActiveWorkProvider,
  useActiveWork,
} from "@/lib/work/active-work-context";

/**
 * Spec P1 (T9) — the active-work session: provider + indicators.
 *
 * Tracks in-progress detached chat turns so the conversation row + global bar
 * advertise resumable work; a poll clears the indicator when a turn finishes
 * WHILE the user is away (the chat hook is unmounted then).
 */

vi.mock("@clerk/nextjs", () => {
  const getToken = () => Promise.resolve("test-jwt-token");
  return { useAuth: () => ({ getToken }) };
});

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, vars?: Record<string, unknown>) =>
    vars ? `${key}:${JSON.stringify(vars)}` : key,
}));

const wrapper = ({ children }: { children: ReactNode }) => (
  <ActiveWorkProvider>{children}</ActiveWorkProvider>
);

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("ActiveWorkProvider", () => {
  it("register makes a conversation active; unregister clears it", () => {
    const { result } = renderHook(() => useActiveWork(), { wrapper });
    expect(result.current.isChatActive("c1")).toBe(false);

    act(() =>
      result.current.registerChat({ conversationId: "c1", personaId: "p1" }),
    );
    expect(result.current.isChatActive("c1")).toBe(true);
    expect(result.current.activeChats).toHaveLength(1);

    act(() => result.current.unregisterChat("c1"));
    expect(result.current.isChatActive("c1")).toBe(false);
  });

  it("the default (no provider) is a safe no-op so useChat works untethered", () => {
    const { result } = renderHook(() => useActiveWork()); // no wrapper
    expect(result.current.isChatActive("c1")).toBe(false);
    act(() =>
      result.current.registerChat({ conversationId: "c1", personaId: "p1" }),
    );
    expect(result.current.isChatActive("c1")).toBe(false); // no-op default
  });

  it("the poll clears an indicator when the turn finished while away (active-turn 404)", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "turn_not_active" }), {
          status: 404,
        }),
    ) as unknown as typeof fetch;
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useActiveWork(), { wrapper });
      act(() =>
        result.current.registerChat({ conversationId: "c1", personaId: "p1" }),
      );
      expect(result.current.isChatActive("c1")).toBe(true);

      // Advance past the poll interval; the 404 reconciles the indicator away.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4500);
      });
      expect(result.current.isChatActive("c1")).toBe(false);
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe("ActiveChatIndicator + ActiveWorkBar", () => {
  function Harness({ conversationId }: { conversationId: string }) {
    const { registerChat } = useActiveWork();
    return (
      <div>
        <button
          type="button"
          onClick={() => registerChat({ conversationId, personaId: "p1" })}
        >
          start
        </button>
        <ActiveChatIndicator
          conversationId={conversationId}
          personaName="Astrid"
        />
        <ActiveWorkBar />
      </div>
    );
  }

  it("the indicator + bar are hidden until a turn is active, then appear", async () => {
    render(
      <ActiveWorkProvider>
        <Harness conversationId="c1" />
      </ActiveWorkProvider>,
    );
    expect(screen.queryByText("indicator.active")).toBeNull();
    expect(screen.queryByText(/bar\.label/)).toBeNull();

    act(() => screen.getByText("start").click());
    await waitFor(() =>
      expect(screen.getByText("indicator.active")).toBeTruthy(),
    );
    // The global bar links to the working conversation (one-tap return).
    const bar = document.querySelector('[data-slot="active-work-bar"]');
    expect(bar?.getAttribute("href")).toBe("/chat/c1");
  });
});

describe("useChat ↔ ActiveWorkProvider wiring", () => {
  function ChatHarness({ onActive }: { onActive: (active: boolean) => void }) {
    const { send } = useChat("c1", [], "p1");
    const { isChatActive } = useActiveWork();
    onActive(isChatActive("c1"));
    return (
      <button type="button" onClick={() => void send("hi")}>
        send
      </button>
    );
  }

  it("registers the conversation as active while its turn streams", async () => {
    // active-turn → 404 (no reattach); the POST tail stays open so streaming
    // (and the registration) persists for the assertion.
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(async (url: string | URL | Request) => {
      const u = url instanceof Request ? url.url : url.toString();
      if (u.endsWith("/active-turn")) {
        return new Response(JSON.stringify({ error: "turn_not_active" }), {
          status: 404,
        });
      }
      // POST /messages — a tail that emits a chunk and never closes.
      return new Response(
        new ReadableStream({
          start(c) {
            c.enqueue(
              new TextEncoder().encode(
                `event: chunk\ndata: ${JSON.stringify({ delta: "hi", is_final: false })}\n\n`,
              ),
            );
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    }) as unknown as typeof fetch;
    let active = false;
    try {
      render(
        <ActiveWorkProvider>
          <ChatHarness
            onActive={(a) => {
              active = a;
            }}
          />
        </ActiveWorkProvider>,
      );
      act(() => screen.getByText("send").click());
      await waitFor(() => expect(active).toBe(true));
    } finally {
      globalThis.fetch = original;
    }
  });
});
