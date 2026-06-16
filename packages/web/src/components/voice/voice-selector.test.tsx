import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { VoiceSelector } from "./voice-selector";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: async () => "jwt" }),
}));

vi.mock("@/lib/voice/voices", () => ({
  fetchVoices: vi.fn(async () => ({
    provider: "cartesia",
    voices: [
      {
        voice_id: "v1",
        name: "Clara",
        gender: "feminine",
        language: "en",
        description: "warm",
        preview_url: "https://cdn/c.mp3",
      },
    ],
  })),
}));

const messages = {
  voice: {
    voiceDefault: "Default voice",
    voicePreview: "Preview",
    voiceStop: "Stop",
    voiceSelected: "Selected",
    voicesLoading: "Loading voices…",
    voicesUnavailable: "Voice selection is unavailable right now.",
    voicesError: "Couldn't load voices.",
  },
};

function renderSelector(props: {
  value?: string | null;
  onChange: (v: { provider: string; voice_id: string } | null) => void;
}) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <VoiceSelector {...props} />
    </NextIntlClientProvider>,
  );
}

describe("VoiceSelector (C2)", () => {
  it("lists catalogue voices; choosing one sets the full {provider, voice_id}", async () => {
    const onChange = vi.fn();
    renderSelector({ value: null, onChange });
    await waitFor(() => expect(screen.getByText("Clara")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Clara"));
    expect(onChange).toHaveBeenCalledWith({
      provider: "cartesia",
      voice_id: "v1",
    });
  });

  it("offers a default option that clears the persona's voice", async () => {
    const onChange = vi.fn();
    renderSelector({ value: "v1", onChange });
    await waitFor(() =>
      expect(screen.getByText("Default voice")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByText("Default voice"));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("renders a preview control for a voice that has a sample", async () => {
    renderSelector({ value: null, onChange: vi.fn() });
    await waitFor(() =>
      expect(screen.getByLabelText("Preview")).toBeInTheDocument(),
    );
  });
});
