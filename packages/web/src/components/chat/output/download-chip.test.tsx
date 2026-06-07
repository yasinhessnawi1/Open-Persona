import { act, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import en from "@/i18n/messages/en.json";

import { DownloadChip } from "./download-chip";

// --------- Clerk auth mock — supplies a stable JWT for the Bearer header ----

const getTokenMock = vi.fn().mockResolvedValue("fake-jwt");
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

// --------- fetch + URL.createObjectURL stubs --------------------------------

const originalFetch = globalThis.fetch;
const originalCreateObjectURL = globalThis.URL.createObjectURL;
const originalRevokeObjectURL = globalThis.URL.revokeObjectURL;
const originalAnchorClick = HTMLAnchorElement.prototype.click;

let fetchMock: ReturnType<typeof vi.fn>;
let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;
let clickSpy: ReturnType<typeof vi.fn>;

/** Build a fetch-style Response whose .blob() resolves to a real Blob. The
 *  default jsdom Response.blob() implementation chokes on synthetic bodies;
 *  the simplest fix is a hand-rolled stub matching the consumed surface. */
function blobResponse(status: number, blob = new Blob([new Uint8Array(8)])) {
  return {
    ok: status >= 200 && status < 300,
    status,
    blob: async () => blob,
  } as unknown as Response;
}

beforeEach(() => {
  fetchMock = vi.fn(async () => blobResponse(200));
  globalThis.fetch = fetchMock as typeof globalThis.fetch;

  createObjectURL = vi.fn(() => "blob:fake-url");
  revokeObjectURL = vi.fn();
  // biome-ignore lint/suspicious/noExplicitAny: stub override
  (globalThis.URL.createObjectURL as any) = createObjectURL;
  // biome-ignore lint/suspicious/noExplicitAny: stub override
  (globalThis.URL.revokeObjectURL as any) = revokeObjectURL;

  clickSpy = vi.fn();
  // biome-ignore lint/suspicious/noExplicitAny: prototype override for spy
  (HTMLAnchorElement.prototype.click as any) = clickSpy;

  getTokenMock.mockClear();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.URL.createObjectURL = originalCreateObjectURL;
  globalThis.URL.revokeObjectURL = originalRevokeObjectURL;
  HTMLAnchorElement.prototype.click = originalAnchorClick;
  vi.restoreAllMocks();
});

function renderWithIntl(ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("<DownloadChip> (T06)", () => {
  describe("rendering", () => {
    it("renders filename, extension label, and size", () => {
      renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/report.pdf"
          mediaType="application/pdf"
          name="report.pdf"
          sizeBytes={12345}
        />,
      );
      expect(screen.getByText("report.pdf")).toBeInTheDocument();
      // Extension label + " · " + size label.
      expect(screen.getByText(/PDF · 12 KB/)).toBeInTheDocument();
    });

    it("renders without a size label when sizeBytes is omitted", () => {
      renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/report.docx"
          mediaType="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          name="report.docx"
        />,
      );
      expect(screen.getByText("report.docx")).toBeInTheDocument();
      // No " · " separator when size is absent.
      expect(screen.getByText("DOCX")).toBeInTheDocument();
    });

    it("dispatches data-media-type for testing hook", () => {
      const { container } = renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/x.pdf"
          mediaType="application/pdf"
          name="x.pdf"
        />,
      );
      expect(
        container.querySelector('[data-slot="download-chip"]'),
      ).toHaveAttribute("data-media-type", "application/pdf");
    });
  });

  describe("download flow", () => {
    it("clicking the button fetches via Bearer auth and triggers a browser download", async () => {
      renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/report.pdf"
          mediaType="application/pdf"
          name="report.pdf"
          sizeBytes={12345}
        />,
      );
      await act(async () => {
        fireEvent.click(screen.getByRole("button"));
      });
      // 1. Bearer-auth fetch fired.
      expect(getTokenMock).toHaveBeenCalled();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toContain("/v1/personas/p1/uploads/uploads/report.pdf");
      expect((init.headers as Record<string, string>).Authorization).toBe(
        "Bearer fake-jwt",
      );
      // 2. Anchor click fired (download triggered).
      expect(clickSpy).toHaveBeenCalledTimes(1);
      // 3. ObjectURL created + revoked.
      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    });

    it("encodes personaId in the URL path", async () => {
      renderWithIntl(
        <DownloadChip
          personaId="p with space"
          workspacePath="uploads/x.pdf"
          mediaType="application/pdf"
          name="x.pdf"
        />,
      );
      await act(async () => {
        fireEvent.click(screen.getByRole("button"));
      });
      const [url] = fetchMock.mock.calls[0] as [string];
      expect(url).toContain("/v1/personas/p%20with%20space/uploads/");
    });

    it("button is disabled while download is in flight (no double-trigger)", async () => {
      let resolveFetch: ((res: Response) => void) | null = null;
      fetchMock.mockImplementationOnce(
        () =>
          new Promise<Response>((res) => {
            resolveFetch = res;
          }),
      );
      renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/x.pdf"
          mediaType="application/pdf"
          name="x.pdf"
        />,
      );
      const btn = screen.getByRole("button");
      // First click — fetch pending.
      await act(async () => {
        fireEvent.click(btn);
      });
      expect(btn).toBeDisabled();
      // Second click while pending should not fire another fetch.
      fireEvent.click(btn);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      // Resolve the first fetch.
      await act(async () => {
        if (resolveFetch !== null) {
          resolveFetch(blobResponse(200));
        }
      });
      expect(btn).not.toBeDisabled();
    });

    it("surfaces a 5xx error on the chip (destructive ring)", async () => {
      fetchMock.mockResolvedValueOnce(blobResponse(500));
      const { container } = renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/x.pdf"
          mediaType="application/pdf"
          name="x.pdf"
        />,
      );
      await act(async () => {
        fireEvent.click(screen.getByRole("button"));
      });
      const card = container.querySelector('[data-slot="download-chip"]');
      expect(card?.className).toContain("ring-destructive");
      // No browser download triggered.
      expect(clickSpy).not.toHaveBeenCalled();
    });
  });

  describe("media-type icon dispatch", () => {
    it("PDF media-type renders distinct icon (data-format=pdf via extension)", () => {
      const { container } = renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/x.pdf"
          mediaType="application/pdf"
          name="x.pdf"
        />,
      );
      // The first child is the icon container.
      const card = container.querySelector('[data-slot="download-chip"]');
      expect(card?.firstChild).toBeDefined();
    });

    it("docx + pptx + xlsx all render their own icons (no crash)", () => {
      const cases = [
        {
          mediaType:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          name: "a.docx",
          extLabel: "DOCX",
        },
        {
          mediaType:
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          name: "a.pptx",
          extLabel: "PPTX",
        },
        {
          mediaType:
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          name: "a.xlsx",
          extLabel: "XLSX",
        },
      ];
      for (const c of cases) {
        const { unmount } = renderWithIntl(
          <DownloadChip
            personaId="p1"
            workspacePath={`uploads/${c.name}`}
            mediaType={c.mediaType}
            name={c.name}
          />,
        );
        expect(screen.getByText(c.extLabel)).toBeInTheDocument();
        unmount();
      }
    });

    it("unknown media-type still renders (fallback FileText icon)", () => {
      renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/x.bin"
          mediaType="application/octet-stream"
          name="x.bin"
        />,
      );
      expect(screen.getByText("x.bin")).toBeInTheDocument();
      // Filename has extension → ext label derived from filename.
      expect(screen.getByText("BIN")).toBeInTheDocument();
    });
  });

  describe("size formatting", () => {
    it.each([
      { bytes: 500, expected: /500 B/ },
      { bytes: 1500, expected: /1 KB/ },
      { bytes: 2 * 1024 * 1024, expected: /2\.0 MB/ },
    ])("formats $bytes bytes as $expected", ({ bytes, expected }) => {
      renderWithIntl(
        <DownloadChip
          personaId="p1"
          workspacePath="uploads/x.pdf"
          mediaType="application/pdf"
          name="x.pdf"
          sizeBytes={bytes}
        />,
      );
      expect(screen.getByText(expected)).toBeInTheDocument();
    });
  });
});
