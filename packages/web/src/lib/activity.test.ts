import { describe, expect, it } from "vitest";
import {
  type ActivityView,
  reduceActivityEnd,
  reduceActivityStart,
} from "@/lib/activity";
import type { ActivityEndData, ActivityStartData } from "@/lib/sse-types";

const start = (id: string, name = "web_search"): ActivityStartData => ({
  activity_id: id,
  kind: "web",
  name,
  label: "Searching the web",
  args_summary: { query: "oslo" },
});

const end = (id: string, status = "ok"): ActivityEndData => ({
  activity_id: id,
  status,
  duration_ms: 12,
  is_error: status === "error",
});

describe("reduceActivityStart", () => {
  it("opens an entry in the running state", () => {
    const out = reduceActivityStart(undefined, start("a1"));
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ activityId: "a1", status: "running" });
  });

  it("is idempotent on replay — a duplicate activity_id does not double", () => {
    let list = reduceActivityStart(undefined, start("a1"));
    list = reduceActivityStart(list, start("a1"));
    expect(list).toHaveLength(1);
  });
});

describe("reduceActivityEnd", () => {
  it("resolves the matching entry's status + duration", () => {
    const opened = reduceActivityStart(undefined, start("a1"));
    const resolved = reduceActivityEnd(opened, end("a1", "ok"));
    expect(resolved[0]).toMatchObject({ status: "ok", durationMs: 12 });
  });

  it("carries awaiting_approval through (A3 gate)", () => {
    const opened = reduceActivityStart(undefined, start("a1"));
    const resolved = reduceActivityEnd(opened, end("a1", "awaiting_approval"));
    expect(resolved[0].status).toBe("awaiting_approval");
  });

  it("normalises an unknown status to ok (never leaves it spinning)", () => {
    const opened = reduceActivityStart(undefined, start("a1"));
    const resolved = reduceActivityEnd(
      opened,
      end("a1", "weird_future_status"),
    );
    expect(resolved[0].status).toBe("ok");
  });

  it("is a no-op when no start was seen", () => {
    const out = reduceActivityEnd(undefined, end("missing"));
    expect(out).toEqual([]);
  });

  it("is idempotent on replay — re-applying end keeps one resolved entry", () => {
    let list: ActivityView[] | undefined = reduceActivityStart(
      undefined,
      start("a1"),
    );
    list = reduceActivityEnd(list, end("a1", "ok"));
    list = reduceActivityEnd(list, end("a1", "ok"));
    expect(list).toHaveLength(1);
    expect(list[0].status).toBe("ok");
  });
});
