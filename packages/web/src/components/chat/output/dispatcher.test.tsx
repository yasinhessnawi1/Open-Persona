import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import en from "@/i18n/messages/en.json";
import type { OutputContent } from "@/lib/api/output-content";

import { OutputDispatcher, OutputList } from "./dispatcher";

// Mock byte-loading hook + Clerk + Shiki lazy chunk so we can assert on
// the dispatcher's routing decisions without exercising downstream IO.
vi.mock("@/lib/hooks/use-authed-image-blob-url", () => ({
  useAuthedImageBlobUrl: () => ({
    src: "blob:fake",
    loading: false,
    error: null,
  }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: vi.fn().mockResolvedValue("fake-jwt") }),
}));

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

describe("<OutputDispatcher> (T09)", () => {
  describe("six-variant routing (D-F4-X-presentation-hint-source)", () => {
    it("kind=inline-image → <InlineVisual intent=image>", () => {
      const output: OutputContent = {
        kind: "inline-image",
        workspace_path: "uploads/abc.png",
        media_type: "image/png",
        alt: "a cat",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="inline-visual"]'),
      ).toHaveAttribute("data-intent", "image");
      expect(screen.getByAltText("a cat")).toBeInTheDocument();
    });

    it("kind=inline-chart → <InlineVisual intent=chart>", () => {
      const output: OutputContent = {
        kind: "inline-chart",
        workspace_path: "charts/q1.png",
        media_type: "image/png",
        prose_context: "Q1 revenue rose 12%.",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="inline-visual"]'),
      ).toHaveAttribute("data-intent", "chart");
      // Chart prose_context surfaces in the figcaption.
      expect(screen.getByText("Q1 revenue rose 12%.")).toBeInTheDocument();
    });

    it("kind=inline-chart derives alt from filename when wire lacks it", () => {
      const output: OutputContent = {
        kind: "inline-chart",
        workspace_path: "charts/q1-revenue.png",
        media_type: "image/png",
      };
      renderWithIntl(<OutputDispatcher personaId="p1" output={output} />);
      // alt defaults to the basename of workspace_path.
      expect(screen.getByAltText("q1-revenue.png")).toBeInTheDocument();
    });

    it("kind=download-doc → <DownloadChip>", () => {
      const output: OutputContent = {
        kind: "download-doc",
        workspace_path: "uploads/report.pdf",
        media_type: "application/pdf",
        name: "report.pdf",
        size_bytes: 12345,
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="download-chip"]'),
      ).toBeInTheDocument();
      expect(screen.getByText("report.pdf")).toBeInTheDocument();
    });

    it("kind=result-block → <ResultBlock>", () => {
      const output: OutputContent = {
        kind: "result-block",
        stdout: "Hello world",
        truncated: false,
        language: "python",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="result-block"]'),
      ).toBeInTheDocument();
      expect(screen.getByText(/Hello world/)).toBeInTheDocument();
    });

    it("kind=working → <WorkingState>", () => {
      const output: OutputContent = {
        kind: "working",
        operation: "code_exec",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="working-state"]'),
      ).toHaveAttribute("data-operation", "code_exec");
      expect(screen.getByText("Running code…")).toBeInTheDocument();
    });

    it("kind=failure → <FailureCard> with operation + message", () => {
      const output: OutputContent = {
        kind: "failure",
        operation: "code_execution",
        error_message: "outcome=timeout exit_code=124",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      const card = container.querySelector('[data-slot="output-failure"]');
      expect(card).toHaveAttribute("data-operation", "code_execution");
      expect(card).toHaveAttribute("role", "alert");
      expect(screen.getByText("code_execution")).toBeInTheDocument();
      expect(
        screen.getByText("outcome=timeout exit_code=124"),
      ).toBeInTheDocument();
    });
  });

  describe("path-traversal defence-in-depth", () => {
    it("inline-image with ../ in path → renders failure card, not the visual", () => {
      const output: OutputContent = {
        kind: "inline-image",
        workspace_path: "uploads/../../etc/passwd",
        media_type: "image/png",
        alt: "x",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="output-failure"]'),
      ).toBeInTheDocument();
      expect(container.querySelector('[data-slot="inline-visual"]')).toBeNull();
    });

    it("inline-chart with ../ → failure card", () => {
      const output: OutputContent = {
        kind: "inline-chart",
        workspace_path: "../something.png",
        media_type: "image/png",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="output-failure"]'),
      ).toBeInTheDocument();
    });

    it("download-doc with ../ → failure card (no download chip)", () => {
      const output: OutputContent = {
        kind: "download-doc",
        workspace_path: "uploads/../../secret.docx",
        media_type:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        name: "secret.docx",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="output-failure"]'),
      ).toBeInTheDocument();
      expect(container.querySelector('[data-slot="download-chip"]')).toBeNull();
    });

    it("clean path (no ..) passes through", () => {
      const output: OutputContent = {
        kind: "inline-image",
        workspace_path: "uploads/clean.png",
        media_type: "image/png",
        alt: "ok",
      };
      const { container } = renderWithIntl(
        <OutputDispatcher personaId="p1" output={output} />,
      );
      expect(
        container.querySelector('[data-slot="inline-visual"]'),
      ).toBeInTheDocument();
    });
  });

  describe("onViewLarger forwarding", () => {
    it("passes a closure carrying workspace_path through to <InlineVisual>", () => {
      const onViewLarger = vi.fn();
      const output: OutputContent = {
        kind: "inline-chart",
        workspace_path: "charts/abc.png",
        media_type: "image/png",
      };
      renderWithIntl(
        <OutputDispatcher
          personaId="p1"
          output={output}
          onViewLarger={onViewLarger}
        />,
      );
      // The button exists; clicking it invokes the closure with the path.
      const btn = screen.getByRole("button");
      btn.click();
      expect(onViewLarger).toHaveBeenCalledWith("charts/abc.png");
    });

    it("absent onViewLarger → no button (non-interactive)", () => {
      const output: OutputContent = {
        kind: "inline-image",
        workspace_path: "uploads/x.png",
        media_type: "image/png",
        alt: "x",
      };
      renderWithIntl(<OutputDispatcher personaId="p1" output={output} />);
      expect(screen.queryByRole("button")).toBeNull();
    });
  });
});

describe("<OutputList> (T09 sibling)", () => {
  it("renders each OutputContent through OutputDispatcher in order", () => {
    const outputs: OutputContent[] = [
      {
        kind: "inline-chart",
        workspace_path: "charts/q1.png",
        media_type: "image/png",
      },
      {
        kind: "download-doc",
        workspace_path: "uploads/report.pdf",
        media_type: "application/pdf",
        name: "report.pdf",
      },
      {
        kind: "result-block",
        stdout: "ok",
        truncated: false,
      },
    ];
    const { container } = renderWithIntl(
      <OutputList personaId="p1" outputs={outputs} />,
    );
    const list = container.querySelector('[data-slot="output-list"]');
    expect(list).toBeInTheDocument();
    expect(
      container.querySelector('[data-slot="inline-visual"]'),
    ).toBeInTheDocument();
    expect(
      container.querySelector('[data-slot="download-chip"]'),
    ).toBeInTheDocument();
    expect(
      container.querySelector('[data-slot="result-block"]'),
    ).toBeInTheDocument();
  });

  it("renders nothing for an empty outputs array", () => {
    const { container } = renderWithIntl(
      <OutputList personaId="p1" outputs={[]} />,
    );
    expect(container.querySelector('[data-slot="output-list"]')).toBeNull();
  });

  it("forwards onViewLarger to every inline-image / inline-chart", () => {
    const onViewLarger = vi.fn();
    const outputs: OutputContent[] = [
      {
        kind: "inline-chart",
        workspace_path: "charts/a.png",
        media_type: "image/png",
      },
      {
        kind: "inline-image",
        workspace_path: "uploads/b.png",
        media_type: "image/png",
        alt: "b",
      },
    ];
    renderWithIntl(
      <OutputList
        personaId="p1"
        outputs={outputs}
        onViewLarger={onViewLarger}
      />,
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    buttons[0].click();
    buttons[1].click();
    expect(onViewLarger).toHaveBeenNthCalledWith(1, "charts/a.png");
    expect(onViewLarger).toHaveBeenNthCalledWith(2, "uploads/b.png");
  });

  it("preserves order across mixed variants", () => {
    const outputs: OutputContent[] = [
      { kind: "working", operation: "code_exec" },
      { kind: "result-block", stdout: "done", truncated: false },
      {
        kind: "inline-chart",
        workspace_path: "charts/x.png",
        media_type: "image/png",
      },
    ];
    const { container } = renderWithIntl(
      <OutputList personaId="p1" outputs={outputs} />,
    );
    const children = container.querySelector(
      '[data-slot="output-list"]',
    )?.children;
    expect(children?.[0].getAttribute("data-slot")).toBe("working-state");
    expect(children?.[1].getAttribute("data-slot")).toBe("result-block");
    expect(children?.[2].getAttribute("data-slot")).toBe("inline-visual");
  });
});
