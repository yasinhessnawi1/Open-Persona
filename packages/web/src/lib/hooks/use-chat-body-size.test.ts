/**
 * F3 T22 — STRUCTURAL STORE-BY-REFERENCE REGRESSION GUARD.
 *
 * This test is the API-call-layer mirror of Spec 13 T13's DB-layer guard
 * (`test_messages_bounded_by_references.py`). It enforces the load-bearing
 * Concern #4 invariant: a chat-message body MUST carry only workspace
 * references for image attachments, NEVER inlined base64 bytes.
 *
 * **If this test fails, production is broken.** The Spec 13 router will
 * still reject base64-inlined payloads at the server, but the wire
 * inefficiency + the cross-spec discipline violation are themselves the
 * bug. The Spec 11 soak test (T03 `max_prompt_tokens=20553`) was measured
 * against text-only conversations; image-bearing conversations stay
 * within that bound ONLY because images travel by reference.
 *
 * **Production-safety invariant:** the body for any message bearing N
 * attached images (each up to the 20 MB upload cap) MUST stay under
 * `2 KB` regardless of image bytes. The body grows with reference count
 * (each ImageRef is ~120 bytes of JSON), not with image size.
 *
 * If anyone in the future "optimises" the upload flow by inlining the
 * image bytes — or a code-gen tool regresses the request shape — this
 * test fails loud immediately, before the regression hits prod.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ImageRef } from "./use-chat";
import { useChat } from "./use-chat";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: () => Promise.resolve("test-jwt") }),
}));

interface Capture {
  body: string;
}

function installCapture(): { restore: () => void; captures: Capture[] } {
  const captures: Capture[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(async (url, init) => {
    const urlStr = typeof url === "string" ? url : url.toString();
    if (init?.method === "POST" && urlStr.includes("/messages")) {
      captures.push({
        body: typeof init.body === "string" ? init.body : "",
      });
    }
    return new Response(new ReadableStream({ start: (c) => c.close() }), {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }) as unknown as typeof fetch;
  return {
    captures,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

describe("F3 T22 — chat-message body size regression guard (production-safety)", () => {
  let restore: () => void;
  afterEach(() => restore?.());

  it("STRUCTURAL: 4 image refs (each pointing to a 1 MB image) → message body < 2 KB", async () => {
    // The realistic worst case: a user attaches the maximum 4 images
    // (per-message cap), each 1 MB on disk. The on-wire body MUST
    // contain only the references, NEVER the bytes.
    const { captures, restore: r } = installCapture();
    restore = r;

    // Construct realistic workspace_path strings — long-ish but representative
    // of `uploads/<uuid>.<ext>` format the API actually returns.
    const refs: ImageRef[] = [
      {
        workspace_path: "uploads/9f8e7d6c5b4a3210-2026-06-06-photo-1.png",
        media_type: "image/png",
      },
      {
        workspace_path: "uploads/9f8e7d6c5b4a3210-2026-06-06-photo-2.jpeg",
        media_type: "image/jpeg",
      },
      {
        workspace_path: "uploads/9f8e7d6c5b4a3210-2026-06-06-photo-3.webp",
        media_type: "image/webp",
      },
      {
        workspace_path: "uploads/9f8e7d6c5b4a3210-2026-06-06-photo-4.gif",
        media_type: "image/gif",
      },
    ];

    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send(
      "Please analyse these four images in detail.",
      refs,
    );
    await waitFor(() => expect(captures.length).toBeGreaterThan(0));

    // **The load-bearing assertion.** If anyone inlines base64 image
    // bytes into the request body, this length blows past 2 KB.
    expect(captures[0].body.length).toBeLessThan(2 * 1024);

    // Defence-in-depth: no base64 / data-URI marker can appear in the body.
    expect(captures[0].body).not.toContain("base64");
    expect(captures[0].body).not.toContain("data:image");

    // Positive shape assertion: the body DOES carry the four workspace
    // references in order (no silent drop).
    const parsed = JSON.parse(captures[0].body);
    expect(parsed.images).toHaveLength(4);
    expect(parsed.images[0].workspace_path).toContain("photo-1");
    expect(parsed.images[3].workspace_path).toContain("photo-4");
  });

  it("STRUCTURAL: text-only send body is <500 B (no images, no shape drift)", async () => {
    const { captures, restore: r } = installCapture();
    restore = r;
    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send("hello");
    await waitFor(() => expect(captures.length).toBeGreaterThan(0));

    expect(captures[0].body.length).toBeLessThan(500);
    expect(captures[0].body).not.toContain("images");
  });

  it("STRUCTURAL: body size grows LINEARLY with reference count, not with image size", async () => {
    // The reference-count vs image-size dimension. If anyone replaces the
    // ref-passing with bytes-passing, body size becomes O(image bytes).
    // With refs, body size is O(reference count) — flat ~120 bytes per ref.
    const { captures, restore: r } = installCapture();
    restore = r;

    const oneRef: ImageRef[] = [
      { workspace_path: "uploads/x.png", media_type: "image/png" },
    ];
    const fourRefs: ImageRef[] = Array.from({ length: 4 }, () => oneRef[0]);

    const { result } = renderHook(() => useChat("conv_1", []));
    await result.current.send("first", oneRef);
    await waitFor(() => expect(captures.length).toBe(1));
    const oneRefSize = captures[0].body.length;

    await result.current.send("second", fourRefs);
    await waitFor(() => expect(captures.length).toBe(2));
    const fourRefSize = captures[1].body.length;

    // 4 refs should be roughly 4x the per-ref overhead larger than 1 ref —
    // well within a few hundred bytes total, not megabytes.
    expect(fourRefSize - oneRefSize).toBeLessThan(500);
    expect(fourRefSize).toBeLessThan(1024);
  });
});
