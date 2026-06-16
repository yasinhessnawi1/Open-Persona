/**
 * Spec 30 T11 — recommendation → persona-doc mutation mapping.
 *
 * A skill pick lands in the skills list; an MCP pick becomes an `mcp:<name>`
 * tools entry; anything else is a plain tool. All idempotent.
 */

import { describe, expect, it } from "vitest";
import {
  applyRecommendation,
  recommendationApplied,
  type ToolRecommendation,
} from "./suggest-capabilities";

const rec = (tool_name: string, provider: string): ToolRecommendation => ({
  tool_name,
  provider,
  rationale: "because",
  confidence: 0.9,
});

const empty = { tools: [] as string[], skills: [] as string[] };

describe("applyRecommendation", () => {
  it("adds a builtin tool to tools", () => {
    expect(applyRecommendation(rec("web_search", "builtin"), empty)).toEqual({
      tools: ["web_search"],
      skills: [],
    });
  });

  it("adds a skill to skills", () => {
    expect(applyRecommendation(rec("web_research", "skill"), empty)).toEqual({
      tools: [],
      skills: ["web_research"],
    });
  });

  it("adds an MCP server as an mcp:<name> tools entry", () => {
    expect(applyRecommendation(rec("weather", "mcp:optional"), empty)).toEqual({
      tools: ["mcp:weather"],
      skills: [],
    });
  });

  it("does not double-prefix an already mcp:-prefixed name", () => {
    expect(
      applyRecommendation(rec("mcp:time", "mcp:builtin"), empty).tools,
    ).toEqual(["mcp:time"]);
  });

  it("is idempotent (returns the same object reference when already present)", () => {
    const current = { tools: ["web_search"], skills: [] };
    expect(applyRecommendation(rec("web_search", "builtin"), current)).toBe(
      current,
    );
  });
});

describe("recommendationApplied", () => {
  it("is true when the capability is already selected", () => {
    expect(
      recommendationApplied(rec("mcp:weather", "mcp:optional"), {
        tools: ["mcp:weather"],
        skills: [],
      }),
    ).toBe(true);
  });

  it("is false when not yet selected", () => {
    expect(recommendationApplied(rec("calculator", "builtin"), empty)).toBe(
      false,
    );
  });
});
