/**
 * Spec F2 T23 — Toast pattern tests.
 *
 * Verifies the sonner integration wrapper renders + the imperative API
 * is exposed correctly. Sonner's internal portal + animation behaviour is
 * exercised by the in-tree spike at /scratch/toasts (re-runnable for T34);
 * this unit suite covers the contract surface only.
 */

import { render } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { describe, expect, it } from "vitest";
import { ToastProvider, toast, useToast } from "./toast";

describe("ToastProvider", () => {
  it("renders without crashing inside a ThemeProvider", () => {
    expect(() =>
      render(
        <ThemeProvider attribute="class">
          <ToastProvider />
        </ThemeProvider>,
      ),
    ).not.toThrow();
  });

  it("renders without crashing without a ThemeProvider (theme defaults to 'system')", () => {
    expect(() => render(<ToastProvider />)).not.toThrow();
  });
});

describe("useToast / toast API", () => {
  it("re-exports sonner's imperative toast namespace", () => {
    expect(typeof toast).toBe("function");
    expect(typeof toast.success).toBe("function");
    expect(typeof toast.error).toBe("function");
    expect(typeof toast.info).toBe("function");
    expect(typeof toast.warning).toBe("function");
    expect(typeof toast.dismiss).toBe("function");
  });

  it("useToast() returns the same imperative surface", () => {
    function Probe() {
      const t = useToast();
      return (
        <div data-testid="probe">
          {`${typeof t}|${typeof t.success}|${typeof t.error}`}
        </div>
      );
    }
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("probe").textContent).toBe("function|function|function");
  });
});
