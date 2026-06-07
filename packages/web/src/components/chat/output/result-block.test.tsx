import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import en from "@/i18n/messages/en.json";

import { ResultBlock } from "./result-block";

// React.lazy → import("./highlighted-code"). We don't want to actually load
// Shiki in tests (heavy + async). Mock the lazy chunk's default export so
// `<Suspense>` resolves immediately with a deterministic shape.
vi.mock("./highlighted-code", () => ({
  default: ({ code, lang }: { code: string; lang: string }) => (
    <pre data-testid="highlighted-code" data-lang={lang}>
      {code}
    </pre>
  ),
}));

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("<ResultBlock> (T07)", () => {
  describe("plain stdout rendering", () => {
    it("renders short stdout without a show-full toggle", () => {
      renderWithIntl(<ResultBlock stdout="Hello world" truncated={false} />);
      expect(screen.getByText(/Hello world/)).toBeInTheDocument();
      expect(screen.queryByText(/Show \d+ more line/i)).not.toBeInTheDocument();
    });

    it("renders an empty stdout as an empty <pre> (no crash)", () => {
      const { container } = renderWithIntl(
        <ResultBlock stdout="" truncated={false} />,
      );
      const pre = container.querySelector('[data-slot="result-block-stdout"]');
      expect(pre).toBeInTheDocument();
    });
  });

  describe("stdout truncation (D-F4-3)", () => {
    const longStdout = Array.from(
      { length: 30 },
      (_, i) => `line ${i + 1}`,
    ).join("\n");

    function stdoutText(container: HTMLElement): string {
      return (
        container.querySelector('[data-slot="result-block-stdout"]')
          ?.textContent ?? ""
      );
    }

    it("renders only the first 12 lines + show-more toggle by default", () => {
      const { container } = renderWithIntl(
        <ResultBlock stdout={longStdout} truncated={false} />,
      );
      const text = stdoutText(container);
      expect(text).toContain("line 1\n");
      expect(text).toContain("line 12");
      // line 13 is NOT in the visible <pre> until expanded.
      expect(text).not.toContain("line 13");
      // show-full affordance reports the hidden line count via the plural key.
      expect(screen.getByText(/Show 18 more lines/)).toBeInTheDocument();
    });

    it("clicking show-more expands the full stdout + flips label", () => {
      const { container } = renderWithIntl(
        <ResultBlock stdout={longStdout} truncated={false} />,
      );
      fireEvent.click(screen.getByText(/Show 18 more lines/));
      // After expansion, line 30 is in the visible <pre>.
      expect(stdoutText(container)).toContain("line 30");
      // Label flips to "Show less".
      expect(screen.getByText("Show less")).toBeInTheDocument();
    });

    it("clicking show-less collapses again", () => {
      const { container } = renderWithIntl(
        <ResultBlock stdout={longStdout} truncated={false} />,
      );
      fireEvent.click(screen.getByText(/Show 18 more lines/));
      fireEvent.click(screen.getByText("Show less"));
      expect(stdoutText(container)).not.toContain("line 13");
    });

    it("singular plural key fires for exactly one hidden line (13 total)", () => {
      const exactly13 = Array.from({ length: 13 }, (_, i) => `l${i}`).join(
        "\n",
      );
      renderWithIntl(<ResultBlock stdout={exactly13} truncated={false} />);
      // 13 - 12 = 1 hidden line → singular form "1 more line".
      expect(screen.getByText("Show 1 more line")).toBeInTheDocument();
    });
  });

  describe("upstream truncation indicator (producer-reported)", () => {
    it("renders the upstreamTruncated banner when truncated=true", () => {
      renderWithIntl(<ResultBlock stdout="short" truncated={true} />);
      expect(
        screen.getByText("Output was truncated upstream."),
      ).toBeInTheDocument();
    });

    it("does not render the banner when truncated=false", () => {
      renderWithIntl(<ResultBlock stdout="short" truncated={false} />);
      expect(
        screen.queryByText("Output was truncated upstream."),
      ).not.toBeInTheDocument();
    });

    it("upstream truncation + line truncation can both fire (independent)", () => {
      const longStdout = Array.from({ length: 30 }, (_, i) => `l${i}`).join(
        "\n",
      );
      renderWithIntl(<ResultBlock stdout={longStdout} truncated={true} />);
      expect(
        screen.getByText("Output was truncated upstream."),
      ).toBeInTheDocument();
      expect(screen.getByText(/Show 18 more lines/)).toBeInTheDocument();
    });
  });

  describe("collapsible code (D-F4-1 instrument-transparency)", () => {
    it("renders no code section when code prop is absent", () => {
      const { container } = renderWithIntl(<ResultBlock stdout="ok" />);
      expect(
        container.querySelector('[data-slot="result-block-code-section"]'),
      ).toBeNull();
    });

    it("renders no code section when code is empty string (defensive)", () => {
      const { container } = renderWithIntl(<ResultBlock stdout="ok" code="" />);
      expect(
        container.querySelector('[data-slot="result-block-code-section"]'),
      ).toBeNull();
    });

    it("renders the show-code toggle, default-COLLAPSED (D-F4-1)", () => {
      renderWithIntl(
        <ResultBlock stdout="ok" code="print('hi')" language="python" />,
      );
      // Toggle button labelled "Show code"; the highlighted code is NOT in
      // the DOM (default-collapsed per D-F4-1).
      expect(
        screen.getByRole("button", { name: "Show code" }),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("highlighted-code")).not.toBeInTheDocument();
    });

    it("clicking show-code expands the lazy Shiki chunk", async () => {
      renderWithIntl(
        <ResultBlock stdout="ok" code="print('hi')" language="python" />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Show code" }));
      // The mocked default export of ./highlighted-code renders synchronously.
      const code = await screen.findByTestId("highlighted-code");
      expect(code).toHaveAttribute("data-lang", "python");
      expect(code).toHaveTextContent("print('hi')");
      // Toggle label flips to "Hide code".
      expect(
        screen.getByRole("button", { name: "Hide code" }),
      ).toBeInTheDocument();
    });

    it("aria-expanded reflects the toggle state for screen readers", () => {
      renderWithIntl(<ResultBlock stdout="ok" code="x" />);
      const toggle = screen.getByRole("button", { name: "Show code" });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
    });

    it("defaults language to python when omitted (D-12-1)", async () => {
      renderWithIntl(<ResultBlock stdout="ok" code="x = 1" />);
      fireEvent.click(screen.getByRole("button", { name: "Show code" }));
      const code = await screen.findByTestId("highlighted-code");
      expect(code).toHaveAttribute("data-lang", "python");
    });

    it("propagates the language prop through to the lazy chunk", async () => {
      renderWithIntl(
        <ResultBlock stdout="ok" code="echo hi" language="bash" />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Show code" }));
      const code = await screen.findByTestId("highlighted-code");
      expect(code).toHaveAttribute("data-lang", "bash");
    });
  });

  describe("structural", () => {
    it("renders inside a result-block container", () => {
      const { container } = renderWithIntl(<ResultBlock stdout="ok" />);
      expect(
        container.querySelector('[data-slot="result-block"]'),
      ).toBeInTheDocument();
    });

    it("stdout has monospace + 1.5 line-height per D-F4-3", () => {
      const { container } = renderWithIntl(<ResultBlock stdout="ok" />);
      const pre = container.querySelector('[data-slot="result-block-stdout"]');
      expect(pre?.className).toContain("font-mono");
      expect(pre?.className).toContain("leading-[1.5]");
    });
  });
});
