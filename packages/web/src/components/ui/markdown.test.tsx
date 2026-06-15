import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./markdown";

// Spec 28 follow-up — markdown image embeds. Workspace artifacts can't load via
// a plain <img> (Bearer-authed); the FileCard already shows them. So the
// Markdown renderer suppresses non-external image embeds (the model echoing
// `![Generated Image](uploads/<hash>.png)` must not leave a broken-image icon)
// while still rendering genuine external images.

describe("Markdown image handling", () => {
  it("suppresses a workspace-relative image embed (no broken <img>)", () => {
    const { container } = render(
      <Markdown>{"![Generated Image](uploads/abc123.png)"}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("suppresses an absolute API uploads path (also Bearer-only)", () => {
    const { container } = render(
      <Markdown>{"![x](/v1/personas/p1/uploads/uploads/abc.png)"}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders a genuine external https image", () => {
    const { container } = render(
      <Markdown>{"![cat](https://example.com/cat.png)"}</Markdown>,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe("https://example.com/cat.png");
  });

  it("still renders ordinary markdown around a suppressed image", () => {
    const { container, getByText } = render(
      <Markdown>{"# Title\n\n![g](uploads/x.png)\n\nbody text"}</Markdown>,
    );
    expect(getByText("Title")).toBeInTheDocument();
    expect(getByText("body text")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
