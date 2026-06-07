import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import en from "@/i18n/messages/en.json";
import { ApiError } from "@/lib/api/client";
import { isNoVisionError, NoVisionErrorBanner } from "./no-vision-error-banner";

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

function apiErr(
  body: { error?: string; detail?: unknown; context?: Record<string, string> },
  status = 422,
): ApiError {
  return new ApiError(status, body, {
    limit: null,
    remaining: null,
    reset: null,
    retryAfter: null,
  });
}

describe("isNoVisionError — F3 T15 detection", () => {
  it("matches error.code === 'no_vision_tier'", () => {
    expect(isNoVisionError(apiErr({ error: "no_vision_tier" }))).toBe(true);
  });

  it("matches context.reason === 'no_vision_tier'", () => {
    expect(
      isNoVisionError(
        apiErr({
          error: "validation_error",
          context: { reason: "no_vision_tier" },
        }),
      ),
    ).toBe(true);
  });

  it("does NOT match unrelated errors", () => {
    expect(isNoVisionError(apiErr({ error: "rate_limited" }, 429))).toBe(false);
    expect(isNoVisionError(apiErr({ error: "magic_bytes_mismatch" }))).toBe(
      false,
    );
  });

  it("does NOT match null error", () => {
    expect(isNoVisionError(null)).toBe(false);
  });
});

describe("<NoVisionErrorBanner>", () => {
  it("renders nothing when error is null (clean composer surface)", () => {
    const { container } = renderWithIntl(<NoVisionErrorBanner error={null} />);
    expect(container.querySelector("[data-slot='error-state']")).toBeNull();
  });

  it("renders nothing when error is unrelated", () => {
    const { container } = renderWithIntl(
      <NoVisionErrorBanner error={apiErr({ error: "rate_limited" })} />,
    );
    expect(container.querySelector("[data-slot='error-state']")).toBeNull();
  });

  it("renders F2 <ErrorState> with deployment-honest title (D-F3-X-no-vision-tooltip-copy)", () => {
    renderWithIntl(
      <NoVisionErrorBanner error={apiErr({ error: "no_vision_tier" })} />,
    );
    const banner = screen.getByText(en.chat.composer.attach.imageDisabled);
    expect(banner).toBeDefined();
  });

  it("surfaces the server detail when present", () => {
    renderWithIntl(
      <NoVisionErrorBanner
        error={apiErr({
          error: "no_vision_tier",
          detail: "configured tiers: deepseek-chat (text-only)",
        })}
      />,
    );
    expect(screen.getByText(/configured tiers: deepseek-chat/)).toBeDefined();
  });
});
