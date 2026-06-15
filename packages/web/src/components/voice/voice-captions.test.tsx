import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import type { CaptionSegment } from "@/lib/voice/captions";
import { VoiceCaptions } from "./voice-captions";

const messages = { voice: { you: "You", captions: "Live captions" } };

function renderCaptions(captions: CaptionSegment[]) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <VoiceCaptions captions={captions} personaName="Astrid" />
    </NextIntlClientProvider>,
  );
}

describe("VoiceCaptions (D-V6-2 dual-region)", () => {
  it("renders nothing when there are no captions", () => {
    const { container } = renderCaptions([]);
    expect(container.firstChild).toBeNull();
  });

  it("announces ONLY finalized segments in a polite log region (never partials)", () => {
    renderCaptions([
      { segmentId: "u0", speaker: "user", text: "hello there", isFinal: true },
      { segmentId: "p0", speaker: "persona", text: "Hi, I am", isFinal: false },
    ]);
    const log = screen.getByRole("log");
    // The SR region carries the finalized user line, NOT the persona partial.
    expect(log).toHaveTextContent("hello there");
    expect(log).not.toHaveTextContent("Hi, I am");
  });

  it("shows the in-progress persona partial visually (not in the SR region)", () => {
    renderCaptions([
      {
        segmentId: "p0",
        speaker: "persona",
        text: "thinking out loud",
        isFinal: false,
      },
    ]);
    // Visible (aria-hidden) caption shows the partial...
    expect(screen.getByText(/thinking out loud/)).toBeInTheDocument();
    // ...but the SR log stays empty (no finals yet).
    expect(screen.getByRole("log")).toBeEmptyDOMElement();
  });

  it("attributes the user as 'You' and the persona by name", () => {
    renderCaptions([
      { segmentId: "u0", speaker: "user", text: "x", isFinal: true },
      { segmentId: "p0", speaker: "persona", text: "y", isFinal: true },
    ]);
    expect(screen.getAllByText(/You:/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Astrid:/).length).toBeGreaterThan(0);
  });
});
