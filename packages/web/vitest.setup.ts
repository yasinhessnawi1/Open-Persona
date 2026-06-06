import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// React Testing Library renders to document.body but doesn't auto-clean
// between tests (its built-in cleanup hook is opt-in for non-Jest runners).
// Without this, `screen.getByLabelText("Astrid")` collides across tests
// because the previous render is still in the DOM. Spec F1 T06 added the
// first test that uses screen-by-label queries, which surfaced the gap.
afterEach(cleanup);

// F2 T23: jsdom doesn't ship window.matchMedia. next-themes (D-09-10) and
// sonner (D-F2-10) both query it for prefers-color-scheme / prefers-reduced-
// motion detection. Stub a no-op MediaQueryList so consumers can call
// .matches / addEventListener without crashing.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(), // deprecated but some libs still call it
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}
