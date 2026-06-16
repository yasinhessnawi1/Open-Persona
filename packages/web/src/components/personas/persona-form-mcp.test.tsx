/**
 * Spec 30 T11 — the unified capability section's MCP servers + cap note.
 *
 * Uses the real `author` message namespace so every form key resolves; asserts
 * MCP chips render, toggling writes an `mcp:<name>` entry into the persona's
 * tools (composing with the existing tools/skills chips), and the combined-cap
 * note reflects the count.
 */

import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import messages from "@/i18n/messages/en.json";
import type { PersonaDoc } from "@/lib/persona-draft";
import { type McpCatalogEntry, PersonaForm } from "./persona-form";

// PersonaForm now renders the V6 VoiceSelector (useAuth + /v1/voices). Mock both
// so the MCP-focused tests render without a ClerkProvider or a network call.
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: async () => null }),
}));
vi.mock("@/lib/voice/voices", () => ({
  fetchVoices: async () => ({ provider: null, voices: [] }),
}));

const MCP: McpCatalogEntry[] = [
  {
    name: "time",
    description: "current time",
    provider: "mcp:builtin",
    defaultEnabled: true,
    requiredEnv: [],
  },
  {
    name: "github",
    description: "GitHub ops",
    provider: "mcp:optional",
    defaultEnabled: false,
    requiredEnv: ["GITHUB_TOKEN"],
  },
];

function renderForm(doc: PersonaDoc, onChange = vi.fn()) {
  const result = render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <PersonaForm
        doc={doc}
        onChange={onChange}
        tools={["web_search"]}
        skills={["web_research"]}
        mcpServers={MCP}
      />
    </NextIntlClientProvider>,
  );
  return { ...result, onChange };
}

describe("PersonaForm — MCP capability section (spec 30 T11)", () => {
  it("renders an MCP chip per catalog server", () => {
    const { container } = renderForm({});
    const chips = container.querySelectorAll('[data-slot="mcp-chip"]');
    expect(chips.length).toBe(2);
  });

  it("marks an enabled MCP server (mcp:<name> in tools) as on", () => {
    const { container } = renderForm({ tools: ["mcp:time"] });
    const time = container.querySelector(
      '[data-slot="mcp-chip"][data-on="true"]',
    );
    expect(time?.textContent).toContain("time");
  });

  it("toggling an MCP server writes mcp:<name> into tools (preserving other tools)", () => {
    const { container, onChange } = renderForm({ tools: ["web_search"] });
    const githubChip = Array.from(
      container.querySelectorAll<HTMLButtonElement>('[data-slot="mcp-chip"]'),
    ).find((b) => b.textContent?.includes("github"));
    githubChip?.click();
    expect(onChange).toHaveBeenCalledTimes(1);
    const nextDoc = onChange.mock.calls[0][0] as PersonaDoc;
    expect(nextDoc.tools).toEqual(["web_search", "mcp:github"]);
  });

  it("shows the combined capability count (tools incl. mcp + skills)", () => {
    const { container } = renderForm({
      tools: ["web_search", "mcp:time"],
      skills: ["web_research"],
    });
    const note = container.querySelector('[data-slot="capability-count"]');
    expect(note?.textContent).toContain("3"); // 2 tools + 1 skill
  });
});
