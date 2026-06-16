/**
 * Spec 30 T03 (D-30-1) — ToolCallCard source-badge tests.
 *
 * Verifies:
 *   1. Each kind renders its badge label (built-in / skill / MCP).
 *   2. An MCP call names its server (`MCP · <server>`).
 *   3. Absent kind (pre-spec-30 frames) renders NO badge — back-compat.
 *   4. The data-kind attribute carries the kind for downstream querying.
 */

import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import { ToolCallCard, type ToolEntry } from "./tool-call-card";

const messages = {
  chat: {
    toolUsing: "Using {tool}",
    toolError: "error",
    kindBuiltin: "Built-in",
    kindSkill: "Skill",
    kindMcp: "MCP",
    kindMcpServer: "MCP · {server}",
  },
};

function renderCard(entry: ToolEntry) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ToolCallCard entry={entry} />
    </NextIntlClientProvider>,
  );
}

const base: ToolEntry = { toolName: "web_search", pending: false };

describe("ToolCallCard source badge", () => {
  it("badges a built-in tool", () => {
    const { getByText, container } = renderCard({ ...base, kind: "builtin" });
    expect(getByText("Built-in")).not.toBeNull();
    expect(
      container
        .querySelector('[data-slot="tool-kind-badge"]')
        ?.getAttribute("data-kind"),
    ).toBe("builtin");
  });

  it("badges a skill activation", () => {
    const { getByText } = renderCard({
      ...base,
      toolName: "use_skill",
      kind: "skill",
    });
    expect(getByText("Skill")).not.toBeNull();
  });

  it("names the MCP server for a builtin MCP call", () => {
    const { getByText } = renderCard({
      ...base,
      toolName: "mcp:time:get_current_time",
      kind: "mcp:builtin",
    });
    expect(getByText("MCP · time")).not.toBeNull();
  });

  it("names the MCP server for an optional/BYO MCP call", () => {
    const { getByText, container } = renderCard({
      ...base,
      toolName: "mcp:my_server:do_thing",
      kind: "mcp:optional",
    });
    expect(getByText("MCP · my_server")).not.toBeNull();
    expect(
      container
        .querySelector('[data-slot="tool-kind-badge"]')
        ?.getAttribute("data-kind"),
    ).toBe("mcp:optional");
  });

  it("renders NO badge when kind is absent (back-compat)", () => {
    const { container } = renderCard(base);
    expect(container.querySelector('[data-slot="tool-kind-badge"]')).toBeNull();
  });

  it("uses motion-safe pulse (reduced-motion compliant) while pending", () => {
    const { container } = renderCard({
      ...base,
      pending: true,
      kind: "builtin",
    });
    const icon = container.querySelector("svg");
    expect(icon?.getAttribute("class")).toContain("motion-safe:animate-pulse");
  });
});
