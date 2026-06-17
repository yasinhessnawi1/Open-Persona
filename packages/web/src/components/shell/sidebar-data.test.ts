import { describe, expect, it } from "vitest";
import {
  rankPersonasByRecency,
  resolveConversations,
  type SidebarConversationInput,
  type SidebarPersona,
} from "./sidebar-data";

const persona = (id: string, created_at: string): SidebarPersona => ({
  id,
  name: id.toUpperCase(),
  role: "role",
  created_at,
  avatar_url: null,
});

const convo = (
  id: string,
  persona_id: string,
  updated_at: string,
): SidebarConversationInput => ({
  id,
  persona_id,
  title: `t-${id}`,
  updated_at,
});

describe("rankPersonasByRecency", () => {
  it("orders used personas by conversation order, unused by created_at desc", () => {
    const personas = [
      persona("a", "2026-01-01"),
      persona("b", "2026-03-01"),
      persona("c", "2026-02-01"),
    ];
    // updated_at DESC already: c used most recently, then a.
    const conversations = [
      convo("1", "c", "2026-06-10"),
      convo("2", "a", "2026-06-09"),
      convo("3", "c", "2026-06-08"), // dup → ignored
    ];
    const ranked = rankPersonasByRecency(personas, conversations).map(
      (p) => p.id,
    );
    // used: c, a (first-seen order); unused: b (newest created).
    expect(ranked).toEqual(["c", "a", "b"]);
  });

  it("falls back entirely to created_at desc when no conversations exist", () => {
    const personas = [persona("a", "2026-01-01"), persona("b", "2026-03-01")];
    expect(rankPersonasByRecency(personas, []).map((p) => p.id)).toEqual([
      "b",
      "a",
    ]);
  });

  it("ignores conversations whose persona is missing", () => {
    const personas = [persona("a", "2026-01-01")];
    const ranked = rankPersonasByRecency(personas, [
      convo("1", "ghost", "2026-06-10"),
    ]);
    expect(ranked.map((p) => p.id)).toEqual(["a"]);
  });
});

describe("resolveConversations", () => {
  it("joins each conversation to its persona, preserving order", () => {
    const personas = [persona("a", "2026-01-01")];
    const rows = resolveConversations(
      [convo("1", "a", "2026-06-10"), convo("2", "ghost", "2026-06-09")],
      personas,
    );
    expect(rows).toHaveLength(2);
    expect(rows[0].persona?.id).toBe("a");
    expect(rows[1].persona).toBeNull();
    expect(rows.map((r) => r.id)).toEqual(["1", "2"]);
  });
});
