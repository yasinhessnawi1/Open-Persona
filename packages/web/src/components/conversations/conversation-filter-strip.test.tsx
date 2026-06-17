/**
 * Regression test for the conversations infinite-navigation loop.
 *
 * Bug: the debounced search effect called router.replace() unconditionally,
 * with `search` (useSearchParams — a fresh reference every render) in its dep
 * array. Each replace → RSC refetch → new `search` ref → effect re-fires →
 * replace … forever (GET /conversations indefinitely, even with no input).
 *
 * The mock returns a NEW URLSearchParams on every call, faithfully reproducing
 * the unstable-reference condition that drove the loop.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationFilterStrip } from "./conversation-filter-strip";

const replace = vi.fn();
let searchString = "";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  // New instance per call — mirrors Next's real useSearchParams reference churn.
  useSearchParams: () => new URLSearchParams(searchString),
}));

const messages = {
  conversations: {
    allPersonas: "All",
    searchPlaceholder: "Search",
    delete: "Clear",
  },
};

function renderStrip() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ConversationFilterStrip personas={[]} />
    </NextIntlClientProvider>,
  );
}

describe("ConversationFilterStrip — no infinite navigation loop", () => {
  beforeEach(() => {
    replace.mockClear();
    searchString = "";
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not navigate when q already matches the URL (no-op → no loop)", () => {
    renderStrip();
    // Let any debounce timers fire repeatedly; the guard must keep replace silent.
    vi.advanceTimersByTime(2000);
    expect(replace).not.toHaveBeenCalled();
  });

  it("pushes q to the URL exactly once after debounce when the user types", () => {
    renderStrip();
    fireEvent.change(screen.getByLabelText("Search"), {
      target: { value: "hello" },
    });
    vi.advanceTimersByTime(300);
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("?q=hello", { scroll: false });
  });
});
